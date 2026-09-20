"""Streamlit Dashboard for AI-Smart-Tred - Interactive Trading Dashboard with REAL MT5 Data."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

# Add the project root to the import path so package-relative imports remain valid.
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.python.logging_config import get_logger  # noqa: E402

logger = get_logger("dashboard")
_SETTINGS_WRITE_LOCK = threading.Lock()


class LiveOrderRejected(RuntimeError):
    """Fail-closed fallback for dashboard-only environments without a full execution stack."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Import project modules
try:
    from src.python.backtest import BacktestEngine
    from src.python.execution import (
        AutoTrader,
        LiveTradingLoop,
        ShadowTradingLoop,
        TradingConfig,
    )
    from src.python.execution.live_order_workflow import (
        LiveOrderConfig,
        LiveOrderRejected,
        LiveOrderWorkflow,
    )
    from src.python.ml import (
        MLConfig,
        MLInferenceService,
        ModelArtifactError,
    )
    from src.python.risk.break_even import BreakEvenConfig
    from src.python.strategies.mean_reversion import MeanReversionStrategy
    from src.python.strategies.smc import SMCStrategy
    from src.python.strategies.trend_following import TrendFollowingStrategy

    MODULES_AVAILABLE = True
except ImportError as e:
    MODULES_AVAILABLE = False
    st.warning(f"Some modules not available: {e}")

# Import dashboard data manager for REAL MT5 data
try:
    from src.python.dashboard.data_manager import get_dashboard_data
    from src.python.dashboard.mt5_connector import get_mt5_instance
    from src.python.mt5_account.mt5_market_watch import (
        DEFAULT_HISTORY_COUNT,
        sync_market_data_from_connector,
    )

    DASHBOARD_MODULES_AVAILABLE = True
except ImportError as e:
    DASHBOARD_MODULES_AVAILABLE = False
    st.warning(f"Dashboard modules not available: {e}")

mt5_enabled = os.getenv("MT5_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
dashboard_direct_mt5 = os.getenv("MT5_DASHBOARD_DIRECT", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# Keep dashboard resources alive across Streamlit reruns. Recreating the MT5
# connector on each rerun can repeat terminal initialization and block the UI.
@st.cache_resource
def _get_dashboard_data_manager():
    """Create the dashboard data manager once per Streamlit process."""

    return get_dashboard_data() if DASHBOARD_MODULES_AVAILABLE else None


@st.cache_resource
def _get_mt5_connector():
    """Create the optional MT5 connector without blocking dashboard startup."""

    if not DASHBOARD_MODULES_AVAILABLE or not mt5_enabled:
        return None
    # MT5.initialize() can block at the native IPC boundary despite its
    # timeout argument. Connection is therefore initiated only by the
    # explicit connection control in the dashboard.
    return get_mt5_instance()


data_manager = _get_dashboard_data_manager()
mt5_connector = _get_mt5_connector()


_market_watch_process: subprocess.Popen[bytes] | None = None
_market_watch_log_handle = None

MODEL_ARTIFACT_SUFFIXES = {".pkl", ".json", ".csv"}
# Overview data is refreshed by Streamlit interaction or the browser reload.
# A no-op one-second fragment rerun made every page render compete with MT5 I/O.
OVERVIEW_REFRESH_INTERVAL_SECONDS: int | None = None
# MT5's Python API is not safe to query concurrently with a page rerun.
# Live values refresh on navigation/manual browser refresh instead of spawning
# a background fragment rerun that can race with sidebar navigation.
LIVE_MONITOR_REFRESH_INTERVAL_SECONDS: int | None = None


def _dashboard_fragment(function):
    """Create a fragment with optional polling disabled for safe MT5 navigation."""
    if LIVE_MONITOR_REFRESH_INTERVAL_SECONDS is None:
        return st.fragment(function)
    return st.fragment(run_every=f"{LIVE_MONITOR_REFRESH_INTERVAL_SECONDS}s")(function)


def _list_model_artifacts(models_dir: Path) -> list[Path]:
    """Return only known training artifacts from the dedicated models folder."""

    if not models_dir.is_dir():
        return []
    artifacts = []
    for path in models_dir.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in MODEL_ARTIFACT_SUFFIXES:
            continue
        if (
            path.suffix.lower() == ".pkl"
            or path.name.startswith("training_summary_")
            or "feature_importance" in path.name
        ):
            artifacts.append(path)
    return sorted(artifacts, key=lambda path: path.stat().st_mtime, reverse=True)


def _delete_model_artifacts(models_dir: Path, selected_names: list[str]) -> list[str]:
    """Delete selected artifacts while preventing paths outside ``models_dir``."""

    if not models_dir.is_dir():
        raise FileNotFoundError(f"Model directory does not exist: {models_dir}")

    deleted = []
    root = models_dir.resolve()
    for name in selected_names:
        candidate = (models_dir / name).resolve()
        if (
            candidate.parent != root
            or candidate.suffix.lower() not in MODEL_ARTIFACT_SUFFIXES
        ):
            raise ValueError(f"Invalid model artifact selection: {name}")
        if not candidate.is_file():
            raise FileNotFoundError(f"Model artifact not found: {name}")
        candidate.unlink()
        deleted.append(name)
    return deleted


def _latest_training_gate_status(models_dir: Path) -> dict[str, Any]:
    """Return the newest accepted or rejected training-gate record for display."""

    candidates = list(
        (models_dir.parent / "reports" / "data_quality").glob("**/*.json")
    )
    candidates.extend(
        path for path in models_dir.glob("training_summary_*.json") if path.is_file()
    )
    if not candidates:
        return {"status": "unknown"}

    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        with latest.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Cannot read training gate record %s: %s", latest, error)
        return {"status": "unavailable", "path": str(latest)}

    manifest = payload.get("data_manifest")
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        quality = manifest.get("quality", {}) if isinstance(manifest, dict) else payload
    if not isinstance(quality, dict):
        quality = {}
    return {
        "status": quality.get("status", "accepted" if manifest else "unknown"),
        "path": str(latest),
        "symbol": payload.get(
            "symbol", manifest.get("symbol") if isinstance(manifest, dict) else None
        ),
        "timeframe": payload.get(
            "timeframe",
            manifest.get("timeframe") if isinstance(manifest, dict) else None,
        ),
        "bars": quality.get("bars", payload.get("n_samples")),
        "coverage_hours": quality.get("coverage_hours"),
        "error": payload.get("error"),
        "class_imbalance": (
            manifest.get("label_diagnostics", {}).get("class_imbalance")
            if isinstance(manifest, dict)
            else payload.get("label_diagnostics", {}).get("class_imbalance")
        ),
    }


def _ensure_market_watch_process() -> bool:
    """Start the live Market Watch collector once for this dashboard process."""
    global _market_watch_process, _market_watch_log_handle

    if not mt5_enabled or dashboard_direct_mt5:
        return True

    if _market_watch_process is not None and _market_watch_process.poll() is None:
        return True

    lock_path = Path(PROJECT_ROOT) / "market_data" / ".market_watch.lock"
    if lock_path.is_file():
        if os.name == "nt":
            return True
        try:
            owner_pid = int(lock_path.read_text(encoding="ascii").strip())
            os.kill(owner_pid, 0)
        except (OSError, ValueError, SystemError):
            try:
                lock_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.warning(
                    "Unable to remove stale Market Watch lock %s: %s",
                    lock_path,
                    cleanup_error,
                )
        else:
            return True

    if _market_watch_log_handle is not None:
        _market_watch_log_handle.close()
        _market_watch_log_handle = None

    watcher_script = (
        Path(PROJECT_ROOT) / "src" / "python" / "mt5_account" / "mt5_market_watch.py"
    )
    log_dir = Path(PROJECT_ROOT) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "market_watch.log"
    try:
        _market_watch_log_handle = log_path.open("a", encoding="utf-8")
        _market_watch_process = subprocess.Popen(
            [sys.executable, str(watcher_script)],
            cwd=str(PROJECT_ROOT),
            stdout=_market_watch_log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError as error:
        if _market_watch_log_handle is not None:
            _market_watch_log_handle.close()
            _market_watch_log_handle = None
        logger.error("Failed to start Market Watch collector: %s", error)
        _market_watch_process = None
        return False
    logger.info("Market Watch collector started (pid=%s)", _market_watch_process.pid)
    return True


# Page configuration
st.set_page_config(
    page_title="AI Smart Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not _ensure_market_watch_process():
    st.warning("جمع‌آوری داده‌های Market Watch شروع نشد؛ لاگ را بررسی کنید.")

# UI localization. Trading symbols, strategy identifiers, and numeric values stay
# unchanged so that translated labels never affect downstream processing.
TRANSLATIONS = {
    "فارسی": {
        "Control Panel": "پنل کنترل",
        "Trading Control": "کنترل معاملات",
        "Start Auto Trading": "شروع معاملات خودکار",
        "Stop Auto Trading": "توقف معاملات خودکار",
        "Navigation": "ناوبری",
        "Overview": "نمای کلی",
        "Strategies": "استراتژی‌ها",
        "Backtest Results": "نتایج بک‌تست",
        "ML Predictions": "پیش‌بینی‌های هوش مصنوعی",
        "Live Trading": "معاملات زنده",
        "Settings": "تنظیمات",
        "All Modules Loaded": "همه ماژول‌ها بارگذاری شدند",
        "Some modules unavailable": "برخی ماژول‌ها در دسترس نیستند",
        "Connected to MT5": "اتصال به MT5 برقرار است",
        "MT5 Not Connected - Live data unavailable": "اتصال به MT5 برقرار نیست؛ دادهٔ زنده در دسترس نیست",
        "Connect to MT5": "اتصال به MT5",
        "Last Update": "آخرین به‌روزرسانی",
        "Trading Overview Dashboard": "داشبورد نمای کلی معاملات",
        "Total Balance": "موجودی کل",
        "Equity": "ارزش خالص حساب",
        "Floating P&L": "سود و زیان شناور",
        "Today's P&L": "سود و زیان امروز",
        "Equity Curve": "نمودار ارزش حساب",
        "Recent Signals": "سیگنال‌های اخیر",
        "Live Signals": "سیگنال‌های زنده",
        "Signal Details": "جزئیات سیگنال",
        "Direction": "جهت",
        "Order Type": "نوع سفارش",
        "Symbols and timeframes": "نمادها و تایم‌فریم‌های انتخاب‌شده",
        "Select symbols from Market Watch": "نمادها را از Market Watch انتخاب کنید",
        "Timeframe for": "تایم‌فریم نماد",
        "At least one symbol must be selected.": "حداقل یک نماد باید انتخاب شود.",
        "Strength": "قدرت",
        "Score": "امتیاز",
        "Recommendation": "پیشنهاد",
        "Reason": "دلیل",
        "Entry Price": "قیمت ورود",
        "Stop Loss": "حد ضرر",
        "Take Profit": "حد سود",
        "No live signals have been detected yet.": "هنوز سیگنال زنده‌ای شناسایی نشده است.",
        "Generated at": "زمان تولید",
        "signals detected": "سیگنال شناسایی شد",
        "Status": "وضعیت",
        "Passed scoring": "عبور از امتیازدهی",
        "Detected": "شناسایی‌شده",
        "Recent Trades": "معاملات اخیر",
        "Strategy Performance": "عملکرد استراتژی‌ها",
        "Select Strategy to Analyze": "استراتژی مورد تحلیل",
        "Run Live Backtest": "اجرای بک‌تست زنده",
        "Backtest Complete!": "بک‌تست با موفقیت انجام شد!",
        "Backtest Analysis": "تحلیل بک‌تست",
        "Strategy": "استراتژی",
        "Initial Balance ($)": "موجودی اولیه (دلار)",
        "Days to Test": "تعداد روزهای آزمون",
        "Run Backtest Now": "اجرای بک‌تست",
        "AI/ML Predictions": "پیش‌بینی‌های هوش مصنوعی/یادگیری ماشین",
        "Generate New Predictions": "تولید پیش‌بینی‌های جدید",
        "Current Predictions": "پیش‌بینی‌های فعلی",
        "Feature Importance": "اهمیت ویژگی‌ها",
        "Model Performance Comparison": "مقایسه عملکرد مدل‌ها",
        "Live Trading Monitor": "پایش معاملات زنده",
        "Risk Level": "سطح ریسک",
        "LOW": "کم",
        "MODERATE": "متوسط",
        "HIGH": "زیاد",
        "Risk Management": "مدیریت ریسک",
        "Strategy Configuration": "پیکربندی استراتژی",
        "AI/ML Settings": "تنظیمات هوش مصنوعی/یادگیری ماشین",
        "Data Sources": "منابع داده",
        "Trading Timeframe": "بازه زمانی معاملات",
        "Market Watch Symbols": "نمادهای Market Watch",
        "Select Symbol": "انتخاب نماد",
        "Select Timeframe": "انتخاب تایم‌فریم",
        "Selected Market Watch Data": "دادهٔ انتخاب‌شده از Market Watch",
        "Market Watch is empty": "در Market Watch نمادی انتخاب نشده است",
        "MT5 Market Watch symbols": "نمادهای Market Watch متاتریدر",
        "Save Settings": "ذخیره تنظیمات",
        "Reset Defaults": "بازنشانی پیش‌فرض‌ها",
        "All Systems Operational": "همه سامانه‌ها فعال هستند",
        "Confirm & Enable Auto Trading": "تأیید و فعال‌سازی معاملات خودکار",
        "Auto trading blocked: MT5 is not connected/configured.": "معاملات خودکار مسدود شد: اتصال یا پیکربندی MT5 برقرار نیست.",
        "Auto trading is disabled by the server safety gate (set MT5_AUTO_TRADING_ENABLED=true explicitly).": "معاملات خودکار توسط گیت ایمنی سرور غیرفعال است (متغیر MT5_AUTO_TRADING_ENABLED=true را صریحاً تنظیم کنید).",
        "Server confirmation created; confirm below before any order can be sent.": "تأییدیه سرور ایجاد شد؛ پیش از ارسال سفارش آن را تأیید کنید.",
        "Confirmation received; starting after safety checks.": "تأییدیه دریافت شد؛ پس از بررسی‌های ایمنی آغاز می‌شود.",
        "Demo Mode - Real MT5 data unavailable": "حالت نمایشی؛ دادهٔ واقعی MT5 در دسترس نیست",
        "Running backtest...": "در حال اجرای بک‌تست...",
        "Backtest failed": "بک‌تست ناموفق بود",
        "Final Balance": "موجودی نهایی",
        "Total Return": "بازده کل",
        "Win Rate": "نرخ معاملات موفق",
        "Time": "زمان",
        "Entry Time": "زمان ورود",
        "Exit Time": "زمان خروج",
        "P&L ($)": "سود و زیان (دلار)",
        "Active Positions": "موقعیت‌های باز",
        "Open Positions": "موقعیت‌های باز",
        "Positions P&L": "سود و زیان موقعیت‌ها",
        "Free Margin": "وجه آزاد",
        "Margin Level": "سطح مارجین",
        "Model": "مدل",
        "Generating predictions...": "در حال تولید پیش‌بینی...",
        "Prediction failed": "تولید پیش‌بینی ناموفق بود",
        "No live strategy signals are available.": "سیگنال زنده‌ای از استراتژی‌ها در دسترس نیست.",
        "Source: MT5 open position": "منبع: موقعیت باز MT5",
        "Open Orders / Positions (MT5)": "سفارش‌ها / موقعیت‌های باز (MT5)",
        "Pending Limit Orders (MT5)": "سفارش‌های در انتظار (MT5)",
        "Recent MT5 Activity": "فعالیت اخیر MT5",
        "Manual Live Order (risk-gated)": "سفارش دستی زنده (تحت کنترل ریسک)",
        "Symbol": "نماد",
        "Order type": "نوع سفارش",
        "Volume": "حجم",
        "Limit price": "قیمت لیمیت",
        "Request Order Confirmation": "درخواست تأیید سفارش",
        "Execute Confirmed Order": "اجرای سفارش تأییدشده",
        "Close All Managed Positions": "بستن همه موقعیت‌های مدیریت‌شده",
        "Export Positions": "خروجی موقعیت‌ها",
        "Download CSV": "دانلود CSV",
        "Refresh Data": "تازه‌سازی داده‌ها",
        "Risk per Trade (%)": "ریسک هر معامله (%)",
        "Max Concurrent Positions": "حداکثر موقعیت‌های هم‌زمان",
        "Daily Loss Limit ($)": "حد ضرر روزانه (دلار)",
        "Reset Daily Loss": "ریست ضرر روزانه",
        "Daily loss calculation reset successfully.": "محاسبه ضرر روزانه با موفقیت ریست شد.",
        "Daily loss starts from": "شروع محاسبه ضرر روزانه از",
        "Maximum Position Volume (lots)": "حداکثر حجم هر موقعیت (لات)",
        "Break-even protection": "حفاظت بریک‌ایون",
        "Enable break-even": "فعال‌سازی بریک‌ایون",
        "Break-even trigger (R)": "آستانه بریک‌ایون (R)",
        "Enable partial position close": "فعال‌سازی بستن بخشی از پوزیشن",
        "Partial close trigger (R)": "آستانه بستن بخشی (R)",
        "Partial close volume (%)": "درصد حجم بستن بخشی (%)",
        "Partial close is enabled": "بستن بخشی فعال است",
        "SL/TP source": "منبع حد ضرر/سود",
        "Use strategy values": "مقادیر استراتژی",
        "Fixed distance": "فاصله ثابت",
        "Percentage of entry": "درصدی از قیمت ورود",
        "Stop loss distance": "فاصله حد ضرر",
        "Take profit distance": "فاصله حد سود",
        "Smart spread filter": "فیلتر هوشمند اسپرد",
        "Enable spread filter": "فعال‌سازی فیلتر اسپرد",
        "Maximum spread": "حداکثر اسپرد",
        "Spread limit mode": "حالت سقف اسپرد",
        "Fixed spread limit": "سقف ثابت اسپرد",
        "Dynamic ATR spread limit": "سقف پویای اسپرد بر اساس ATR",
        "ATR period for spread": "دوره ATR برای اسپرد",
        "ATR spread multiplier": "ضریب ATR اسپرد",
        "ATR spread timeframe": "تایم‌فریم ATR اسپرد",
        "Break-even entry offset": "آفست نقطه ورود بریک‌ایون",
        "Break-even status": "وضعیت بریک‌ایون",
        "Break-even applied": "بریک‌ایون اعمال شد",
        "Break-even rejected": "بریک‌ایون رد شد",
        "Max Drawdown (%)": "حداکثر افت سرمایه (%)",
        "Enable Strategies": "فعال‌سازی استراتژی‌ها",
        "Primary ML Model": "مدل اصلی یادگیری ماشین",
        "Primary Market Watch Symbol": "نماد اصلی Market Watch",
        "Trading Start Time (optional)": "زمان شروع معاملات (اختیاری)",
        "At least one strategy must be enabled before saving.": "پیش از ذخیره، حداقل یک استراتژی را فعال کنید.",
        "Settings saved and applied to this dashboard session.": "تنظیمات ذخیره و در این نشست داشبورد اعمال شد.",
        "Test MT5 Connection": "آزمون اتصال MT5",
        "Current Applied Settings": "تنظیمات اعمال‌شده فعلی",
        "Active symbol/timeframe selections": "انتخاب‌های فعال نماد/تایم‌فریم",
        "MT5 Connection: ACTIVE": "اتصال MT5: فعال",
        "MT5 Connection: DISCONNECTED — live values unavailable": "اتصال MT5: قطع — مقادیر زنده در دسترس نیست",
        "No open positions are currently reported by MT5.": "در حال حاضر MT5 موقعیت بازی گزارش نمی‌کند.",
        "Live order workflow is unavailable until MT5 is connected and configured.": "تا زمان اتصال و پیکربندی MT5، گردش‌کار سفارش زنده در دسترس نیست.",
        "No visible Market Watch symbols are available.": "نماد قابل مشاهده‌ای در Market Watch موجود نیست.",
        "No live quote is available for": "قیمت زنده‌ای برای این نماد موجود نیست",
        "No MT5 balance history is available for this period.": "تاریخچه موجودی MT5 برای این بازه در دسترس نیست.",
        "MT5 is disconnected; live account chart is unavailable.": "اتصال MT5 قطع است؛ نمودار زنده حساب در دسترس نیست.",
        "MT5 reports no open orders or positions for this account.": "MT5 برای این حساب سفارش یا موقعیت بازی گزارش نمی‌کند.",
        "MT5 is disconnected; open orders cannot be read.": "اتصال MT5 قطع است؛ سفارش‌های باز قابل خواندن نیستند.",
        "MT5 reports no pending limit or stop orders.": "MT5 سفارش لیمیت یا استاپ در انتظاری گزارش نمی‌کند.",
        "MT5 is disconnected; pending orders cannot be read.": "اتصال MT5 قطع است؛ سفارش‌های در انتظار قابل خواندن نیستند.",
        "MT5 is disconnected; recent trades are unavailable.": "اتصال MT5 قطع است؛ معاملات اخیر در دسترس نیستند.",
        "For BUY orders, SL must be below and TP above the entry price.": "در سفارش خرید، حد ضرر باید پایین‌تر و حد سود بالاتر از قیمت ورود باشد.",
        "For SELL orders, SL must be above and TP below the entry price.": "در سفارش فروش، حد ضرر باید بالاتر و حد سود پایین‌تر از قیمت ورود باشد.",
        "A one-time confirmation code was created. It expires in 120 seconds.": "کد تأیید یک‌بارمصرف ایجاد شد و پس از ۱۲۰ ثانیه منقضی می‌شود.",
        "No live order is sent until a confirmation code is requested and entered.": "تا زمان درخواست و ورود کد تأیید، هیچ سفارش زنده‌ای ارسال نمی‌شود.",
        "Order accepted by MT5": "سفارش توسط MT5 پذیرفته شد",
        "MT5 connection is active.": "اتصال MT5 فعال است.",
        "MT5 connection is unavailable.": "اتصال MT5 در دسترس نیست.",
        "MT5 order failed before completion; no retry was attempted.": "سفارش MT5 پیش از تکمیل ناموفق شد؛ تلاش مجدد انجام نشد.",
        "Confirmation code created. Enter it below and press Execute.": "کد تأیید ایجاد شد؛ آن را در کادر زیر وارد و اجرا را انتخاب کنید. این کد پس از ۱۲۰ ثانیه منقضی می‌شود.",
        "Close request accepted for": "درخواست بستن برای این تعداد موقعیت پذیرفته شد",
        "MT5 close-order failed; no retry was attempted.": "بستن سفارش MT5 ناموفق بود؛ تلاش مجدد انجام نشد.",
        "Primary Timeframe": "تایم‌فریم اصلی",
        "No MT5 activity reported in the last 24 hours.": "در ۲۴ ساعت گذشته فعالیتی از MT5 گزارش نشده است.",
        "Live cycle": "چرخه زنده",
        "signals": "سیگنال",
        "orders": "سفارش",
        "opened": "بازشده",
        "errors": "خطا",
        "Live cycle errors": "خطاهای چرخه زنده",
        "Live cycle skipped": "چرخه زنده رد شد",
        "Live spread monitor": "پایش لحظه‌ای اسپرد",
        "Current spread": "اسپرد فعلی",
        "Spread limit": "سقف اسپرد",
        "Spread status": "وضعیت اسپرد",
        "Allowed": "مجاز",
        "Blocked": "مسدود",
        "Unavailable": "در دسترس نیست",
        "Latest order rejection": "آخرین علت رد سفارش",
        "No order rejection recorded": "هنوز رد سفارشی ثبت نشده است",
        "Order failed for": "سفارش ناموفق بود برای",
        "daily loss circuit breaker is active": "مدار قطع‌کننده ضرر روزانه فعال است",
        "Protected SL/TP required for": "حد ضرر/حد سود حفاظتی لازم است برای",
        "position already open for": "برای این نماد از قبل موقعیت باز وجود دارد",
        "max positions reached": "حداکثر تعداد موقعیت‌ها رسیده است",
        "max total exposure": "حداکثر میزان درگیری سرمایه رسیده است",
        "MT5 rejected the order during validation": "MT5 سفارش را هنگام اعتبارسنجی رد کرد",
        "Invalid stops": "حد ضرر یا حد سود نامعتبر است",
        "Review price, SL/TP distance, volume, and symbol trading rules.": "قیمت، فاصله حد ضرر/حد سود، حجم و قوانین معاملاتی نماد را بررسی کنید.",
        "MT5 order_send failed": "ارسال سفارش به MT5 ناموفق بود",
        "MT5 rejected order": "MT5 سفارش را رد کرد",
        "broker_validation_failed": "اعتبارسنجی کارگزار ناموفق بود",
        "spread_too_high": "اسپرد بیش از حد مجاز است",
        "spread_unavailable": "اطلاعات اسپرد در دسترس نیست",
        "position_size_exceeded": "حجم سفارش از حد مجاز بیشتر است",
        "symbol_not_whitelisted": "نماد در فهرست مجاز نیست",
        "no_quote": "قیمت زنده دریافت نشد",
        "UTC time": "زمان UTC",
        "Account balance": "موجودی حساب",
        "MT5 realized balance + current balance": "موجودی تحقق‌یافته MT5 + موجودی فعلی",
        "Database credentials and broker paths remain managed through environment variables.": "اعتبارنامه پایگاه داده و مسیرهای کارگزار از طریق متغیرهای محیطی مدیریت می‌شوند.",
        "Minimum Confidence Threshold (%)": "حداقل آستانه اطمینان (%)",
        "Only trade signals with confidence above this threshold": "فقط سیگنال‌هایی با اطمینان بالاتر از این آستانه معامله شوند",
        "Data Source": "منبع داده",
        "Live execution price": "قیمت اجرای زنده",
        "Stop Loss (optional; 0 = none)": "حد ضرر (اختیاری؛ صفر = بدون حد ضرر)",
        "Take Profit (optional; 0 = none)": "حد سود (اختیاری؛ صفر = بدون حد سود)",
        "Confirmation code": "کد تأیید",
    }
}

language = st.sidebar.selectbox("Language / زبان", ["فارسی", "English"], index=0)


def t(text: str) -> str:
    """Return the localized UI label while keeping internal identifiers stable."""
    return TRANSLATIONS["فارسی"].get(text, text) if language == "فارسی" else text


def localize_cycle_error(message: str) -> str:
    """Translate known live-cycle error messages while preserving symbol details."""
    if language != "فارسی":
        return message
    localized = message
    for source, target in (
        ("Order failed for ", f"{t('Order failed for')} "),
        (
            "daily loss circuit breaker is active",
            t("daily loss circuit breaker is active"),
        ),
        ("Protected SL/TP required for ", f"{t('Protected SL/TP required for')} "),
        ("position already open for ", f"{t('position already open for')} "),
        ("max positions reached", t("max positions reached")),
        ("max total exposure", t("max total exposure")),
        (
            "MT5 rejected the order during validation",
            t("MT5 rejected the order during validation"),
        ),
        ("Invalid stops", t("Invalid stops")),
        (
            "Review price, SL/TP distance, volume, and symbol trading rules.",
            t("Review price, SL/TP distance, volume, and symbol trading rules."),
        ),
        ("MT5 order_send failed", t("MT5 order_send failed")),
        ("MT5 rejected order", t("MT5 rejected order")),
        ("broker_validation_failed", t("broker_validation_failed")),
        ("spread_too_high", t("spread_too_high")),
        ("spread_unavailable", t("spread_unavailable")),
        ("position_size_exceeded", t("position_size_exceeded")),
        ("symbol_not_whitelisted", t("symbol_not_whitelisted")),
        ("no_quote", t("no_quote")),
    ):
        localized = localized.replace(source, target)
    localized = re.sub(
        r"Order failed for ",
        f"{t('Order failed for')} ",
        localized,
    )
    return localized


# Custom CSS
text_direction = "rtl" if language == "فارسی" else "ltr"
st.markdown(
    f"""
<style>
    :root {{
        --ink: #17233d;
        --muted: #5d6d87;
        --surface: rgba(255, 255, 255, 0.78);
        --line: rgba(92, 111, 145, 0.22);
        --cyan: #00c9d7;
        --violet: #7357ff;
        --neon-green: #21e58b;
    }}
    .stApp {{
        background:
            radial-gradient(circle at 8% 8%, rgba(0, 231, 205, 0.20), transparent 28rem),
            radial-gradient(circle at 92% 4%, rgba(168, 255, 0, 0.16), transparent 25rem),
            linear-gradient(135deg, #f7fbff 0%, #eef5ff 48%, #f9f5ff 100%);
        color: var(--ink);
    }}
    [data-testid="stHeader"] {{ background: rgba(255, 255, 255, 0.78); }}
    [data-testid="stAppViewContainer"] .main,
    [data-testid="stSidebar"] > div:first-child {{
        direction: {text_direction};
    }}
    [data-testid="stAppViewContainer"] .main {{ background: transparent; }}
    [data-testid="stAppViewContainer"] .block-container {{
        max-width: 1480px;
        padding: 2.5rem 3rem 4rem;
    }}
    [data-testid="stSidebar"] > div:first-child {{
        background: linear-gradient(180deg, rgba(255, 255, 255, .96), rgba(239, 247, 255, .98));
        border-right: 1px solid var(--line);
        box-shadow: 14px 0 45px rgba(0, 0, 0, .22);
    }}
    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stAppViewContainer"] label,
    [data-testid="stAppViewContainer"] button,
    [data-testid="stAppViewContainer"] p,
    [data-testid="stAppViewContainer"] h1,
    [data-testid="stAppViewContainer"] h2,
    [data-testid="stAppViewContainer"] h3 {{
        font-family: Tahoma, "Segoe UI", sans-serif;
        color: var(--ink);
    }}
    [data-testid="stAppViewContainer"] h1 {{
        font-size: clamp(2rem, 4vw, 3.35rem);
        letter-spacing: -.04em;
        text-shadow: 0 8px 30px rgba(0, 201, 215, .18);
    }}
    .metric-card {{
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 20px;
        margin: 10px 0;
        box-shadow: 0 18px 45px rgba(55, 78, 119, .16);
        transition: transform .28s ease, box-shadow .28s ease, border-color .28s ease;
        backdrop-filter: blur(18px);
    }}
    .metric-card:hover {{
        transform: perspective(900px) rotateX(2deg) translateY(-5px) translateZ(10px);
        border-color: rgba(0, 201, 215, .72);
        box-shadow: 0 24px 55px rgba(55, 78, 119, .22), 0 0 28px rgba(33, 229, 139, .20);
    }}
    [data-testid="stMetric"] {{
        background: linear-gradient(145deg, rgba(255, 255, 255, .94), rgba(232, 249, 250, .82));
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 1.1rem 1.2rem;
        min-height: 112px;
        box-shadow: 0 16px 36px rgba(55, 78, 119, .15);
        transition: transform .25s ease, border-color .25s ease;
    }}
    [data-testid="stMetric"]:hover {{
        transform: translateY(-4px) rotateX(2deg);
        border-color: rgba(115, 87, 255, .72);
    }}
    [data-testid="stMetricLabel"] p {{ color: var(--muted) !important; font-size: .9rem; }}
    [data-testid="stMetricValue"] {{ color: var(--ink) !important; font-weight: 800; }}
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stAppViewContainer"] .stButton > button {{
        border: 1px solid var(--line);
        border-radius: 13px;
        background: linear-gradient(135deg, rgba(218, 255, 244, .96), rgba(225, 240, 255, .96));
        color: var(--ink);
        box-shadow: 0 9px 22px rgba(55, 78, 119, .14);
        transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
    }}
    [data-testid="stAppViewContainer"] .stButton > button:hover {{
        transform: translateY(-2px);
        border-color: var(--neon-green);
        box-shadow: 0 12px 26px rgba(55, 78, 119, .18), 0 0 18px rgba(33, 229, 139, .28);
    }}
    [data-testid="stDataFrame"], [data-testid="stTable"] {{
        border: 1px solid var(--line);
        border-radius: 18px;
        overflow: hidden;
        box-shadow: 0 14px 35px rgba(55, 78, 119, .13);
    }}
    .smart-hero {{
        position: relative;
        overflow: hidden;
        margin: .4rem 0 1.7rem;
        padding: 2rem 2.25rem;
        border: 1px solid rgba(0, 201, 215, .36);
        border-radius: 28px;
        background: linear-gradient(120deg, rgba(255, 255, 255, .96), rgba(222, 252, 248, .90));
        box-shadow: 0 28px 70px rgba(55, 78, 119, .18), inset 0 1px 0 rgba(255,255,255,.90);
        transform: perspective(1200px) rotateX(1deg);
    }}
    .smart-hero::after {{
        content: "";
        position: absolute;
        width: 190px;
        height: 190px;
        right: 7%;
        top: -85px;
        border: 1px solid rgba(0, 201, 215, .38);
        border-radius: 50%;
        box-shadow: 0 0 0 18px rgba(0, 201, 215, .08), 0 0 0 38px rgba(33, 229, 139, .05);
    }}
    .smart-hero-kicker {{
        color: var(--cyan);
        font-size: .78rem;
        font-weight: 800;
        letter-spacing: .16em;
        text-transform: uppercase;
    }}
    .smart-hero-title {{
        position: relative;
        z-index: 1;
        margin: .45rem 0;
        color: #17233d;
        font-size: clamp(1.5rem, 3vw, 2.35rem);
        font-weight: 900;
    }}
    .smart-hero-copy {{
        position: relative;
        z-index: 1;
        max-width: 680px;
        color: #50627e;
        line-height: 1.8;
        margin: 0;
    }}
    .smart-status {{
        position: relative;
        z-index: 1;
        display: inline-flex;
        margin-top: 1.1rem;
        padding: .5rem .85rem;
        border-radius: 999px;
        color: #087348;
        background: rgba(33, 229, 139, .18);
        border: 1px solid rgba(33, 229, 139, .58);
        font-size: .82rem;
        font-weight: 700;
    }}
    .smart-status--disconnected {{
        color: #9b2c2c;
        background: rgba(255, 191, 191, .24);
        border-color: rgba(198, 64, 64, .52);
    }}
    .stAlert {{ border-radius: 16px; border: 1px solid var(--line); background: rgba(255, 255, 255, .78); }}
    hr {{ border-color: var(--line) !important; }}
    @media (max-width: 900px) {{
        [data-testid="stAppViewContainer"] .block-container {{ padding: 1.5rem 1rem 3rem; }}
        .smart-hero {{ padding: 1.45rem; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
        *, *::before, *::after {{ transition: none !important; transform: none !important; }}
    }}
</style>
""",
    unsafe_allow_html=True,
)

# Dashboard settings are persisted outside Streamlit session state so a browser
# refresh or a new Streamlit session does not silently restore defaults.
SETTINGS_FILE = Path(PROJECT_ROOT) / "data" / "dashboard_settings.json"
DEFAULT_SETTINGS = {
    "risk_per_trade": 1.0,
    "max_positions": 3,
    "daily_loss_limit": 500.0,
    "daily_loss_reset_at": 0.0,
    "max_position_volume": 1.0,
    "break_even_enabled": True,
    "break_even_trigger_r": 1.0,
    "break_even_entry_offset": 0.0,
    "partial_close_enabled": False,
    "partial_close_trigger_r": 1.5,
    "partial_close_percent": 50.0,
    "sl_tp_mode": "signal",
    "stop_loss_value": 0.0,
    "take_profit_value": 0.0,
    "spread_filter_enabled": False,
    "max_spread": 0.0,
    "spread_mode": "fixed",
    "spread_atr_period": 14,
    "spread_atr_multiplier": 1.0,
    "spread_atr_timeframe": "H1",
    "max_drawdown": 15.0,
    "enabled_strategies": ["TrendFollowing"],
    "ml_model": "Ensemble",
    "min_confidence": 60,
    "symbol": "",
    "timeframe": "H1",
    "symbol_timeframes": {},
    "trading_start": None,
    "auto_trading_enabled": False,
    "auto_trading_expires_at": 0.0,
    "shadow_trading_enabled": False,
    "ml_scheduler_symbol": "",
    "ml_scheduler_timeframe": "H1",
    "ml_scheduler_pid": 0,
    "dashboard_page": "Overview",
    # ML Training Settings
    "ml_training_enabled": False,
    "retrain_schedule": "weekly",
    "ml_accuracy_threshold": 65.0,
    "ml_profit_factor_threshold": 1.2,
}


def _load_saved_settings() -> dict[str, Any]:
    """Load persisted dashboard settings, falling back safely on invalid data."""
    try:
        if not SETTINGS_FILE.exists():
            return DEFAULT_SETTINGS.copy()
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("dashboard settings must be a JSON object")
        settings = DEFAULT_SETTINGS.copy()
        settings.update(
            {key: value for key, value in stored.items() if key in settings}
        )
        if isinstance(settings.get("trading_start"), str):
            try:
                from datetime import time as datetime_time

                settings["trading_start"] = datetime_time.fromisoformat(
                    settings["trading_start"]
                )
            except ValueError:
                settings["trading_start"] = None
        return settings
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Unable to load dashboard settings: %s", error)
        return DEFAULT_SETTINGS.copy()


def _persist_saved_settings(settings: dict[str, Any]) -> None:
    """Persist non-secret dashboard settings atomically for future sessions."""
    with _SETTINGS_WRITE_LOCK:
        serializable = dict(settings)
        trading_start = serializable.get("trading_start")
        if hasattr(trading_start, "isoformat"):
            serializable["trading_start"] = trading_start.isoformat()
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_name(
            f"{SETTINGS_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(serializable, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        for attempt in range(3):
            try:
                temporary.replace(SETTINGS_FILE)
                break
            except PermissionError:
                if attempt == 2:
                    temporary.unlink(missing_ok=True)
                    raise
                time.sleep(0.05 * (attempt + 1))


def _persist_dashboard_preference(key: str, value: Any) -> None:
    """Persist a non-secret page preference without changing risk limits."""
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings[key] = value
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


def _persist_shadow_trading_state(enabled: bool) -> None:
    """Persist shadow state without sharing the live authorization lease."""
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings["shadow_trading_enabled"] = bool(enabled)
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


def _persist_ml_scheduler_state(
    enabled: bool,
    symbol: str | None = None,
    timeframe: str | None = None,
    schedule: str | None = None,
) -> None:
    """Persist the scheduler intent so a browser refresh cannot silently stop it."""
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings["ml_training_enabled"] = bool(enabled)
    if enabled:
        if symbol:
            settings["ml_scheduler_symbol"] = str(symbol)
        if timeframe:
            settings["ml_scheduler_timeframe"] = str(timeframe)
        if schedule:
            settings["retrain_schedule"] = str(schedule)
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


def _reset_live_signal_view() -> None:
    """Discard runtime signals so the dashboard cannot show stale symbols."""
    st.session_state.live_signals = []
    st.session_state.live_signal_cycle = None
    loop = st.session_state.get("live_trading_loop")
    if loop is not None:
        loop.stop()
    st.session_state.live_trading_loop = None


def _active_symbol_timeframes() -> dict[str, str]:
    """Return the currently saved symbol/timeframe mapping with legacy fallback."""
    saved = st.session_state.get("saved_settings", {})
    pairs = saved.get("symbol_timeframes", {})
    if isinstance(pairs, dict):
        normalized = {
            str(symbol): str(timeframe)
            for symbol, timeframe in pairs.items()
            if str(symbol).strip() and str(timeframe).strip()
        }
        if normalized:
            return normalized
    symbol = str(saved.get("symbol") or "").strip()
    if symbol:
        return {symbol: str(saved.get("timeframe", "H1"))}
    return {}


def _ml_job_running() -> bool:
    """Return whether a dashboard-started ML process is still running."""
    process = st.session_state.get("ml_process")
    return process is not None and process.poll() is None


def _process_id_is_running(process_id: Any) -> bool:
    """Check a persisted Windows process id without adopting or terminating it."""
    try:
        pid = int(process_id or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, TypeError, ValueError):
        return False


def _ml_log_tail() -> str:
    """Read the latest ML operation output without blocking the dashboard."""
    log_path = st.session_state.get("ml_log_path")
    if not log_path:
        return ""
    try:
        lines = (
            Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return ""
    return "\n".join(lines[-30:])


def _start_ml_process(kind: str, symbol: str, timeframe: str, years: int) -> None:
    """Start one allowlisted ML operation and capture its output to a log file."""
    if _ml_job_running():
        raise RuntimeError("An ML operation is already running.")
    allowed_timeframes = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
    if not symbol.strip() or timeframe not in allowed_timeframes:
        raise ValueError("Invalid symbol or timeframe.")
    if years < 1 or years > 20:
        raise ValueError("years must be between 1 and 20.")
    script = "train_model.py" if kind == "training" else "retrain_model.py"
    command = [
        sys.executable,
        str(Path(PROJECT_ROOT) / "scripts" / script),
        "--symbol",
        symbol.strip(),
        "--timeframe",
        timeframe,
        "--data-dir",
        str(Path(PROJECT_ROOT) / "market_data"),
        "--models-dir" if kind == "retraining" else "--output-dir",
        str(Path(PROJECT_ROOT) / "models"),
    ]
    if kind == "training":
        command.extend(
            [
                "--years",
                str(years),
                "--model-type",
                str(st.session_state.ml_model_type).lower(),
            ]
        )
    else:
        command.extend(["--schedule", "manual", "--force"])
    log_dir = Path(PROJECT_ROOT) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"dashboard_ml_{kind}_{int(time.time())}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        log_handle.close()
        raise
    st.session_state.ml_process = process
    st.session_state.ml_log_handle = log_handle
    st.session_state.ml_log_path = str(log_path)
    st.session_state.ml_job_kind = kind
    st.session_state.ml_job_started_at = datetime.now(timezone.utc).isoformat()


def _start_ml_scheduler(symbol: str, timeframe: str, schedule: str) -> None:
    """Start the allowlisted periodic retraining scheduler."""
    if _ml_job_running():
        raise RuntimeError("An ML operation is already running.")
    if not symbol.strip() or timeframe not in {
        "M1",
        "M5",
        "M15",
        "M30",
        "H1",
        "H4",
        "D1",
        "W1",
        "MN1",
    }:
        raise ValueError("Invalid symbol or timeframe.")
    if schedule not in {"daily", "weekly", "monthly"}:
        raise ValueError("Invalid retraining schedule.")
    log_dir = Path(PROJECT_ROOT) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"dashboard_ml_scheduler_{int(time.time())}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        str(Path(PROJECT_ROOT) / "scripts" / "ml_scheduler.py"),
        "--symbol",
        symbol.strip(),
        "--timeframe",
        timeframe,
        "--schedule",
        schedule,
        "--interval-seconds",
        "3600",
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        log_handle.close()
        raise
    st.session_state.ml_process = process
    st.session_state.ml_log_handle = log_handle
    st.session_state.ml_log_path = str(log_path)
    st.session_state.ml_job_kind = "scheduler"
    st.session_state.ml_job_started_at = datetime.now(timezone.utc).isoformat()
    _persist_ml_scheduler_state(True, symbol, timeframe, schedule)
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings["ml_scheduler_pid"] = int(process.pid)
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


def _stop_ml_process() -> None:
    """Stop only the process created by this dashboard session."""
    process = st.session_state.get("ml_process")
    if process is not None and process.poll() is None:
        process.terminate()
    elif st.session_state.get("ml_job_kind") == "scheduler":
        scheduler_pid = st.session_state.get("saved_settings", {}).get(
            "ml_scheduler_pid"
        )
        if _process_id_is_running(scheduler_pid):
            os.kill(int(scheduler_pid), 15)
    handle = st.session_state.pop("ml_log_handle", None)
    if handle is not None:
        handle.close()
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings["ml_scheduler_pid"] = 0
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


def _poll_ml_process() -> None:
    """Refresh process state and close the dashboard-owned log handle."""
    process = st.session_state.get("ml_process")
    if process is None:
        return
    return_code = process.poll()
    if return_code is not None:
        st.session_state.ml_last_return_code = return_code
        handle = st.session_state.pop("ml_log_handle", None)
        if handle is not None:
            handle.close()


def _ml_scheduler_running() -> bool:
    """Return whether the dashboard-owned or restored scheduler is active."""
    if _ml_job_running():
        return st.session_state.get("ml_job_kind") == "scheduler"
    return _process_id_is_running(
        st.session_state.get("saved_settings", {}).get("ml_scheduler_pid")
    )


# Session state management
if "trading_active" not in st.session_state:
    st.session_state.trading_active = False
if "positions" not in st.session_state:
    st.session_state.positions = []
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []
if "live_signals" not in st.session_state:
    st.session_state.live_signals = []
if "live_signal_cycle" not in st.session_state:
    st.session_state.live_signal_cycle = None
if "live_confirmation_token" not in st.session_state:
    st.session_state.live_confirmation_token = ""
if "pending_live_order" not in st.session_state:
    st.session_state.pending_live_order = None
if "pending_live_limit_order" not in st.session_state:
    st.session_state.pending_live_limit_order = None
if "auto_trading_confirmation" not in st.session_state:
    st.session_state.auto_trading_confirmation = ""
if "auto_trading_confirm_requested" not in st.session_state:
    st.session_state.auto_trading_confirm_requested = False
if "shadow_start_requested" not in st.session_state:
    st.session_state.shadow_start_requested = False
if "live_trading_loop" not in st.session_state:
    st.session_state.live_trading_loop = None
if "saved_settings" not in st.session_state:
    st.session_state.saved_settings = _load_saved_settings()
if "ml_process" not in st.session_state:
    st.session_state.ml_process = None
if "ml_log_path" not in st.session_state:
    st.session_state.ml_log_path = ""
if "ml_job_kind" not in st.session_state:
    st.session_state.ml_job_kind = ""
if "ml_last_return_code" not in st.session_state:
    st.session_state.ml_last_return_code = None
if "ml_model_type" not in st.session_state:
    st.session_state.ml_model_type = "Ensemble"
_poll_ml_process()
if (
    MODULES_AVAILABLE
    and bool(st.session_state.saved_settings.get("ml_training_enabled"))
    and not _ml_job_running()
    and not _process_id_is_running(
        st.session_state.saved_settings.get("ml_scheduler_pid")
    )
):
    scheduler_symbol = str(
        st.session_state.saved_settings.get("ml_scheduler_symbol")
        or st.session_state.saved_settings.get("symbol")
        or ""
    ).strip()
    scheduler_timeframe = str(
        st.session_state.saved_settings.get("ml_scheduler_timeframe")
        or st.session_state.saved_settings.get("timeframe")
        or "H1"
    )
    scheduler_schedule = str(
        st.session_state.saved_settings.get("retrain_schedule") or "weekly"
    )
    if scheduler_symbol:
        try:
            _start_ml_scheduler(
                scheduler_symbol,
                scheduler_timeframe,
                scheduler_schedule,
            )
            logger.info(
                "Persisted ML scheduler restored for %s/%s",
                scheduler_symbol,
                scheduler_timeframe,
            )
        except (OSError, RuntimeError, ValueError) as error:
            logger.warning("Persisted ML scheduler was not restored: %s", error)
if (
    not st.session_state.trading_active
    and MODULES_AVAILABLE
    and bool(st.session_state.saved_settings.get("auto_trading_enabled"))
    and float(
        st.session_state.saved_settings.get("auto_trading_expires_at", 0.0) or 0.0
    )
    > time.time()
    and LiveTradingLoop.enabled_by_server()
):
    st.session_state.trading_active = True


def get_market_watch_symbols() -> List[Dict[str, Any]]:
    """Return symbols currently visible in MT5 Market Watch."""
    if not mt5_connector or not mt5_connector.is_connected():
        return []
    cache_key = "_market_watch_symbols"
    cached_at = st.session_state.get("_market_watch_symbols_at", 0.0)
    cached_symbols = st.session_state.get(cache_key)
    if cached_symbols is not None and time.monotonic() - cached_at < 10.0:
        return cached_symbols
    try:
        symbols = mt5_connector.get_symbols_list(visible_only=True)
        st.session_state[cache_key] = symbols
        st.session_state["_market_watch_symbols_at"] = time.monotonic()
        return symbols
    except Exception as error:
        st.error(f"Error fetching Market Watch symbols: {error}")
        return []


def _sync_selected_market_data(
    additional_subscriptions: list[tuple[str, str, int]] | None = None,
    force: bool = False,
) -> int:
    """Persist selected Market Watch candles through the dashboard MT5 owner."""
    if not mt5_connector or not mt5_connector.is_connected():
        return 0

    saved = st.session_state.get("saved_settings", {})
    configured_pairs = saved.get("symbol_timeframes", {})
    if not isinstance(configured_pairs, dict):
        return 0

    visible_symbols = {
        str(item.get("name", "")).upper(): str(item.get("name", ""))
        for item in get_market_watch_symbols()
        if item.get("name")
    }
    subscriptions = [
        (visible_symbols[str(symbol).upper()], str(timeframe), DEFAULT_HISTORY_COUNT)
        for symbol, timeframe in configured_pairs.items()
        if str(symbol).upper() in visible_symbols
        and str(timeframe).upper()
        in {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
    ]
    if additional_subscriptions:
        subscriptions.extend(
            (visible_symbols[symbol.upper()], timeframe.upper(), count)
            for symbol, timeframe, count in additional_subscriptions
            if symbol.upper() in visible_symbols
            and timeframe.upper()
            in {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
            and count > 0
        )
        subscriptions = list(dict.fromkeys(subscriptions))
    if not subscriptions:
        return 0

    last_sync = float(st.session_state.get("_market_data_sync_at", 0.0) or 0.0)
    if not force and time.monotonic() - last_sync < 15.0:
        return 0
    saved_count = sync_market_data_from_connector(
        mt5_connector,
        subscriptions,
        data_dir=Path(PROJECT_ROOT) / "market_data",
    )
    st.session_state["_market_data_sync_at"] = time.monotonic()
    st.session_state["_market_data_sync_count"] = saved_count
    return saved_count


def _get_ml_operation_symbols(
    market_watch_symbols: List[Dict[str, Any]], data_dir: str | os.PathLike[str]
) -> list[str]:
    """Return safe training symbols, preferring MT5 and falling back to local candles."""
    market_symbols = list(
        dict.fromkeys(
            str(item.get("name", "")).strip()
            for item in market_watch_symbols
            if str(item.get("name", "")).strip()
        )
    )
    if market_symbols:
        return market_symbols

    allowed_timeframes = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
    local_symbols: set[str] = set()
    data_path = Path(data_dir)
    if not data_path.is_dir():
        return []
    for candle_file in data_path.glob("*_*.json"):
        symbol, separator, timeframe = candle_file.stem.rpartition("_")
        if separator and symbol and timeframe in allowed_timeframes:
            local_symbols.add(symbol)
    return sorted(local_symbols)


def get_live_order_workflow() -> Optional[LiveOrderWorkflow]:
    """Build the fail-closed live adapter; no adapter means no order path."""
    if not mt5_connector:
        return None
    try:
        if "_live_order_workflow" not in st.session_state:
            config = LiveOrderConfig.from_environment()
            saved = st.session_state.get("saved_settings", {})
            if not config.allowed_symbols:
                config = LiveOrderConfig(
                    allowed_symbols=frozenset(
                        item["name"].upper()
                        for item in get_market_watch_symbols()
                        if item.get("name")
                    ),
                    magic=config.magic,
                    max_position_volume=float(
                        saved.get("max_position_volume", config.max_position_volume)
                    ),
                    max_daily_loss=float(
                        saved.get("daily_loss_limit", config.max_daily_loss)
                    ),
                    daily_loss_reset_at=float(
                        saved.get("daily_loss_reset_at", 0.0) or 0.0
                    ),
                    max_spread=float(saved.get("max_spread", config.max_spread)),
                    spread_mode=str(saved.get("spread_mode", config.spread_mode)),
                    spread_atr_period=int(
                        saved.get("spread_atr_period", config.spread_atr_period)
                    ),
                    spread_atr_multiplier=float(
                        saved.get("spread_atr_multiplier", config.spread_atr_multiplier)
                    ),
                    spread_atr_timeframe=str(
                        saved.get("spread_atr_timeframe", config.spread_atr_timeframe)
                    ),
                    confirmation_ttl_seconds=config.confirmation_ttl_seconds,
                    automation_session_seconds=config.automation_session_seconds,
                )
            elif "daily_loss_limit" in saved:
                config = LiveOrderConfig(
                    allowed_symbols=config.allowed_symbols,
                    magic=config.magic,
                    max_position_volume=float(
                        saved.get("max_position_volume", config.max_position_volume)
                    ),
                    max_daily_loss=float(saved["daily_loss_limit"]),
                    daily_loss_reset_at=float(
                        saved.get("daily_loss_reset_at", 0.0) or 0.0
                    ),
                    max_spread=float(saved.get("max_spread", config.max_spread)),
                    spread_mode=str(saved.get("spread_mode", config.spread_mode)),
                    spread_atr_period=int(
                        saved.get("spread_atr_period", config.spread_atr_period)
                    ),
                    spread_atr_multiplier=float(
                        saved.get("spread_atr_multiplier", config.spread_atr_multiplier)
                    ),
                    spread_atr_timeframe=str(
                        saved.get("spread_atr_timeframe", config.spread_atr_timeframe)
                    ),
                    confirmation_ttl_seconds=config.confirmation_ttl_seconds,
                    automation_session_seconds=config.automation_session_seconds,
                )
            st.session_state._live_order_workflow = LiveOrderWorkflow(
                mt5_connector, config
            )
        return st.session_state._live_order_workflow
    except (TypeError, ValueError) as error:
        logger = __import__("logging").getLogger(__name__)
        logger.error("Live workflow configuration rejected: %s", error)
        return None


def _persist_auto_trading_state(enabled: bool, expires_at: float = 0.0) -> None:
    """Persist only the active lease state; confirmation tokens are never persisted."""
    settings = dict(st.session_state.get("saved_settings", DEFAULT_SETTINGS))
    settings["auto_trading_enabled"] = bool(enabled)
    settings["auto_trading_expires_at"] = float(expires_at if enabled else 0.0)
    st.session_state.saved_settings = settings
    _persist_saved_settings(settings)


# Sidebar
st.sidebar.title(f"🎛️ {t('Control Panel')}")
st.sidebar.markdown("---")

# Module status
if MODULES_AVAILABLE:
    st.sidebar.success(f"✅ {t('All Modules Loaded')}")
else:
    st.sidebar.warning(f"⚠️ {t('Some modules unavailable')}")

# Trading control
st.sidebar.subheader(f"⚡ {t('Trading Control')}")
live_trading_enabled = (
    MODULES_AVAILABLE
    and LiveTradingLoop.enabled_by_server()
    and os.getenv("MT5_DEMO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
)
if st.sidebar.button(
    f"🚀 {t('Start Auto Trading')}",
    disabled=st.session_state.trading_active or not live_trading_enabled,
    type="primary",
):
    workflow = get_live_order_workflow()
    if workflow is None or not mt5_connector or not mt5_connector.is_connected():
        st.sidebar.error(t("Auto trading blocked: MT5 is not connected/configured."))
    elif not LiveTradingLoop.enabled_by_server():
        st.sidebar.warning(
            t(
                "Auto trading is disabled by the server safety gate "
                "(set MT5_AUTO_TRADING_ENABLED=true explicitly)."
            )
        )
    else:
        st.session_state.auto_trading_confirmation = workflow.request_confirmation(
            "start_auto_trading"
        )
        st.session_state.auto_trading_confirm_requested = False
        st.sidebar.warning(
            t(
                "Server confirmation created; confirm below before any order can be sent."
            )
        )

if not live_trading_enabled:
    st.sidebar.info(
        t(
            "Live automation is disabled; Demo Shadow Mode is the only available trading mode."
        )
    )

if st.sidebar.button(
    "🧪 Start Demo Shadow Mode",
    disabled=st.session_state.trading_active,
    type="secondary",
):
    st.session_state.shadow_start_requested = True

if st.session_state.auto_trading_confirmation:
    st.sidebar.code(st.session_state.auto_trading_confirmation, language=None)
    if st.sidebar.button(t("Confirm & Enable Auto Trading"), type="primary"):
        st.session_state.auto_trading_confirm_requested = True
        st.sidebar.info(t("Confirmation received; starting after safety checks."))

if st.sidebar.button(
    f"⏹️ {t('Stop Auto Trading')}",
    disabled=not st.session_state.trading_active,
    type="secondary",
):
    if st.session_state.live_trading_loop:
        st.session_state.live_trading_loop.stop()
    st.session_state.trading_active = False
    st.session_state.auto_trading_confirmation = ""
    st.session_state.auto_trading_confirm_requested = False
    _persist_auto_trading_state(False)
    _persist_shadow_trading_state(False)
    st.sidebar.info(f"ℹ️ {t('Stop Auto Trading')}")

# Navigation
page = st.sidebar.radio(
    t("Navigation"),
    [
        "Overview",
        "Strategies",
        "Backtest Results",
        "ML Predictions",
        "Live Trading",
        "Settings",
    ],
    format_func=t,
    index=[
        "Overview",
        "Strategies",
        "Backtest Results",
        "ML Predictions",
        "Live Trading",
        "Settings",
    ].index(
        st.session_state.saved_settings.get("dashboard_page", "Overview")
        if st.session_state.saved_settings.get("dashboard_page", "Overview")
        in {
            "Overview",
            "Strategies",
            "Backtest Results",
            "ML Predictions",
            "Live Trading",
            "Settings",
        }
        else "Overview"
    ),
)
if page != st.session_state.saved_settings.get("dashboard_page"):
    _persist_dashboard_preference("dashboard_page", page)

st.sidebar.markdown("---")
st.sidebar.info(
    f"**{t('Last Update')}:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)


# Helper functions for REAL data from MT5
def get_real_account_data() -> Dict[str, Any]:
    """Get real account data from MT5 via data manager."""
    if data_manager:
        try:
            return data_manager.get_account_data()
        except Exception as e:
            st.error(f"Error fetching account data: {e}")
    return {
        "balance": 0.0,
        "equity": 0.0,
        "profit": 0.0,
        "margin_free": 0.0,
        "margin_level": 0.0,
        "currency": "",
        "login": None,
        "server": None,
        "connected": False,
    }


def _format_account_value(value: Any, suffix: str = "") -> str:
    """Format optional broker values without crashing disconnected views."""
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid account value: %r", value)
        return "—"


def get_real_positions_data() -> pd.DataFrame:
    """Get real positions data from MT5."""
    if data_manager:
        try:
            return data_manager.get_positions_data()
        except Exception as e:
            st.error(f"Error fetching positions: {e}")
    return pd.DataFrame()


def get_real_orders_data() -> pd.DataFrame:
    """Get pending orders reported by MT5."""
    if data_manager:
        try:
            return data_manager.get_orders_data()
        except Exception as error:
            st.error(f"Error fetching pending orders: {error}")
    return pd.DataFrame()


def get_real_history_data(days: int = 30) -> pd.DataFrame:
    """Get real history data from MT5."""
    if data_manager:
        try:
            return data_manager.get_history_data(days)
        except Exception as e:
            st.error(f"Error fetching history: {e}")
    return pd.DataFrame()


def get_real_equity_curve(days: int = 30) -> pd.DataFrame:
    """Get real equity curve from MT5 history."""
    if data_manager:
        try:
            return data_manager.get_equity_curve(days)
        except Exception as e:
            st.error(f"Error fetching equity curve: {e}")
    return pd.DataFrame(columns=["time", "equity"])


def get_real_performance_metrics() -> Dict[str, Any]:
    """Get real performance metrics from MT5."""
    if data_manager:
        try:
            return data_manager.get_performance_metrics()
        except Exception as e:
            st.error(f"Error fetching metrics: {e}")
    return {"win_rate": 0, "total_trades": 0, "profit_factor": 0}


def get_real_market_data(symbols: List[str] = None) -> Dict[str, Dict]:
    """Get real market data from MT5."""
    if data_manager:
        try:
            return data_manager.get_market_data(symbols)
        except Exception as e:
            st.error(f"Error fetching market data: {e}")
    return {}


def get_live_auto_market_payload(
    symbols: List[str], timeframe: str
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, float]]:
    """Build strategy input strictly from connected MT5 data."""
    if not mt5_connector or not mt5_connector.is_connected():
        return {}, {}
    market_data: Dict[str, Dict[str, Any]] = {}
    prices: Dict[str, float] = {}
    for symbol in symbols:
        candles = mt5_connector.get_historical_candles(
            symbol, timeframe=timeframe, count=250
        )
        tick = mt5_connector.symbol_info_tick(symbol)
        if len(candles) < 60 or tick is None:
            continue
        market_data[symbol] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [
                {
                    **candle,
                    "timestamp": datetime.fromtimestamp(
                        candle["time"], tz=timezone.utc
                    ),
                    "volume": candle.get("tick_volume", 0),
                }
                for candle in candles
            ],
            "context": {},
        }
        prices[symbol] = float((tick.bid + tick.ask) / 2.0)
    return market_data, prices


def build_live_trading_loop() -> LiveTradingLoop:
    """Construct the live loop from current account and saved risk settings."""
    if not mt5_connector:
        raise LiveOrderRejected("mt5_unavailable", "MT5 connector is unavailable")
    workflow = get_live_order_workflow()
    if workflow is None:
        raise LiveOrderRejected("workflow_unavailable", "live workflow is unavailable")
    account = get_real_account_data()
    saved = st.session_state.get("saved_settings", {})
    market_watch = {
        str(item["name"]).upper(): str(item["name"])
        for item in get_market_watch_symbols()
        if item.get("name")
    }
    configured_pairs = saved.get("symbol_timeframes", {})
    if not isinstance(configured_pairs, dict):
        configured_pairs = {}
    symbol_timeframes = {
        market_watch[str(symbol).upper()]: str(timeframe)
        for symbol, timeframe in configured_pairs.items()
        if str(symbol).upper() in market_watch
        and str(symbol).upper() in workflow.config.allowed_symbols
        and str(timeframe) in {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
    }
    if not symbol_timeframes:
        selected = str(saved.get("symbol") or "").upper()
        if selected in market_watch and selected in workflow.config.allowed_symbols:
            symbol_timeframes = {
                market_watch[selected]: str(saved.get("timeframe", "H1"))
            }
    symbols = list(symbol_timeframes)
    if not symbols:
        raise LiveOrderRejected(
            "symbol_not_whitelisted", "no whitelisted Market Watch symbols available"
        )
    config = TradingConfig(
        account_balance=float(account.get("balance", 0.0) or 0.0),
        risk_per_trade=float(saved.get("risk_per_trade", 1.0)) / 100.0,
        max_positions=int(saved.get("max_positions", 3)),
        daily_loss_limit=(
            float(saved.get("daily_loss_limit", workflow.config.max_daily_loss))
            / max(float(account.get("balance", 0.0) or 0.0), 1.0)
        ),
        min_signal_score=float(saved.get("min_confidence", 60)) / 100.0,
        symbols=symbols,
        timeframes=sorted(set(symbol_timeframes.values())),
        symbol_timeframes=symbol_timeframes,
        execution_mode="live",
        max_position_volume=float(workflow.config.max_position_volume),
        require_protection=True,
        break_even=BreakEvenConfig(
            enabled=bool(saved.get("break_even_enabled", True)),
            trigger_r=float(saved.get("break_even_trigger_r", 1.0)),
            entry_offset=float(saved.get("break_even_entry_offset", 0.0)),
        ),
        partial_close_enabled=bool(saved.get("partial_close_enabled", False)),
        partial_close_trigger_r=float(saved.get("partial_close_trigger_r", 1.5)),
        partial_close_percent=float(saved.get("partial_close_percent", 50.0)),
        sl_tp_mode=str(saved.get("sl_tp_mode", "signal")),
        stop_loss_value=float(saved.get("stop_loss_value", 0.0)),
        take_profit_value=float(saved.get("take_profit_value", 0.0)),
    )
    trader = AutoTrader(config)
    enabled = set(saved.get("enabled_strategies", ["TrendFollowing"]))
    if "TrendFollowing" in enabled:
        trader.add_strategy(TrendFollowingStrategy())
    if "MeanReversion" in enabled:
        trader.add_strategy(MeanReversionStrategy())
    if "SMC" in enabled:
        trader.add_strategy(SMCStrategy())
    return LiveTradingLoop(
        trader, workflow, mt5_connector, get_live_auto_market_payload
    )


def build_shadow_trading_loop() -> ShadowTradingLoop:
    """Construct a data-only trader using the same strategy settings as live mode."""
    if not mt5_connector or not mt5_connector.is_connected():
        raise LiveOrderRejected("mt5_disconnected", "MT5 market data is not connected")
    saved = st.session_state.get("saved_settings", {})
    market_watch = {
        str(item["name"]).upper(): str(item["name"])
        for item in get_market_watch_symbols()
        if item.get("name")
    }
    configured_pairs = saved.get("symbol_timeframes", {})
    if not isinstance(configured_pairs, dict):
        configured_pairs = {}
    symbol_timeframes = {
        market_watch[str(symbol).upper()]: str(timeframe)
        for symbol, timeframe in configured_pairs.items()
        if str(symbol).upper() in market_watch
        and str(timeframe) in {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
    }
    if not symbol_timeframes:
        selected = str(saved.get("symbol") or "").upper()
        if selected in market_watch:
            symbol_timeframes = {
                market_watch[selected]: str(saved.get("timeframe", "H1"))
            }
    if not symbol_timeframes:
        raise LiveOrderRejected(
            "no_market_data_symbols", "no visible symbols are configured"
        )
    account = get_real_account_data()
    trader = AutoTrader(
        TradingConfig(
            account_balance=float(account.get("balance", 0.0) or 0.0),
            risk_per_trade=float(saved.get("risk_per_trade", 1.0)) / 100.0,
            max_positions=int(saved.get("max_positions", 3)),
            daily_loss_limit=(
                float(saved.get("daily_loss_limit", 500.0))
                / max(float(account.get("balance", 0.0) or 0.0), 1.0)
            ),
            min_signal_score=float(saved.get("min_confidence", 60)) / 100.0,
            symbols=list(symbol_timeframes),
            timeframes=sorted(set(symbol_timeframes.values())),
            symbol_timeframes=symbol_timeframes,
            execution_mode="shadow",
            max_position_volume=float(saved.get("max_position_volume", 1.0)),
            require_protection=False,
            shadow_ledger_path=os.getenv(
                "SHADOW_LEDGER_PATH", "data/shadow_orders.jsonl"
            ),
        )
    )
    enabled = set(saved.get("enabled_strategies", ["TrendFollowing"]))
    if "TrendFollowing" in enabled:
        trader.add_strategy(TrendFollowingStrategy())
    if "MeanReversion" in enabled:
        trader.add_strategy(MeanReversionStrategy())
    if "SMC" in enabled:
        trader.add_strategy(SMCStrategy())
    return ShadowTradingLoop(trader, get_live_auto_market_payload)


if st.session_state.shadow_start_requested and not st.session_state.trading_active:
    try:
        loop = build_shadow_trading_loop()
        loop.start()
        st.session_state.live_trading_loop = loop
        st.session_state.trading_active = True
        st.session_state.shadow_start_requested = False
        _persist_shadow_trading_state(True)
        st.sidebar.success("Demo Shadow Mode enabled; no real orders can be sent.")
    except (LiveOrderRejected, TypeError, ValueError) as error:
        st.session_state.shadow_start_requested = False
        st.sidebar.error(f"Shadow mode blocked ({error})")


if (
    st.session_state.live_trading_loop is None
    and MODULES_AVAILABLE
    and bool(st.session_state.saved_settings.get("shadow_trading_enabled"))
):
    try:
        if mt5_connector and not mt5_connector.is_connected():
            mt5_connector.connect()
        loop = build_shadow_trading_loop()
        loop.start()
        st.session_state.live_trading_loop = loop
        st.session_state.trading_active = True
        st.sidebar.info("Demo Shadow Mode restored after dashboard refresh.")
    except (LiveOrderRejected, TypeError, ValueError) as error:
        logger.warning("Persisted shadow trading session was not restored: %s", error)
        st.session_state.trading_active = True
        st.sidebar.warning(f"Shadow mode is still enabled but unavailable ({error})")


if (
    st.session_state.trading_active
    and st.session_state.live_trading_loop is None
    and st.session_state.saved_settings.get("auto_trading_enabled")
):
    try:
        workflow = get_live_order_workflow()
        expires_at = float(
            st.session_state.saved_settings.get("auto_trading_expires_at", 0.0) or 0.0
        )
        if workflow is None or not LiveTradingLoop.enabled_by_server():
            raise LiveOrderRejected(
                "auto_trading_restore_blocked",
                "live trading safety gates are not active",
            )
        workflow.restore_automation_authorization(expires_at)
        loop = build_live_trading_loop()
        loop.restore_active()
        st.session_state.live_trading_loop = loop
    except (LiveOrderRejected, TypeError, ValueError) as error:
        logger.warning("Persisted auto trading session was not restored: %s", error)
        st.session_state.trading_active = False
        _persist_auto_trading_state(False)

if (
    st.session_state.auto_trading_confirm_requested
    and st.session_state.auto_trading_confirmation
):
    try:
        loop = st.session_state.live_trading_loop or build_live_trading_loop()
        loop.start(st.session_state.auto_trading_confirmation)
        st.session_state.live_trading_loop = loop
        st.session_state.trading_active = True
        _persist_auto_trading_state(
            True,
            time.time() + loop.workflow.config.automation_session_seconds,
        )
        st.session_state.auto_trading_confirmation = ""
        st.session_state.auto_trading_confirm_requested = False
        st.sidebar.success("✅ Auto trading enabled (risk-gated live loop).")
    except LiveOrderRejected as error:
        st.session_state.auto_trading_confirmation = ""
        st.session_state.auto_trading_confirm_requested = False
        st.sidebar.error(f"Auto trading blocked ({error.code}): {error}")
    except Exception as error:
        logger.exception("Auto trading start failed")
        st.session_state.auto_trading_confirmation = ""
        st.session_state.auto_trading_confirm_requested = False
        st.sidebar.error(f"Auto trading failed closed: {type(error).__name__}")


def _live_trading_fragment_interval(trading_active: bool) -> float | None:
    """Schedule live polling only while an explicitly authorized loop is active."""
    if not trading_active:
        return None
    return max(1.0, float(os.getenv("MT5_AUTO_TRADING_INTERVAL_SECONDS", "5")))


@st.fragment(
    run_every=_live_trading_fragment_interval(
        bool(st.session_state.get("trading_active", False))
    )
)
def _run_continuous_live_trading() -> None:
    """Process the selected symbol/timeframe continuously while automation is active."""
    if not st.session_state.trading_active or not st.session_state.live_trading_loop:
        return
    loop = st.session_state.live_trading_loop
    try:
        cycle = loop.run_once()
        if loop.last_status == "authorization_expired":
            st.session_state.trading_active = False
            _persist_auto_trading_state(False)
        if cycle is not None:
            st.session_state.live_signal_cycle = cycle
            st.session_state.live_signals = cycle.signal_details
            if cycle.errors:
                rejection_history = st.session_state.setdefault(
                    "live_order_rejections", []
                )
                rejection_history.extend(
                    {
                        "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                        "reason": localize_cycle_error(error),
                    }
                    for error in cycle.errors
                    if error
                )
                del rejection_history[:-20]
            st.sidebar.caption(
                f"{t('Live cycle')} "
                f"({loop.trader.config.symbols[0]} / {loop.trader.config.timeframes[0]}): "
                f"{t('signals')}={cycle.signals_generated}, "
                f"{t('orders')}={cycle.orders_executed}, "
                f"{t('opened')}={cycle.positions_opened}, "
                f"{t('errors')}={len(cycle.errors)}"
            )
            break_even_actions = getattr(loop, "last_break_even_actions", [])
            if break_even_actions:
                applied = sum(
                    action.get("status") == "applied" for action in break_even_actions
                )
                rejected = len(break_even_actions) - applied
                st.sidebar.caption(
                    f"{t('Break-even status')}: "
                    f"{t('Break-even applied')}={applied}, "
                    f"{t('Break-even rejected')}={rejected}"
                )
            if cycle.errors:
                localized_errors = "; ".join(
                    localize_cycle_error(error) for error in cycle.errors[:3]
                )
                st.sidebar.warning(f"{t('Live cycle errors')}: {localized_errors}")
        else:
            st.sidebar.caption(f"{t('Live cycle skipped')}: {loop.last_status}")
    except Exception:
        logger.exception("Automated trading cycle failed closed")
        loop.stop()
        st.session_state.trading_active = False
        _persist_auto_trading_state(False)


def _render_live_spread_monitor() -> None:
    """Render broker spreads and the latest live rejection without mock data."""
    if not mt5_connector or not mt5_connector.is_connected():
        return
    saved = st.session_state.get("saved_settings", {})
    pairs = saved.get("symbol_timeframes", {})
    symbols = list(pairs) if isinstance(pairs, dict) else []
    if not symbols and saved.get("symbol"):
        symbols = [str(saved["symbol"])]
    if not symbols:
        return
    limit = float(saved.get("max_spread", 0.0) or 0.0)
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_limit = limit
        spread: float | None = None
        spread_available = True
        try:
            info = mt5_connector.get_symbol_info(symbol) or {}
            spread = float(info.get("spread", 0.0) or 0.0)
            workflow = get_live_order_workflow()
            if workflow and workflow.config.spread_mode == "atr":
                symbol_limit = workflow.spread_limit(symbol)
        except LiveOrderRejected as error:
            spread_available = False
            symbol_limit = None
            logger.warning(
                "Spread monitor degraded for %s: %s (%s)",
                symbol,
                error.code,
                error,
            )
        except (TypeError, ValueError, AttributeError) as error:
            spread_available = False
            symbol_limit = None
            logger.warning(
                "Spread monitor received invalid data for %s: %s", symbol, error
            )
        spread_status = (
            t("Unavailable")
            if not spread_available
            else (
                t("Allowed")
                if symbol_limit <= 0 or 0 < spread <= symbol_limit
                else t("Blocked")
            )
        )
        rows.append(
            {
                t("Symbol"): symbol,
                t("Current spread"): spread if spread_available else "—",
                t("Spread limit"): (
                    symbol_limit
                    if symbol_limit is not None and symbol_limit > 0
                    else "—"
                ),
                t("Spread status"): spread_status,
            }
        )
    with st.sidebar.expander(f"📡 {t('Live spread monitor')}", expanded=True):
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        rejection_history = st.session_state.get("live_order_rejections", [])
        if rejection_history:
            latest = rejection_history[-1]
            st.caption(
                f"{t('Latest order rejection')} ({latest['time']}): {latest['reason']}"
            )
            st.dataframe(
                pd.DataFrame(rejection_history[-5:]),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption(
                f"{t('Latest order rejection')}: {t('No order rejection recorded')}"
            )


_run_continuous_live_trading()
_render_live_spread_monitor()


def _resolve_prediction_artifact(models_dir: Path, model_name: str) -> Path:
    """Resolve the newest artifact for a selected trained model type."""
    patterns = {
        "RandomForest": ("random_forest_latest.pkl", "random_forest_*.pkl"),
        "XGBoost": ("xgboost_latest.pkl", "xgboost_*.pkl"),
        "Ensemble": ("ensemble_latest.pkl", "ensemble_*.pkl"),
    }
    if model_name not in patterns:
        raise ValueError(f"Unsupported prediction model: {model_name}")
    preferred, fallback = patterns[model_name]
    preferred_path = models_dir / preferred
    if preferred_path.is_file():
        return preferred_path
    candidates = sorted(
        models_dir.glob(fallback),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No trained {model_name} artifact was found in {models_dir}"
        )
    return candidates[0]


def generate_market_predictions(
    symbols: List[str],
    timeframe: str,
    model_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate read-only predictions using a trained artifact and live MT5 candles."""
    if not mt5_connector or not mt5_connector.is_connected():
        raise RuntimeError("MT5 is not connected; predictions were not generated")

    models_dir = Path(PROJECT_ROOT) / "models"
    artifact_path = _resolve_prediction_artifact(models_dir, model_name)
    prediction_rows = []
    service = MLInferenceService(
        artifact_path,
        MLConfig(expected_timeframe=timeframe),
    )
    artifact_symbol = str(service.metadata.get("symbol", "")).strip()
    compatible_symbols = [
        symbol for symbol in symbols if not artifact_symbol or symbol == artifact_symbol
    ]
    if not compatible_symbols:
        raise RuntimeError(
            f"Model {model_name} is trained for "
            f"{artifact_symbol or 'an unspecified symbol'}, not the selected symbols"
        )

    for symbol in compatible_symbols:
        candles = mt5_connector.get_historical_candles(
            symbol, timeframe=timeframe, count=250
        )
        frame = pd.DataFrame(candles)
        if frame.empty:
            raise RuntimeError(f"No MT5 candles available for {symbol} {timeframe}")
        try:
            result = service.predict(frame, symbol=symbol)
        except (ValueError, ModelArtifactError) as error:
            raise RuntimeError(
                f"Prediction unavailable for {symbol} {timeframe}: {error}"
            ) from error
        price = float(frame["close"].iloc[-1])
        probabilities = result.probabilities
        expected_return = (probabilities["BUY"] - probabilities["SELL"]) * 100
        prediction_rows.append(
            {
                "Symbol": symbol,
                "Direction": result.direction,
                "Confidence": f"{result.confidence * 100:.1f}%",
                "Expected Return": f"{expected_return:+.2f}%",
                "Last Price": round(price, 5),
                "BUY Probability": f"{probabilities['BUY'] * 100:.1f}%",
                "HOLD Probability": f"{probabilities['HOLD'] * 100:.1f}%",
                "SELL Probability": f"{probabilities['SELL'] * 100:.1f}%",
                "Model": model_name,
                "Data": f"MT5 {timeframe} ({len(frame)} candles)",
            }
        )
    try:
        importance_values = service.feature_importance()
    except ModelArtifactError:
        importance_values = {}
    importance = pd.DataFrame(
        {
            "feature": list(importance_values),
            "importance": list(importance_values.values()),
        }
    ).sort_values("importance", ascending=False)
    return pd.DataFrame(prediction_rows), importance


def run_live_backtest(
    strategy_name: str,
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    initial_balance: float = 10000,
) -> Dict[str, Any]:
    """Run a read-only backtest using recent candles from the connected MT5 terminal."""
    if not MODULES_AVAILABLE:
        return {"error": "Modules not available"}
    if not mt5_connector or not mt5_connector.is_connected():
        return {"error": "MT5 is not connected; live backtest was not run"}

    try:
        timeframe = timeframe.upper()
        candles = mt5_connector.get_historical_candles(
            symbol,
            timeframe=timeframe,
            count=500,
        )
        if len(candles) < 100:
            return {
                "error": (
                    f"Insufficient live MT5 candles for "
                    f"{symbol} {timeframe}: {len(candles)}"
                )
            }
        market_data = {"symbol": symbol, "timeframe": timeframe, "candles": candles}

        # Initialize strategy
        if strategy_name == "TrendFollowing":
            strategy = TrendFollowingStrategy()
        elif strategy_name == "MeanReversion":
            strategy = MeanReversionStrategy()
        else:
            strategy = TrendFollowingStrategy()  # Default

        # Run backtest
        if initial_balance <= 0:
            return {"error": "Initial balance must be greater than zero"}
        engine = BacktestEngine(initial_balance=initial_balance)
        results = engine.run(strategy, market_data)
        result_data = results.to_dict()

        return {
            "success": True,
            "final_balance": result_data["end_balance"],
            "total_return": result_data["total_pnl_percent"],
            "win_rate": result_data["win_rate"],
            "max_drawdown": result_data["max_drawdown_percent"],
            "total_trades": result_data["total_trades"],
            "equity_curve": list(getattr(results, "equity_curve", [])),
            "data_source": (
                f"MT5 live history: {symbol} {timeframe} ({len(candles)} candles)"
            ),
        }
    except Exception as e:
        return {"error": str(e)}


# Main content based on navigation
if page == "Overview":
    st.title(f"📊 {t('Trading Overview Dashboard')}")

    # Connection status indicator
    if DASHBOARD_MODULES_AVAILABLE and mt5_connector:
        if mt5_connector.is_connected():
            st.sidebar.success(f"✅ {t('Connected to MT5')}")
        else:
            st.sidebar.warning(f"⚠️ {t('MT5 Not Connected - Live data unavailable')}")
            if st.sidebar.button(f"🔄 {t('Connect to MT5')}"):
                if mt5_connector.connect():
                    st.sidebar.success(t("Connected to MT5"))
                    st.rerun()
                else:
                    error = getattr(mt5_connector, "last_connection_error", None)
                    st.sidebar.error(
                        f"MT5 connection failed: {error or 'unknown error'}"
                    )
    else:
        st.sidebar.warning("⚠️ اتصال واقعی MT5 در دسترس نیست؛ داده‌ای نمایش داده نمی‌شود.")

    if mt5_connector and mt5_connector.is_connected():
        try:
            synced_files = _sync_selected_market_data()
            if synced_files:
                st.sidebar.info(
                    f"✅ دادهٔ {synced_files} نماد/تایم‌فریم در market_data ذخیره شد."
                )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logger.warning("Selected Market Watch data was not persisted: %s", error)
            st.sidebar.warning(
                "⚠️ دریافت دادهٔ Market Watch انجام نشد؛ دادهٔ زنده همچنان در دسترس است."
            )

    # Get REAL account data
    account_data = get_real_account_data()
    positions_df = get_real_positions_data()
    performance = get_real_performance_metrics()

    # Calculate deltas
    balance = float(account_data.get("balance", 0.0) or 0.0)
    equity = float(account_data.get("equity", 0.0) or 0.0)
    profit = float(account_data.get("profit", 0.0) or 0.0)
    profit_pct = (profit / balance * 100) if balance > 0 else 0
    connection_state = "connected" if account_data.get("connected") else "disconnected"
    connection_label = (
        "● اتصال زنده به MetaTrader 5 برقرار است"
        if connection_state == "connected"
        else "● اتصال به MetaTrader 5 برقرار نیست؛ داده‌ای نمایش داده نمی‌شود"
    )
    st.markdown(
        f"""
        <section class="smart-hero" aria-label="Smart trading overview">
            <div class="smart-hero-kicker">AI SMART TRADING // COMMAND CENTER</div>
            <div class="smart-hero-title">هوش مصنوعی، ریسک کنترل‌شده، تصمیم شفاف</div>
            <p class="smart-hero-copy">
                نمایی یکپارچه از سلامت حساب، سرمایه، عملکرد استراتژی‌ها و سیگنال‌های بازار؛
                همه‌چیز قبل از هر اقدام با گیت‌های ایمنی سیستم بررسی می‌شود.
            </p>
            <div class="smart-status smart-status--{connection_state}" role="status">
                {connection_label}
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    # Key metrics with REAL data
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            label=t("Total Balance"),
            value=f"${balance:,.2f}",
            delta=f"${profit:,.2f} ({profit_pct:.2f}%)",
            delta_color="normal" if profit >= 0 else "inverse",
        )

    with col2:
        st.metric(
            label=t("Equity"),
            value=f"${equity:,.2f}",
            delta=f"{profit_pct:.2f}%",
            delta_color="normal" if profit >= 0 else "inverse",
        )

    with col3:
        st.metric(
            label=t("Floating P&L"),
            value=f"${profit:,.2f}",
            delta=f"{profit_pct:.2f}%",
            delta_color="normal" if profit >= 0 else "inverse",
        )

    with col4:
        win_rate = float(performance.get("win_rate", 0.0) or 0.0)
        st.metric(
            label=t("Win Rate"),
            value=f"{win_rate:.1f}%",
            delta=f"{performance.get('total_trades', 0)} trades",
            delta_color="off",
        )

    with col5:
        active_positions = len(positions_df) if not positions_df.empty else 0
        st.metric(
            label=t("Active Positions"),
            value=str(active_positions),
            delta="-1" if active_positions > 0 else None,
            delta_color="inverse" if active_positions > 3 else "normal",
        )

    st.markdown("---")

    # Equity curve with REAL data
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"📈 {t('Equity Curve')}")
        equity_df = get_real_equity_curve(90)
        chart_data = pd.DataFrame(columns=["time", "equity"])
        if not equity_df.empty and "time" in equity_df.columns:
            chart_data = equity_df[["time", "equity"]].copy()
            chart_data["time"] = pd.to_datetime(chart_data["time"], utc=True)
            chart_data["equity"] = pd.to_numeric(chart_data["equity"], errors="coerce")
            chart_data = chart_data.dropna(subset=["time", "equity"])
        if not chart_data.empty:
            equity_values = chart_data["equity"]
            value_range = float(equity_values.max() - equity_values.min())
            padding = max(
                value_range * 0.15, abs(float(equity_values.mean())) * 0.001, 0.01
            )
            y_min = float(equity_values.min() - padding)
            y_max = float(equity_values.max() + padding)
            st.vega_lite_chart(
                chart_data,
                {
                    "height": 430,
                    "mark": {
                        "type": "line",
                        "point": {"filled": True, "size": 65},
                        "strokeWidth": 3,
                        "color": "#1976D2",
                    },
                    "encoding": {
                        "x": {
                            "field": "time",
                            "type": "temporal",
                            "title": t("UTC time"),
                            "axis": {"labelAngle": -35},
                        },
                        "y": {
                            "field": "equity",
                            "type": "quantitative",
                            "title": t("Account balance"),
                            "scale": {"domain": [y_min, y_max], "zero": False},
                            "axis": {
                                "tickCount": 8,
                                "format": ",.2f",
                                "grid": True,
                            },
                        },
                        "tooltip": [
                            {"field": "time", "type": "temporal", "title": "Time"},
                            {
                                "field": "equity",
                                "type": "quantitative",
                                "title": "Balance",
                                "format": ",.2f",
                            },
                        ],
                    },
                },
                use_container_width=True,
            )
            st.caption(
                f"{t('MT5 realized balance + current balance')} | "
                f"{len(equity_df)} data points"
            )
        elif mt5_connector and mt5_connector.is_connected():
            st.info(t("No MT5 balance history is available for this period."))
        else:
            st.warning(t("MT5 is disconnected; live account chart is unavailable."))

    with col2:
        st.subheader(f"📋 {t('Recent Signals')}")
        if positions_df.empty:
            st.info(t("No live strategy signals are available."))
        for _, position in positions_df.head(5).iterrows():
            direction = str(position.get("Type", "UNKNOWN"))
            color = (
                "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "🟡"
            )
            symbol = position.get("symbol", position.get("Symbol", "UNKNOWN"))
            pnl = float(position.get("profit", position.get("Profit", 0)) or 0)
            st.markdown(f"{color} **{symbol}** - {direction} ({pnl:+.2f})")
            st.caption(t("Source: MT5 open position"))
            st.divider()

    st.subheader(f"📡 {t('Live Signals')}")
    live_cycle = st.session_state.get("live_signal_cycle")
    active_symbol_timeframes = _active_symbol_timeframes()
    configured_symbols = {symbol.upper() for symbol in active_symbol_timeframes}
    if active_symbol_timeframes:
        st.caption(
            f"{t('Active symbol/timeframe selections')}: "
            + ", ".join(
                f"{symbol} / {timeframe}"
                for symbol, timeframe in active_symbol_timeframes.items()
            )
        )
    live_details = [
        detail
        for detail in st.session_state.get("live_signals", [])
        if not configured_symbols
        or str(detail.get("symbol", "")).upper() in configured_symbols
    ]
    live_scores = getattr(live_cycle, "scores", []) if live_cycle else []
    score_by_key = {
        (score.signal.symbol, score.signal.strategy_name or "Unknown"): score
        for score in live_scores
    }
    signal_rows = []
    for detail in live_details:
        score = score_by_key.get((detail.get("symbol"), detail.get("strategy")))
        row = {
            t("Generated at"): detail.get("timestamp", ""),
            t("Symbol"): detail.get("symbol", ""),
            t("Direction"): detail.get("direction", ""),
            t("Order Type"): detail.get("order_type", "MARKET"),
            t("Strategy"): detail.get("strategy", ""),
            t("Strength"): detail.get("strength", 0.0),
            t("Score"): round(float(score.total_score), 3) if score else None,
            t("Recommendation"): score.recommendation if score else "HOLD",
            t("Status"): t("Passed scoring") if score else t("Detected"),
            t("Entry Price"): detail.get("entry_price"),
            t("Stop Loss"): detail.get("stop_loss"),
            t("Take Profit"): detail.get("take_profit"),
            t("Reason"): detail.get("reason") or t("Signal Details"),
        }
        signal_rows.append(row)
    if signal_rows:
        st.dataframe(
            pd.DataFrame(signal_rows), use_container_width=True, hide_index=True
        )
        st.caption(f"{t('Signal Details')}: {len(signal_rows)} {t('signals detected')}")
    else:
        st.info(t("No live signals have been detected yet."))

    st.subheader(f"📌 {t('Open Orders / Positions (MT5)')}")
    if not positions_df.empty:
        open_orders = positions_df.copy().rename(
            columns={
                "ticket": "Ticket",
                "symbol": "Symbol",
                "Type": "Type",
                "volume": "Volume",
                "price_open": "Entry Price",
                "price_current": "Current Price",
                "sl": "Stop Loss",
                "tp": "Take Profit",
                "profit": "P&L",
                "swap": "Swap",
                "time": "Open Time",
            }
        )
        visible_columns = [
            "Ticket",
            "Symbol",
            "Type",
            t("Volume"),
            "Entry Price",
            "Current Price",
            "Stop Loss",
            "Take Profit",
            "P&L",
            "Swap",
            "Open Time",
        ]
        open_orders = open_orders.reindex(
            columns=[column for column in visible_columns if column in open_orders]
        )
        st.dataframe(
            open_orders,
            column_config={
                "Volume": st.column_config.NumberColumn("Volume", format="%.2f"),
                "Entry Price": st.column_config.NumberColumn(
                    "Entry Price", format="%.5f"
                ),
                "Current Price": st.column_config.NumberColumn(
                    "Current Price", format="%.5f"
                ),
                "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="%.5f"),
                "Take Profit": st.column_config.NumberColumn(
                    "Take Profit", format="%.5f"
                ),
                "P&L": st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
                "Swap": st.column_config.NumberColumn("Swap ($)", format="$%.2f"),
                "Open Time": st.column_config.DatetimeColumn("Open Time"),
            },
            use_container_width=True,
            hide_index=True,
        )
    elif mt5_connector and mt5_connector.is_connected():
        st.info(t("MT5 reports no open orders or positions for this account."))
    else:
        st.warning(t("MT5 is disconnected; open orders cannot be read."))

    st.subheader(f"⏳ {t('Pending Limit Orders (MT5)')}")
    pending_orders = get_real_orders_data()
    if not pending_orders.empty:
        order_type_names = {
            2: "BUY LIMIT",
            3: "SELL LIMIT",
            4: "BUY STOP",
            5: "SELL STOP",
            6: "BUY STOP LIMIT",
            7: "SELL STOP LIMIT",
        }
        pending_display = pending_orders.copy().rename(
            columns={
                "ticket": "Ticket",
                "symbol": "Symbol",
                "type": "Order Type",
                "volume_initial": "Volume",
                "price_open": "Entry Price",
                "price_current": "Current Price",
                "sl": "Stop Loss",
                "tp": "Take Profit",
                "time_setup": "Placed At",
                "time_expiration": "Expiration",
            }
        )
        pending_display["Order Type"] = pending_display["Order Type"].map(
            lambda value: order_type_names.get(int(value), f"TYPE {value}")
        )
        pending_display = pending_display[
            [
                "Ticket",
                "Symbol",
                "Order Type",
                "Volume",
                "Entry Price",
                "Current Price",
                "Stop Loss",
                "Take Profit",
                "Placed At",
                "Expiration",
            ]
        ]
        st.dataframe(
            pending_display,
            column_config={
                "Volume": st.column_config.NumberColumn("Volume", format="%.2f"),
                "Entry Price": st.column_config.NumberColumn(
                    "Entry Price", format="%.5f"
                ),
                "Current Price": st.column_config.NumberColumn(
                    "Current Price", format="%.5f"
                ),
                "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="%.5f"),
                "Take Profit": st.column_config.NumberColumn(
                    "Take Profit", format="%.5f"
                ),
                "Placed At": st.column_config.DatetimeColumn("Placed At"),
                "Expiration": st.column_config.DatetimeColumn("Expiration"),
            },
            use_container_width=True,
            hide_index=True,
        )
    elif mt5_connector and mt5_connector.is_connected():
        st.info(t("MT5 reports no pending limit or stop orders."))
    else:
        st.warning(t("MT5 is disconnected; pending orders cannot be read."))

    # Recent trades table with REAL data
    st.subheader(f"📜 {t('Recent Trades')}")
    history_df = get_real_history_data(30)

    if not history_df.empty:
        display_df = history_df.sort_values("time", ascending=False).head(10)
        st.dataframe(
            display_df,
            column_config={
                "time": st.column_config.DatetimeColumn("Time"),
                "entry_time": st.column_config.DatetimeColumn("Entry Time"),
                "exit_time": st.column_config.DatetimeColumn("Exit Time"),
                "profit": st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
            },
            use_container_width=True,
            hide_index=True,
        )
    elif DASHBOARD_MODULES_AVAILABLE and mt5_connector and mt5_connector.is_connected():
        st.info(
            "📭 No executed trades were returned by MT5 for the selected period."
            if language == "English"
            else "📭 در بازهٔ انتخاب‌شده هیچ معاملهٔ اجراشده‌ای از MT5 دریافت نشد."
        )
    else:
        st.warning(t("MT5 is disconnected; recent trades are unavailable."))

elif page == "Strategies":
    st.title(f"🧠 {t('Strategy Performance')}")
    saved = st.session_state.saved_settings

    strategies = ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"]

    market_watch_symbols = get_market_watch_symbols()
    symbol_names = [item["name"] for item in market_watch_symbols if item.get("name")]
    symbol_labels = {
        item["name"]: f"{item['name']} — {item.get('description', '')}".strip(" —")
        for item in market_watch_symbols
        if item.get("name")
    }

    st.subheader(f"📡 {t('Selected Market Watch Data')}")
    if not symbol_names:
        st.error(t("Market Watch is empty"))
        st.info(
            "در MetaTrader 5 نماد موردنظر را به Market Watch اضافه کنید و صفحه را تازه‌سازی کنید."
            if language == "فارسی"
            else "Add a symbol to the MetaTrader 5 Market Watch and refresh this page."
        )
        st.stop()

    selection_col, timeframe_col = st.columns(2)
    with selection_col:
        selected_symbol = st.selectbox(
            t("Select Symbol"),
            symbol_names,
            index=symbol_names.index(saved["symbol"])
            if saved.get("symbol") in symbol_names
            else 0,
            format_func=lambda name: symbol_labels.get(name, name),
            key="strategy_symbol",
        )
    with timeframe_col:
        selected_timeframe = st.selectbox(
            t("Select Timeframe"),
            ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"],
            index=(
                ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"].index(
                    saved["timeframe"]
                )
                if saved.get("timeframe")
                in ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
                else 4
            ),
            key="strategy_timeframe",
        )

    selected_quote = next(
        (item for item in market_watch_symbols if item.get("name") == selected_symbol),
        None,
    )
    if selected_quote:
        st.caption(
            f"{selected_symbol} | {selected_timeframe} | "
            f"Bid: {selected_quote.get('bid', 0)} | "
            f"Ask: {selected_quote.get('ask', 0)}"
        )

    # Interactive strategy selector
    selected_strategy = st.selectbox(
        t("Select Strategy to Analyze"),
        strategies,
        index=(
            strategies.index(saved["strategy"])
            if saved.get("strategy") in strategies
            else 0
        ),
        key="strategy_analysis",
    )
    _persist_dashboard_preference("strategy", selected_strategy)

    for strategy in strategies:
        with st.expander(
            f"📊 {strategy} Strategy", expanded=(strategy == selected_strategy)
        ):
            strategy_metrics = get_real_performance_metrics()
            equity_df = get_real_equity_curve(30)
            if not mt5_connector or not mt5_connector.is_connected():
                st.warning("MT5 متصل نیست؛ عملکرد واقعی استراتژی قابل نمایش نیست.")
            elif equity_df.empty:
                st.info(
                    "برای این حساب Demo در ۳۰ روز اخیر دادهٔ عملکردی از MT5 وجود ندارد."
                )
            else:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Win Rate", f"{strategy_metrics.get('win_rate', 0):.1f}%")
                col2.metric(
                    "Profit Factor", f"{strategy_metrics.get('profit_factor', 0):.2f}"
                )
                col3.metric(
                    "Total Trades", f"{strategy_metrics.get('total_trades', 0)}"
                )
                col4.metric(
                    "Net P&L", f"${strategy_metrics.get('total_profit', 0):,.2f}"
                )
                st.caption(
                    "این شاخص‌ها از معاملات واقعی MT5 هستند و به تفکیک استراتژی در تاریخچهٔ بروکر ثبت نشده‌اند."
                )
                chart = equity_df.copy()
                chart["time"] = pd.to_datetime(chart["time"], utc=True)
                chart["equity"] = pd.to_numeric(chart["equity"], errors="coerce")
                chart = chart.dropna(subset=["time", "equity"])
                if chart.empty:
                    st.info("برای این حساب Demo دادهٔ معتبر منحنی سرمایه وجود ندارد.")
                else:
                    st.line_chart(chart.set_index("time")["equity"])

            # Interactive backtest button
            if st.button(
                f"🔬 {t('Run Live Backtest')} - {strategy}", key=f"bt_{strategy}"
            ):
                with st.spinner(t("Running backtest...")):
                    result = run_live_backtest(
                        strategy,
                        symbol=selected_symbol,
                        timeframe=selected_timeframe,
                    )
                    if "error" not in result:
                        st.success(f"✅ {t('Backtest Complete!')}")
                        col_a, col_b, col_c = st.columns(3)
                        col_a.metric(
                            t("Final Balance"), f"${result['final_balance']:.2f}"
                        )
                        col_b.metric(
                            t("Total Return"), f"{result['total_return']:.2f}%"
                        )
                        col_c.metric(t("Win Rate"), f"{result['win_rate']:.1f}%")
                        st.caption(result["data_source"])
                    else:
                        st.error(f"{t('Backtest failed')}: {result['error']}")

elif page == "Backtest Results":
    st.title(f"🔬 {t('Backtest Analysis')}")
    saved = st.session_state.saved_settings

    # Interactive controls
    market_watch_symbols = get_market_watch_symbols()
    symbol_names = [item["name"] for item in market_watch_symbols if item.get("name")]
    symbol_labels = {
        item["name"]: f"{item['name']} — {item.get('description', '')}".strip(" —")
        for item in market_watch_symbols
        if item.get("name")
    }
    if not symbol_names:
        st.error(t("Market Watch is empty"))
        st.info(
            "در MetaTrader 5 نماد موردنظر را به Market Watch اضافه کنید و صفحه را تازه‌سازی کنید."
            if language == "فارسی"
            else "Add a symbol to the MetaTrader 5 Market Watch and refresh this page."
        )
        st.stop()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        selected_strategy = st.selectbox(
            t("Strategy"),
            ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"],
            index=(
                ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"].index(
                    saved["enabled_strategies"][0]
                )
                if saved.get("enabled_strategies")
                and saved["enabled_strategies"][0]
                in ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"]
                else 0
            ),
            key="backtest_strategy",
        )
    with col2:
        initial_balance = st.number_input(
            t("Initial Balance ($)"),
            1000,
            100000,
            int(saved.get("backtest_initial_balance", 10000)),
            1000,
            key="backtest_initial_balance",
        )
    with col3:
        days_to_test = st.slider(
            t("Days to Test"),
            30,
            365,
            int(saved.get("backtest_days", 180)),
            key="backtest_days",
        )
    with col4:
        selected_symbol = st.selectbox(
            t("Select Symbol"),
            symbol_names,
            index=symbol_names.index(saved["symbol"])
            if saved.get("symbol") in symbol_names
            else 0,
            format_func=lambda name: symbol_labels.get(name, name),
            key="backtest_symbol",
        )
    with col5:
        selected_timeframe = st.selectbox(
            t("Select Timeframe"),
            ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"],
            index=(
                ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"].index(
                    saved["timeframe"]
                )
                if saved.get("timeframe")
                in ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
                else 4
            ),
            key="backtest_timeframe",
        )
    _persist_dashboard_preference("backtest_initial_balance", initial_balance)
    _persist_dashboard_preference("backtest_days", days_to_test)

    # Run backtest button
    if st.button(f"🚀 {t('Run Backtest Now')}", type="primary"):
        with st.spinner(f"Running backtest for {selected_strategy}..."):
            result = run_live_backtest(
                selected_strategy,
                symbol=selected_symbol,
                timeframe=selected_timeframe,
                initial_balance=initial_balance,
            )
            if "error" not in result:
                st.success(f"✅ {t('Backtest Complete!')}")

                # Display metrics
                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("Total Return", f"{result['total_return']:.2f}%")
                col_b.metric("Max Drawdown", f"{result['max_drawdown']:.2f}%")
                col_c.metric("Win Rate", f"{result['win_rate']:.1f}%")
                col_d.metric("Total Trades", f"{result['total_trades']}")
                st.caption(result["data_source"])

                # Equity curve
                st.subheader("📈 Equity Curve")
                equity_curve = result.get("equity_curve", [])
                if equity_curve:
                    equity_df = pd.DataFrame({"equity": equity_curve})
                    st.area_chart(equity_df)
                else:
                    st.info("منحنی سرمایه از موتور بک‌تست دریافت نشد.")

                # Trade distribution
                col_x, col_y = st.columns(2)
                with col_x:
                    st.subheader("Win/Loss Distribution")
                    win_count = int(result["total_trades"] * result["win_rate"] / 100)
                    loss_count = result["total_trades"] - win_count
                    win_loss = pd.DataFrame(
                        {"Result": ["Wins", "Losses"], "Count": [win_count, loss_count]}
                    )
                    st.bar_chart(win_loss.set_index("Result"))

                with col_y:
                    st.subheader("Monthly Returns")
                    history = get_real_history_data(days_to_test)
                    if history.empty:
                        st.info(
                            "برای توزیع ماهانه، معاملهٔ واقعی از MT5 در این بازه وجود ندارد."
                        )
                    else:
                        history = history.copy()
                        history["time"] = pd.to_datetime(history["time"], utc=True)
                        monthly = (
                            history.assign(
                                month=history["time"].dt.to_period("M").astype(str)
                            )
                            .groupby("month")["profit"]
                            .sum()
                            .to_frame("return")
                        )
                        st.bar_chart(monthly)
            else:
                st.error(f"❌ Backtest failed: {result['error']}")
    else:
        st.info(
            "👆 برای تحلیل، بک‌تست را اجرا کنید. "
            "معیارها فقط پس از دریافت دادهٔ واقعی MT5 و اجرای موتور بک‌تست نمایش داده می‌شوند."
        )

elif page == "ML Predictions":
    st.title(f"🤖 {t('AI/ML Predictions')}")
    saved = st.session_state.saved_settings

    # Add ML Training Status Section
    st.subheader("📚 وضعیت آموزش مدل‌های یادگیری ماشین")

    # Check for trained models
    models_dir = Path(PROJECT_ROOT) / "models"
    trained_models = []
    if models_dir.exists():
        for model_file in models_dir.glob("*.pkl"):
            if not model_file.name.startswith("."):
                trained_models.append(model_file)

    model_artifacts = _list_model_artifacts(models_dir)
    gate_status = _latest_training_gate_status(models_dir)
    gate_bars = int(gate_status.get("bars") or 0)
    gate_coverage_hours = float(gate_status.get("coverage_hours") or 0.0)
    if gate_status["status"] == "accepted":
        st.success(
            "✅ Training Gate: آماده | "
            f"{gate_status.get('symbol', 'N/A')} {gate_status.get('timeframe', 'N/A')} | "
            f"{gate_bars:,} کندل | "
            f"{gate_coverage_hours:.1f} ساعت"
        )
    elif gate_status["status"] == "rejected":
        st.error(
            "⛔ Training Gate: رد شد | "
            f"{gate_status.get('symbol', 'N/A')} {gate_status.get('timeframe', 'N/A')} | "
            f"{gate_bars:,} کندل | "
            f"{gate_status.get('error', 'دادهٔ کافی یا معتبر نیست')}"
        )
    elif gate_status["status"] == "unavailable":
        st.warning("⚠️ وضعیت Training Gate قابل خواندن نیست.")
    else:
        st.info("ℹ️ هنوز گزارش قابل‌اعتمادی از Training Gate ثبت نشده است.")

    if model_artifacts:
        with st.expander("🗑️ مدیریت و حذف مدل‌های ذخیره‌شده", expanded=False):
            st.warning(
                "حذف دائمی است و فقط فایل‌های انتخاب‌شده از پوشه models حذف می‌شوند."
            )
            artifact_names = [artifact.name for artifact in model_artifacts]
            selected_artifacts = st.multiselect(
                "مدل‌ها و اطلاعات آموزشی برای حذف",
                artifact_names,
                format_func=lambda name: (
                    f"{name} "
                    f"({next(item.stat().st_size for item in model_artifacts if item.name == name) / 1024:.1f} KB)"
                ),
                key="selected_model_artifacts_for_deletion",
            )
            confirm_deletion = st.checkbox(
                "حذف فایل‌های انتخاب‌شده را تأیید می‌کنم",
                key="confirm_model_artifact_deletion",
            )
            if st.button(
                "🗑️ حذف موارد انتخاب‌شده",
                type="secondary",
                disabled=not selected_artifacts,
                key="delete_selected_model_artifacts",
            ):
                if not confirm_deletion:
                    st.error("برای حذف، ابتدا تأیید حذف را فعال کنید.")
                else:
                    try:
                        deleted_names = _delete_model_artifacts(
                            models_dir, selected_artifacts
                        )
                    except (FileNotFoundError, ValueError, OSError) as error:
                        logger.exception("Model artifact deletion failed")
                        st.error(f"حذف مدل‌ها انجام نشد: {error}")
                    else:
                        st.success(f"{len(deleted_names)} مورد حذف شد.")
                        st.session_state.pop(
                            "selected_model_artifacts_for_deletion", None
                        )
                        st.session_state.pop("confirm_model_artifact_deletion", None)
                        st.rerun()

    if trained_models:
        st.success(f"✅ {len(trained_models)} مدل آموزش‌دیده یافت شد")

        # Display model information
        model_info_col1, model_info_col2 = st.columns(2)
        with model_info_col1:
            st.markdown("### مدل‌های موجود:")
            for model_file in trained_models[:5]:  # Show first 5 models
                st.code(model_file.name, language="text")

        with model_info_col2:
            # Try to load training summary if available
            summary_files = list(models_dir.glob("training_summary_*.json"))
            if summary_files:
                latest_summary = max(summary_files, key=lambda x: x.stat().st_mtime)
                try:
                    with open(latest_summary, "r") as f:
                        summary_data = json.load(f)
                    summary_metrics = summary_data.get("metrics", {})
                    summary_model = summary_data.get(
                        "model_type", summary_data.get("model", "Unknown")
                    )
                    if (
                        summary_model == "Unknown"
                        and isinstance(summary_metrics, dict)
                        and len(summary_metrics) == 1
                    ):
                        summary_model = next(iter(summary_metrics))
                    if isinstance(summary_metrics, dict) and isinstance(
                        summary_metrics.get(summary_model), dict
                    ):
                        summary_metrics = summary_metrics[summary_model]
                    if not isinstance(summary_metrics, dict):
                        summary_metrics = {}
                    st.markdown("### خلاصه آخرین آموزش:")
                    st.json(
                        {
                            "model": summary_model,
                            "accuracy": f"{summary_metrics.get('accuracy', summary_data.get('test_accuracy', 0)):.2%}",
                            "f1_macro": f"{summary_metrics.get('f1_macro', 0):.2%}",
                            "profit_factor": summary_metrics.get(
                                "profit_factor",
                                summary_data.get("profit_factor", "N/A"),
                            ),
                            "trained_at": summary_data.get(
                                "trained_at",
                                summary_data.get("training_date", "Unknown"),
                            ),
                        }
                    )
                except Exception:
                    st.info("جزئیات آموزش در دسترس نیست")
            else:
                st.info("فایل خلاصه آموزش یافت نشد")

        # Show feature importance from training
        importance_files = list(models_dir.glob("*feature_importance_*.csv"))
        if importance_files and "latest_feature_importance" not in st.session_state:
            latest_importance = max(importance_files, key=lambda x: x.stat().st_mtime)
            try:
                importance_df = pd.read_csv(latest_importance)
                if {"feature", "importance"}.issubset(importance_df.columns):
                    importance_df["importance"] = pd.to_numeric(
                        importance_df["importance"], errors="coerce"
                    )
                    importance_df = importance_df.dropna(
                        subset=["feature", "importance"]
                    )
                if not importance_df.empty and {"feature", "importance"}.issubset(
                    importance_df.columns
                ):
                    st.markdown("### اهمیت ویژگی‌ها (از آموزش):")
                    st.bar_chart(importance_df.set_index("feature").head(10))
                else:
                    st.info("دادهٔ معتبر اهمیت ویژگی در دسترس نیست.")
            except Exception:
                pass
    else:
        st.warning(
            "⚠️ هیچ مدل آموزش‌دیده‌ای یافت نشد. برای آموزش مدل به بخش راهنما مراجعه کنید."
        )
        st.info("""
        **برای آموزش مدل:**
        1. از ترمینال دستور زیر را اجرا کنید:
           ```bash
           python scripts/train_model.py --symbol XAUUSD_l --timeframe M5 --years 2
           ```
        2. یا از فایل پیکربندی استفاده کنید:
           ```bash
           python scripts/train_model.py --config scripts/ml_training_config.json
           ```
        """)

    st.divider()

    st.subheader("🎛️ کنترل آموزش و بازآموزی")
    # The terminal's Market Watch is the source of truth for every symbol
    # selector; market_data only determines whether training can run.
    market_watch_symbols = get_market_watch_symbols()
    market_watch_names = [
        item["name"] for item in market_watch_symbols if item.get("name")
    ]
    market_watch_labels = {
        item["name"]: f"{item['name']} — {item.get('description', '')}".strip(" —")
        for item in market_watch_symbols
        if item.get("name")
    }
    operation_symbols = _get_ml_operation_symbols(
        market_watch_symbols, Path(PROJECT_ROOT) / "market_data"
    )
    if not operation_symbols:
        st.error(t("Market Watch is empty"))
        st.info(
            "در MetaTrader 5 نماد موردنظر را به Market Watch اضافه کنید و صفحه را تازه‌سازی کنید."
            if language == "فارسی"
            else "Add a symbol to the MetaTrader 5 Market Watch or collect local candles, then refresh this page."
        )
        st.stop()
    if not market_watch_names:
        st.info(
            "MT5 در دسترس نیست؛ نمادهای فایل‌های محلی برای آموزش استفاده می‌شوند."
            if language == "فارسی"
            else "MT5 is unavailable; local candle files are being used for training."
        )

    # Remove symbols that were selected in an earlier rerun but are no longer
    # visible in the terminal, then default to all currently visible symbols.
    if "ml_operation_symbol" in st.session_state and (
        st.session_state.ml_operation_symbol not in operation_symbols
    ):
        st.session_state.ml_operation_symbol = operation_symbols[0]
    if "prediction_symbols" in st.session_state:
        st.session_state.prediction_symbols = [
            name
            for name in st.session_state.prediction_symbols
            if name in market_watch_names
        ]
    operation_symbol = st.selectbox(
        "نماد عملیات ML",
        operation_symbols,
        format_func=lambda name: market_watch_labels.get(name, name),
        key="ml_operation_symbol",
    )
    operation_timeframe = st.selectbox(
        "تایم‌فریم عملیات ML",
        ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"],
        index=4,
        key="ml_operation_timeframe",
    )
    operation_col1, operation_col2, operation_col3 = st.columns(3)
    with operation_col1:
        st.session_state.ml_model_type = st.selectbox(
            "نوع مدل آموزش",
            ["RandomForest", "XGBoost", "Ensemble"],
            index=["RandomForest", "XGBoost", "Ensemble"].index(
                st.session_state.ml_model_type
            ),
            key="ml_model_type_select",
        )
        training_years = st.number_input(
            "سال‌های داده", min_value=1, max_value=20, value=2
        )
    with operation_col2:
        if st.button(
            "▶️ شروع آموزش اولیه", disabled=_ml_job_running(), use_container_width=True
        ):
            try:
                _sync_selected_market_data(
                    [(operation_symbol, operation_timeframe, DEFAULT_HISTORY_COUNT)],
                    force=True,
                )
                _start_ml_process(
                    "training",
                    operation_symbol,
                    operation_timeframe,
                    int(training_years),
                )
                st.success("آموزش در پس‌زمینه شروع شد.")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"شروع آموزش ناموفق بود: {error}")
        if st.button(
            "🔁 بازآموزی اجباری", disabled=_ml_job_running(), use_container_width=True
        ):
            try:
                _start_ml_process(
                    "retraining",
                    operation_symbol,
                    operation_timeframe,
                    int(training_years),
                )
                st.success("بازآموزی در پس‌زمینه شروع شد.")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"شروع بازآموزی ناموفق بود: {error}")
    with operation_col3:
        schedule = st.selectbox(
            "برنامه بازآموزی", ["daily", "weekly", "monthly"], key="ml_schedule"
        )
        if st.button(
            "✅ فعال‌سازی بازآموزی خودکار",
            disabled=_ml_job_running() or _ml_scheduler_running(),
            use_container_width=True,
        ):
            try:
                _start_ml_scheduler(operation_symbol, operation_timeframe, schedule)
                _persist_dashboard_preference("ml_training_enabled", True)
                _persist_dashboard_preference("retrain_schedule", schedule)
                st.success("بازآموزی خودکار فعال شد.")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"فعال‌سازی ناموفق بود: {error}")
        if st.button(
            "⏹️ توقف عملیات ML",
            disabled=not (_ml_job_running() or _ml_scheduler_running()),
            use_container_width=True,
        ):
            _stop_ml_process()
            _persist_ml_scheduler_state(False)
            st.warning("فرایند ML این نشست متوقف شد.")

    if st.session_state.get("ml_process") is not None:
        status = (
            "در حال اجرا"
            if _ml_job_running()
            else f"پایان‌یافته (کد {st.session_state.ml_last_return_code})"
        )
        st.info(
            f"وضعیت: {status} | نوع: {st.session_state.ml_job_kind} | "
            f"شروع: {st.session_state.get('ml_job_started_at', '-')}"
        )
        log_tail = _ml_log_tail()
        if log_tail:
            with st.expander("آخرین لاگ عملیات ML", expanded=not _ml_job_running()):
                st.code(log_tail, language="text")
    st.caption(
        "اجرای هم‌زمان آموزش و بازآموزی مجاز نیست؛ هیچ سفارش معاملاتی از این کنترل‌ها ارسال نمی‌شود."
    )

    st.divider()

    symbol_names = market_watch_names
    symbol_labels = market_watch_labels

    prediction_col, timeframe_col = st.columns(2)
    with prediction_col:
        st.multiselect(
            "نمادهای Market Watch" if language == "فارسی" else "Market Watch Symbols",
            symbol_names,
            default=symbol_names,
            format_func=lambda name: symbol_labels.get(name, name),
            key="prediction_symbols",
        )
    with timeframe_col:
        prediction_timeframe = st.selectbox(
            t("Select Timeframe"),
            ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"],
            index=(
                ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"].index(
                    saved["timeframe"]
                )
                if saved.get("timeframe")
                in ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
                else 4
            ),
            key="prediction_timeframe",
        )
    selected_prediction_symbols = st.session_state.get(
        "prediction_symbols", symbol_names
    )
    st.caption(
        f"Market Watch: {len(selected_prediction_symbols)} symbol(s) | "
        f"Timeframe: {prediction_timeframe}"
    )

    # Model selection with info about trained models
    models = ["RandomForest", "XGBoost", "Ensemble"]
    trained_model_types: set[str] = set()

    # Show which models have been trained
    if trained_models:
        for model_file in trained_models:
            if "random_forest" in model_file.name.lower():
                trained_model_types.add("RandomForest")
            elif (
                "xgboost" in model_file.name.lower() or "xgb" in model_file.name.lower()
            ):
                trained_model_types.add("XGBoost")
            elif "ensemble" in model_file.name.lower():
                trained_model_types.add("Ensemble")

        if trained_model_types:
            st.info(f"✅ مدل‌های آموزش‌دیده: {', '.join(trained_model_types)}")

    preferred_model = saved.get("prediction_model", saved["ml_model"])
    if preferred_model not in trained_model_types:
        preferred_model = next(
            (model for model in models if model in trained_model_types),
            preferred_model,
        )
    if (
        trained_model_types
        and st.session_state.get("prediction_model") not in trained_model_types
    ):
        st.session_state["prediction_model"] = preferred_model
    selected_model = st.selectbox(
        t("Model"),
        models,
        index=models.index(preferred_model) if preferred_model in models else 2,
        key="prediction_model",
    )
    _persist_dashboard_preference("prediction_model", selected_model)
    _persist_dashboard_preference("prediction_timeframe", prediction_timeframe)
    _persist_dashboard_preference("prediction_symbols", selected_prediction_symbols)

    # Generate predictions button
    if st.button(f"🔄 {t('Generate New Predictions')}", type="primary"):
        if not selected_prediction_symbols:
            st.warning(
                "حداقل یک نماد از Market Watch را انتخاب کنید."
                if language == "فارسی"
                else "Select at least one Market Watch symbol."
            )
        else:
            with st.spinner(t("Generating predictions...")):
                try:
                    pred_df, feature_importance = generate_market_predictions(
                        list(selected_prediction_symbols),
                        prediction_timeframe,
                        selected_model,
                    )
                except (FileNotFoundError, RuntimeError, ValueError) as error:
                    st.error(f"{t('Prediction failed')}: {error}")
                else:
                    st.session_state.latest_predictions = pred_df
                    st.session_state.latest_feature_importance = feature_importance
                    st.session_state.latest_predictions_context = (
                        f"MT5 Market Watch: {prediction_timeframe} | "
                        f"{len(selected_prediction_symbols)} symbol(s) | "
                        f"model: {selected_model}"
                    )
                    st.session_state.latest_predictions_context_key = (
                        tuple(selected_prediction_symbols),
                        prediction_timeframe,
                        selected_model,
                    )

    # Display predictions
    st.subheader(t("Current Predictions"))
    has_current_predictions = (
        "latest_predictions" in st.session_state
        and st.session_state.get("latest_predictions_context_key")
        == (tuple(selected_prediction_symbols), prediction_timeframe, selected_model)
    )
    if has_current_predictions:
        pred_df = st.session_state.latest_predictions
    else:
        pred_df = pd.DataFrame()
    if "latest_predictions_context" in st.session_state:
        st.caption(st.session_state.latest_predictions_context)

    # Color-code the dataframe
    def color_direction(val):
        if val == "BUY":
            return "background-color: #d4edda; color: #155724"
        elif val == "SELL":
            return "background-color: #f8d7da; color: #721c24"
        return ""

    # Use map() instead of applymap() for pandas >= 1.4.0 compatibility
    if pred_df.empty:
        st.info(
            "هنوز پیش‌بینی معتبر تولید نشده است. ابتدا مدل آموزش‌دیده و دادهٔ واقعی MT5 "
            "را انتخاب کنید و دکمهٔ تولید پیش‌بینی را بزنید."
        )
    else:
        styled_df = pred_df.style.map(color_direction, subset=["Direction"])
        st.dataframe(styled_df, use_container_width=True, hide_index=True)

    # Feature importance with interactive chart
    st.subheader(f"📊 {t('Feature Importance')}")
    features = st.session_state.get("latest_feature_importance")
    if features is None or features.empty:
        st.info("اهمیت ویژگی معتبر از artifact مدل فعلی در دسترس نیست.")
    else:
        st.bar_chart(features.set_index("feature").head(20))

    # Model comparison
    st.subheader(f"📈 {t('Model Performance Comparison')}")
    metric_rows = []
    for summary_path in sorted(
        models_dir.glob("training_summary_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        model_type = summary.get("model_type") or summary.get("model")
        metrics = summary.get("metrics", summary)
        if not model_type or not isinstance(metrics, dict):
            continue
        metric_rows.append(
            {
                "Model": str(model_type),
                "Accuracy": metrics.get("test_accuracy", metrics.get("accuracy")),
                "Precision": metrics.get("test_precision", metrics.get("precision")),
                "Recall": metrics.get("test_recall", metrics.get("recall")),
            }
        )
    if metric_rows:
        st.dataframe(
            pd.DataFrame(metric_rows).drop_duplicates("Model"),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("برای مقایسه، خلاصهٔ معتبر آموزش مدل‌ها در models/ یافت نشد.")

    # Add A/B Testing Results Section
    st.divider()
    st.subheader("🔬 نتایج آزمون A/B (با و بدون ML)")

    backtest_results_dir = Path(PROJECT_ROOT) / "backtest_results"
    if backtest_results_dir.exists():
        comparison_files = list(backtest_results_dir.glob("backtest_comparison_*.json"))
        if comparison_files:
            latest_comparison = max(comparison_files, key=lambda x: x.stat().st_mtime)
            try:
                with open(latest_comparison, "r") as f:
                    comparison_data = json.load(f)

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "بهبود Win Rate",
                        f"{comparison_data.get('win_rate_improvement', 0):+.1f}%",
                        help="تفاوت نرخ برد با و بدون ML",
                    )

                with col2:
                    st.metric(
                        "بهبود Profit Factor",
                        f"{comparison_data.get('profit_factor_improvement', 0):+.2f}",
                        help="تفاوت ضریب سود با و بدون ML",
                    )

                with col3:
                    st.metric(
                        "کاهش Max Drawdown",
                        f"{comparison_data.get('drawdown_reduction', 0):+.1f}%",
                        help="کاهش حداکثر افت سرمایه با ML",
                    )

                st.json(comparison_data)
            except Exception as e:
                st.info(f"نتایج آزمون A/B یافت نشد: {e}")
        else:
            st.info(
                "هنوز آزمون A/B انجام نشده است. از scripts/backtest_comparison.py استفاده کنید."
            )
    else:
        st.info("پوشه backtest_results یافت نشد")

elif page == "Live Trading":
    st.title(f"⚡ {t('Live Trading Monitor')}")

    @_dashboard_fragment
    def render_live_account():
        """Refresh account, connection and open-position data periodically."""
        account = get_real_account_data()
        st.session_state.live_account = account
        connection_label = "ACTIVE" if account.get("connected") else "DISCONNECTED"
        if account.get("connected"):
            st.success("📡 MT5 Connection: ACTIVE")
        else:
            st.error("📡 MT5 Connection: DISCONNECTED — live values unavailable")

        account_columns = st.columns(5)
        account_columns[0].metric(
            t("Balance"),
            _format_account_value(
                account.get("balance"), f" {account.get('currency', '')}"
            ),
        )
        account_columns[1].metric(
            t("Equity"),
            _format_account_value(
                account.get("equity"), f" {account.get('currency', '')}"
            ),
        )
        account_columns[2].metric(
            t("Floating P&L"),
            _format_account_value(
                account.get("profit"), f" {account.get('currency', '')}"
            ),
        )
        account_columns[3].metric(
            t("Free Margin"), _format_account_value(account.get("margin_free"))
        )
        account_columns[4].metric(
            t("Margin Level"),
            _format_account_value(account.get("margin_level"), "%"),
        )
        st.caption(
            f"Account: {account.get('login', '-')}"
            f" | Server: {account.get('server', '-')}"
            f" | Status: {connection_label}"
            f" | Last update: {account.get('last_update', '-')}"
        )

        st.subheader(t("Active Positions"))
        live_positions = get_real_positions_data()
        positions_df = live_positions.rename(
            columns={
                "symbol": "Symbol",
                "Type": "Type",
                "volume": "Volume",
                "price_open": "Entry",
                "price_current": "Current",
                "profit": "P&L ($)",
                "sl": "SL",
                "tp": "TP",
                "ticket": "Ticket",
                "time": "Open Time",
            }
        )
        columns = [
            "Ticket",
            "Symbol",
            "Type",
            "Volume",
            "Entry",
            "Current",
            "P&L ($)",
            "SL",
            "TP",
            "Open Time",
        ]
        positions_df = positions_df.reindex(columns=columns)
        st.session_state.live_positions_df = positions_df
        position_count = len(positions_df)
        floating_pnl = float(positions_df["P&L ($)"].sum()) if position_count else 0.0
        summary_columns = st.columns(2)
        summary_columns[0].metric(t("Open Positions"), position_count)
        summary_columns[1].metric(t("Positions P&L"), f"{floating_pnl:,.2f}")
        if positions_df.empty:
            st.info("No open positions are currently reported by MT5.")
        else:

            def color_pnl(value):
                if isinstance(value, (int, float)):
                    return (
                        "color: green"
                        if value > 0
                        else ("color: red" if value < 0 else "")
                    )
                return ""

            st.dataframe(
                positions_df.style.map(color_pnl, subset=["P&L ($)"]),
                hide_index=True,
                use_container_width=True,
            )

        st.subheader(f"📡 {t('Live Signals')}")
        active_symbol_timeframes = _active_symbol_timeframes()
        configured_symbols = {symbol.upper() for symbol in active_symbol_timeframes}
        if active_symbol_timeframes:
            st.caption(
                f"{t('Active symbol/timeframe selections')}: "
                + ", ".join(
                    f"{symbol} / {timeframe}"
                    for symbol, timeframe in active_symbol_timeframes.items()
                )
            )
        live_signal_rows = [
            detail
            for detail in st.session_state.get("live_signals", [])
            if not configured_symbols
            or str(detail.get("symbol", "")).upper() in configured_symbols
        ]
        live_cycle = st.session_state.get("live_signal_cycle")
        live_scores = getattr(live_cycle, "scores", []) if live_cycle else []
        score_by_key = {
            (score.signal.symbol, score.signal.strategy_name or "Unknown"): score
            for score in live_scores
        }
        signal_table = []
        for detail in live_signal_rows:
            score = score_by_key.get((detail.get("symbol"), detail.get("strategy")))
            signal_table.append(
                {
                    t("Generated at"): detail.get("timestamp", ""),
                    t("Symbol"): detail.get("symbol", ""),
                    t("Direction"): detail.get("direction", ""),
                    t("Order Type"): detail.get("order_type", "MARKET"),
                    t("Strategy"): detail.get("strategy", ""),
                    t("Strength"): detail.get("strength", 0.0),
                    t("Score"): round(float(score.total_score), 3) if score else None,
                    t("Status"): t("Passed scoring") if score else t("Detected"),
                    t("Reason"): detail.get("reason") or t("Signal Details"),
                }
            )
        if signal_table:
            st.dataframe(
                pd.DataFrame(signal_table),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info(t("No live signals have been detected yet."))

    render_live_account()
    account = st.session_state.get("live_account")
    if account is None:
        account = get_real_account_data()

    # Quick actions
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        workflow = get_live_order_workflow()
        watch_symbols = get_market_watch_symbols()
        symbol_options = [item["name"] for item in watch_symbols if item.get("name")]
        with st.expander(t("Manual Live Order (risk-gated)"), expanded=False):
            if workflow is None or not account.get("connected"):
                st.info(
                    t(
                        "Live order workflow is unavailable until MT5 is connected and configured."
                    )
                )
            elif not symbol_options:
                st.warning(t("No visible Market Watch symbols are available."))
            else:
                order_symbol = st.selectbox(
                    t("Symbol"), symbol_options, key="live_order_symbol"
                )
                order_mode = st.selectbox(
                    t("Order type"),
                    ["Market BUY", "Market SELL", "BUY LIMIT", "SELL LIMIT"],
                    key="live_order_mode",
                )
                max_volume = float(workflow.config.max_position_volume)
                minimum_volume = min(0.01, max_volume)
                order_volume = st.number_input(
                    t("Volume"),
                    min_value=minimum_volume,
                    max_value=max_volume,
                    value=minimum_volume,
                    step=minimum_volume,
                    key="live_order_volume",
                )
                pending_price = 0.0
                stop_loss = 0.0
                take_profit = 0.0
                live_price = 0.0
                if order_mode.startswith("Market "):
                    live_tick = mt5_connector.symbol_info_tick(order_symbol)
                    if live_tick is None:
                        st.error(
                            f"{t('No live quote is available for')} {order_symbol}."
                        )
                        st.stop()
                    order_direction = order_mode.replace("Market ", "")
                    live_price = float(
                        live_tick.ask if order_direction == "BUY" else live_tick.bid
                    )
                    st.caption(f"{t('Live execution price')}: {live_price:g}")
                    stop_loss = st.number_input(
                        t("Stop Loss (optional; 0 = none)"),
                        min_value=0.0,
                        value=0.0,
                        format="%.5f",
                        key="live_market_sl",
                    )
                    take_profit = st.number_input(
                        t("Take Profit (optional; 0 = none)"),
                        min_value=0.0,
                        value=0.0,
                        format="%.5f",
                        key="live_market_tp",
                    )
                if order_mode.endswith("LIMIT"):
                    pending_price = st.number_input(
                        t("Limit price"),
                        min_value=0.00001,
                        value=0.00001,
                        format="%.5f",
                        key="live_pending_price",
                    )
                    stop_loss = st.number_input(
                        t("Stop Loss (optional; 0 = none)"),
                        min_value=0.0,
                        value=0.0,
                        format="%.5f",
                        key="live_pending_sl",
                    )
                    take_profit = st.number_input(
                        t("Take Profit (optional; 0 = none)"),
                        min_value=0.0,
                        value=0.0,
                        format="%.5f",
                        key="live_pending_tp",
                    )
                if st.button(t("Request Order Confirmation"), type="secondary"):
                    is_pending = order_mode.endswith("LIMIT")
                    order_direction = order_mode.replace("Market ", "")
                    reference_price = pending_price if is_pending else live_price
                    if order_direction == "BUY" and (
                        (stop_loss and stop_loss >= reference_price)
                        or (take_profit and take_profit <= reference_price)
                    ):
                        st.error(
                            t(
                                "For BUY orders, SL must be below and TP above the entry price."
                            )
                        )
                        st.stop()
                    if order_direction == "SELL" and (
                        (stop_loss and stop_loss <= reference_price)
                        or (take_profit and take_profit >= reference_price)
                    ):
                        st.error(
                            t(
                                "For SELL orders, SL must be above and TP below the entry price."
                            )
                        )
                        st.stop()
                    action = (
                        f"pending:{order_symbol.upper()}:{order_direction.replace(' ', '_')}:"
                        f"{order_volume:g}:{pending_price:g}"
                        if is_pending
                        else (
                            f"open:{order_symbol.upper()}:{order_direction}:{order_volume:g}:"
                            f"{stop_loss:g}:{take_profit:g}"
                        )
                    )
                    st.session_state.live_confirmation_token = (
                        workflow.request_confirmation(action)
                    )
                    target_state = {
                        "symbol": order_symbol,
                        "direction": order_direction,
                        "volume": order_volume,
                    }
                    target_state["stop_loss"] = stop_loss
                    target_state["take_profit"] = take_profit
                    if is_pending:
                        target_state["price"] = pending_price
                    st.session_state.pending_live_order = target_state
                    st.warning(
                        t(
                            "A one-time confirmation code was created. It expires in 120 seconds."
                        )
                    )
                pending_order = st.session_state.pending_live_order
                if pending_order and st.session_state.live_confirmation_token:
                    pending_label = f"{pending_order['direction']} {pending_order['volume']:g} {pending_order['symbol']}"
                    if "price" in pending_order:
                        pending_label += f" @ {pending_order['price']:g}"
                        if pending_order.get("stop_loss"):
                            pending_label += f" | SL {pending_order['stop_loss']:g}"
                        if pending_order.get("take_profit"):
                            pending_label += f" | TP {pending_order['take_profit']:g}"
                    st.caption(f"Pending: {pending_label}")
                    st.code(
                        st.session_state.live_confirmation_token,
                        language=None,
                    )
                    st.caption(
                        "The one-time code is filled automatically. "
                        "It expires in 120 seconds."
                    )
                    # Keep the confirmation token synchronized with the current
                    # request so manual copy/paste cannot introduce an error.
                    st.session_state.live_order_code = (
                        st.session_state.live_confirmation_token
                    )
                    order_code = st.text_input(
                        "Confirmation code",
                        type="password",
                        key="live_order_code",
                        disabled=True,
                    )
                    if st.button("Execute Confirmed Order", type="primary"):
                        try:
                            # Use the server-side token directly; the disabled
                            # display field is informational and must not be an
                            # independent source of truth.
                            confirmation_token = (
                                st.session_state.live_confirmation_token
                            )
                            if "price" in pending_order:
                                result = workflow.execute_pending_order(
                                    pending_order["symbol"],
                                    pending_order["direction"],
                                    pending_order["volume"],
                                    pending_order["price"],
                                    stop_loss=pending_order.get("stop_loss", 0.0),
                                    take_profit=pending_order.get("take_profit", 0.0),
                                    confirmation_token=confirmation_token,
                                )
                            else:
                                result = workflow.execute_market_order(
                                    pending_order["symbol"],
                                    pending_order["direction"],
                                    pending_order["volume"],
                                    stop_loss=pending_order.get("stop_loss", 0.0),
                                    take_profit=pending_order.get("take_profit", 0.0),
                                    confirmation_token=confirmation_token,
                                )
                            st.session_state.live_confirmation_token = ""
                            st.session_state.pending_live_order = None
                            st.success(
                                f"Order accepted by MT5 (retcode {getattr(result, 'retcode', 'unknown')})."
                            )
                        except LiveOrderRejected as error:
                            st.session_state.live_confirmation_token = ""
                            st.session_state.pending_live_order = None
                            st.error(
                                f"{t('Order blocked')} ({error.code}): {localize_cycle_error(str(error))}"
                            )
                        except Exception as error:
                            st.session_state.live_confirmation_token = ""
                            st.session_state.pending_live_order = None
                            logger.exception("Unexpected MT5 order error")
                            st.error(
                                f"{t('MT5 order failed before completion; no retry was attempted.')} "
                                f"{type(error).__name__}: {localize_cycle_error(str(error))}"
                            )
        if st.button(
            "🔒 Request Close-All Confirmation",
            type="secondary",
            disabled=workflow is None or not account.get("connected"),
            help="This only creates a one-time confirmation code; it never sends an order.",
        ):
            st.session_state.live_confirmation_token = workflow.request_confirmation(
                "close_all"
            )
            st.warning(
                t("Confirmation code created. Enter it below and press Execute.")
            )
        if st.session_state.live_confirmation_token:
            st.session_state.live_confirmation_input = (
                st.session_state.live_confirmation_token
            )
            entered = st.text_input(
                "Confirmation code (filled automatically)",
                type="password",
                key="live_confirmation_input",
                disabled=True,
            )
            if st.button(
                "⚠️ Execute Confirmed Close-All",
                type="primary",
                disabled=workflow is None or not account.get("connected"),
            ):
                try:
                    results = workflow.close_all_positions(entered)
                    st.session_state.live_confirmation_token = ""
                    st.success(
                        f"{t('Close request accepted for')} {len(results)} موقعیت مدیریت‌شده."
                    )
                except LiveOrderRejected as error:
                    st.error(
                        f"{t('Order blocked')} ({error.code}): {localize_cycle_error(str(error))}"
                    )
                except Exception as error:
                    logger.exception("Unexpected MT5 close-order error")
                    st.error(
                        f"{t('MT5 close-order failed; no retry was attempted.')} "
                        f"{type(error).__name__}: {localize_cycle_error(str(error))}"
                    )
        else:
            st.caption(
                t(
                    "No live order is sent until a confirmation code is requested and entered."
                )
            )
    with col_b:
        if st.button("📊 Export Positions", type="secondary"):
            positions_df = st.session_state.get(
                "live_positions_df",
                pd.DataFrame(
                    columns=[
                        "Ticket",
                        "Symbol",
                        "Type",
                        "Volume",
                        "Entry",
                        "Current",
                        "P&L ($)",
                        "SL",
                        "TP",
                        "Open Time",
                    ]
                ),
            )
            st.download_button(
                label="Download CSV",
                data=positions_df.to_csv(index=False),
                file_name=f"positions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
    with col_c:
        if st.button("🔄 Refresh Data", type="secondary"):
            if data_manager:
                data_manager.refresh_all()
            st.rerun()

    st.markdown("---")

    @_dashboard_fragment
    def render_live_history():
        """Refresh recent MT5 deals without polling MT5 every second."""
        st.subheader(f"📜 {t('Recent MT5 Activity')}")
        history = get_real_history_data(days=1)
        if history.empty:
            st.info("No MT5 activity reported in the last 24 hours.")
        else:
            display_columns = [
                column
                for column in [
                    "time",
                    "symbol",
                    "direction",
                    "volume",
                    "profit",
                    "status",
                ]
                if column in history.columns
            ]
            st.dataframe(
                history[display_columns], hide_index=True, use_container_width=True
            )

    render_live_history()

elif page == "Settings":
    st.title(f"⚙️ {t('Settings')}")
    saved = st.session_state.saved_settings
    watch_symbols = get_market_watch_symbols()
    symbol_options = [
        str(item.get("name")) for item in watch_symbols if item.get("name")
    ]
    if not symbol_options:
        st.error(
            "MT5 Market Watch is unavailable. Settings cannot be applied without a visible symbol."
        )
    else:
        saved_pairs = saved.get("symbol_timeframes", {})
        if not isinstance(saved_pairs, dict):
            saved_pairs = {}
        legacy_symbol = saved.get("symbol")
        if not saved_pairs and legacy_symbol in symbol_options:
            saved_pairs = {legacy_symbol: saved.get("timeframe", "H1")}
        st.info(f"Connected Market Watch: {len(symbol_options)} visible symbols")
        risk_per_trade = st.slider(
            t("Risk per Trade (%)"),
            0.1,
            5.0,
            float(saved["risk_per_trade"]),
            0.1,
            key="settings_risk",
        )
        max_positions = st.number_input(
            t("Max Concurrent Positions"),
            1,
            10,
            int(saved["max_positions"]),
            key="settings_positions",
        )
        daily_loss_limit = st.number_input(
            t("Daily Loss Limit ($)"),
            min_value=1.0,
            max_value=5000.0,
            value=max(1.0, min(5000.0, float(saved["daily_loss_limit"]))),
            step=1.0,
            format="%.2f",
            help="Stop opening new trades when the daily loss reaches this amount.",
            key="settings_daily_loss",
        )
        spread_filter_enabled = st.checkbox(
            t("Enable spread filter"),
            value=bool(saved.get("spread_filter_enabled", False)),
            key="settings_spread_filter_enabled",
        )
        spread_modes = {
            t("Fixed spread limit"): "fixed",
            t("Dynamic ATR spread limit"): "atr",
        }
        saved_spread_mode = str(saved.get("spread_mode", "fixed"))
        spread_mode_label = st.selectbox(
            t("Spread limit mode"),
            list(spread_modes),
            index=list(spread_modes.values()).index(saved_spread_mode)
            if saved_spread_mode in spread_modes.values()
            else 0,
            disabled=not spread_filter_enabled,
            key="settings_spread_mode",
        )
        spread_mode = spread_modes[spread_mode_label]
        max_spread = st.number_input(
            t("Maximum spread"),
            min_value=0.00001,
            max_value=1000.0,
            value=max(0.00001, float(saved.get("max_spread", 0.0) or 0.00001)),
            step=0.00001,
            format="%.5f",
            disabled=not spread_filter_enabled or spread_mode == "atr",
            help="Reject live orders when the broker-reported ask-bid spread exceeds this price distance.",
            key="settings_max_spread",
        )
        spread_atr_period = st.number_input(
            t("ATR period for spread"),
            min_value=1,
            max_value=200,
            value=max(1, min(200, int(saved.get("spread_atr_period", 14)))),
            step=1,
            disabled=not spread_filter_enabled or spread_mode != "atr",
            key="settings_spread_atr_period",
        )
        spread_atr_multiplier = st.number_input(
            t("ATR spread multiplier"),
            min_value=0.01,
            max_value=20.0,
            value=max(0.01, min(20.0, float(saved.get("spread_atr_multiplier", 1.0)))),
            step=0.05,
            format="%.2f",
            disabled=not spread_filter_enabled or spread_mode != "atr",
            key="settings_spread_atr_multiplier",
        )
        spread_atr_timeframe = st.selectbox(
            t("ATR spread timeframe"),
            ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
            index=["M1", "M5", "M15", "M30", "H1", "H4", "D1"].index(
                str(saved.get("spread_atr_timeframe", "H1"))
            )
            if str(saved.get("spread_atr_timeframe", "H1"))
            in ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
            else 4,
            disabled=not spread_filter_enabled or spread_mode != "atr",
            key="settings_spread_atr_timeframe",
        )
        reset_at = float(saved.get("daily_loss_reset_at", 0.0) or 0.0)
        if reset_at > 0:
            reset_label = (
                datetime.fromtimestamp(reset_at, tz=timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S")
            )
            st.caption(f"{t('Daily loss starts from')}: {reset_label}")
        if st.button(
            f"🔄 {t('Reset Daily Loss')}",
            type="secondary",
            key="settings_reset_daily_loss",
        ):
            reset_at = datetime.now(timezone.utc).timestamp()
            updated_settings = dict(st.session_state.saved_settings)
            updated_settings["daily_loss_reset_at"] = reset_at
            st.session_state.saved_settings = updated_settings
            _persist_saved_settings(updated_settings)
            workflow = st.session_state.get("_live_order_workflow")
            if workflow is not None:
                workflow.reset_daily_loss(reset_at)
            live_loop = st.session_state.get("live_trading_loop")
            if live_loop is not None and live_loop.workflow is not workflow:
                live_loop.workflow.reset_daily_loss(reset_at)
            st.success(f"✅ {t('Daily loss calculation reset successfully.')}")
            st.rerun()
        max_position_volume = st.number_input(
            t("Maximum Position Volume (lots)"),
            min_value=0.01,
            max_value=100.0,
            value=max(
                0.01,
                min(100.0, float(saved.get("max_position_volume", 1.0))),
            ),
            step=0.01,
            format="%.2f",
            help="Hard upper limit for every live position.",
            key="settings_max_position_volume",
        )
        break_even_enabled = st.checkbox(
            t("Enable break-even"),
            value=bool(saved.get("break_even_enabled", True)),
            key="settings_break_even_enabled",
        )
        break_even_trigger_r = st.number_input(
            t("Break-even trigger (R)"),
            min_value=0.1,
            max_value=10.0,
            value=max(0.1, min(10.0, float(saved.get("break_even_trigger_r", 1.0)))),
            step=0.1,
            format="%.1f",
            key="settings_break_even_trigger",
        )
        break_even_entry_offset = st.number_input(
            t("Break-even entry offset"),
            min_value=0.0,
            max_value=100000.0,
            value=max(0.0, float(saved.get("break_even_entry_offset", 0.0))),
            step=0.01,
            format="%.5f",
            key="settings_break_even_offset",
        )
        partial_close_enabled = st.checkbox(
            t("Enable partial position close"),
            value=bool(saved.get("partial_close_enabled", False)),
            key="settings_partial_close_enabled",
        )
        partial_close_trigger_r = st.number_input(
            t("Partial close trigger (R)"),
            min_value=0.1,
            max_value=20.0,
            value=max(0.1, min(20.0, float(saved.get("partial_close_trigger_r", 1.5)))),
            step=0.1,
            format="%.1f",
            key="settings_partial_close_trigger",
        )
        partial_close_percent = st.slider(
            t("Partial close volume (%)"),
            min_value=1,
            max_value=99,
            value=max(1, min(99, int(saved.get("partial_close_percent", 50)))),
            key="settings_partial_close_percent",
        )
        sl_tp_modes = {
            t("Use strategy values"): "signal",
            t("Fixed distance"): "fixed",
            t("Percentage of entry"): "percent",
        }
        saved_sl_tp_mode = str(saved.get("sl_tp_mode", "signal"))
        sl_tp_mode_label = st.selectbox(
            t("SL/TP source"),
            list(sl_tp_modes),
            index=list(sl_tp_modes.values()).index(saved_sl_tp_mode)
            if saved_sl_tp_mode in sl_tp_modes.values()
            else 0,
            key="settings_sl_tp_mode",
        )
        sl_tp_mode = sl_tp_modes[sl_tp_mode_label]
        stop_loss_value = st.number_input(
            t("Stop loss distance"),
            min_value=0.0,
            max_value=100000.0,
            value=max(0.0, float(saved.get("stop_loss_value", 0.0))),
            step=0.1 if sl_tp_mode == "percent" else 0.00001,
            format="%.2f" if sl_tp_mode == "percent" else "%.5f",
            disabled=sl_tp_mode == "signal",
            key="settings_stop_loss_value",
        )
        take_profit_value = st.number_input(
            t("Take profit distance"),
            min_value=0.0,
            max_value=100000.0,
            value=max(0.0, float(saved.get("take_profit_value", 0.0))),
            step=0.1 if sl_tp_mode == "percent" else 0.00001,
            format="%.2f" if sl_tp_mode == "percent" else "%.5f",
            disabled=sl_tp_mode == "signal",
            key="settings_take_profit_value",
        )
        max_drawdown = st.slider(
            t("Max Drawdown (%)"),
            1.0,
            30.0,
            float(saved["max_drawdown"]),
            1.0,
            key="settings_drawdown",
        )
        enabled_strategies = st.multiselect(
            t("Enable Strategies"),
            ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"],
            default=[
                item
                for item in saved["enabled_strategies"]
                if item in ["TrendFollowing", "MeanReversion", "SMC", "Ensemble"]
            ],
            key="settings_strategies",
        )
        ml_models = ["RandomForest", "XGBoost", "Ensemble"]
        ml_model = st.selectbox(
            t("Primary ML Model"),
            ml_models,
            index=ml_models.index(saved["ml_model"])
            if saved["ml_model"] in ml_models
            else 2,
            key="settings_ml",
        )

        # ML Training Settings Section
        st.subheader("🎯 تنظیمات آموزش و بازآموزی مدل یادگیری ماشین")

        col_ml1, col_ml2 = st.columns(2)
        with col_ml1:
            ml_training_enabled = st.checkbox(
                "فعال‌سازی بازآموزی خودکار مدل",
                value=bool(saved.get("ml_training_enabled", False)),
                key="settings_ml_training_enabled",
                help="اگر فعال شود، مدل به‌طور دوره‌ای با داده‌های جدید بازآموزی می‌شود",
            )

            retrain_schedule_options = ["daily", "weekly", "monthly", "manual"]
            retrain_schedule = st.selectbox(
                "برنامه بازآموزی",
                retrain_schedule_options,
                index=retrain_schedule_options.index(
                    str(saved.get("retrain_schedule", "weekly"))
                )
                if str(saved.get("retrain_schedule", "weekly"))
                in retrain_schedule_options
                else 1,
                disabled=not ml_training_enabled,
                key="settings_retrain_schedule",
            )

        with col_ml2:
            ml_accuracy_threshold = st.number_input(
                "حداقل دقت مدل (%)",
                min_value=50.0,
                max_value=99.0,
                value=float(saved.get("ml_accuracy_threshold", 65.0)),
                step=1.0,
                format="%.1f",
                disabled=not ml_training_enabled,
                key="settings_ml_accuracy_threshold",
                help="اگر دقت مدل زیر این مقدار باشد، بازآموزی انجام می‌شود",
            )

            ml_profit_factor_threshold = st.number_input(
                "حداقل Profit Factor",
                min_value=0.5,
                max_value=5.0,
                value=float(saved.get("ml_profit_factor_threshold", 1.2)),
                step=0.1,
                format="%.2f",
                disabled=not ml_training_enabled,
                key="settings_ml_profit_factor_threshold",
                help="اگر profit factor مدل زیر این مقدار باشد، بازآموزی انجام می‌شود",
            )

        # Show last training info
        models_dir = Path(PROJECT_ROOT) / "models"
        if models_dir.exists():
            retrain_log_file = models_dir / "retraining_log.json"
            if retrain_log_file.exists():
                try:
                    with open(retrain_log_file, "r") as f:
                        retrain_log = json.load(f)
                    if retrain_log.get("last_retraining"):
                        st.info(f"""
                        **آخرین بازآموزی:**
                        - زمان: {retrain_log["last_retraining"].get("timestamp", "نامشخص")}
                        - مدل: {retrain_log["last_retraining"].get("model_type", "نامشخص")}
                        - دقت: {retrain_log["last_retraining"].get("test_accuracy", "نامشخص")}
                        """)
                except Exception:
                    pass

        min_confidence = st.slider(
            t("Minimum Confidence Threshold (%)"),
            30,
            90,
            int(saved["min_confidence"]),
            5,
            key="settings_confidence",
        )
        st.caption(
            t(
                "Database credentials and broker paths remain managed through environment variables."
            )
        )
        timeframe_options = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        selected_symbols = st.multiselect(
            t("Select symbols from Market Watch"),
            symbol_options,
            default=[symbol for symbol in saved_pairs if symbol in symbol_options],
            key="settings_symbols",
        )
        settings_pair_timeframes = {}
        for selected in selected_symbols:
            previous = str(saved_pairs.get(selected, saved.get("timeframe", "H1")))
            settings_pair_timeframes[selected] = st.selectbox(
                f"{t('Timeframe for')} {selected}",
                timeframe_options,
                index=timeframe_options.index(previous)
                if previous in timeframe_options
                else 4,
                key=f"settings_timeframe_{selected}",
            )
        if not selected_symbols:
            st.warning(t("At least one symbol must be selected."))
        trading_hours = st.time_input(
            t("Trading Start Time (optional)"),
            value=saved.get("trading_start"),
            key="settings_start",
        )
        col_x, col_y, col_z = st.columns([1, 1, 2])
        with col_x:
            if st.button(
                f"💾 {t('Save Settings')}",
                type="primary",
                use_container_width=True,
                key="settings_save",
            ):
                if not enabled_strategies:
                    st.error(t("At least one strategy must be enabled before saving."))
                elif not settings_pair_timeframes:
                    st.error(t("At least one symbol must be selected."))
                else:
                    current = st.session_state.saved_settings
                    st.session_state.saved_settings = {
                        "risk_per_trade": risk_per_trade,
                        "max_positions": max_positions,
                        "daily_loss_limit": daily_loss_limit,
                        "max_drawdown": max_drawdown,
                        "spread_filter_enabled": spread_filter_enabled,
                        "max_spread": max_spread if spread_filter_enabled else 0.0,
                        "spread_mode": spread_mode
                        if spread_filter_enabled
                        else "fixed",
                        "spread_atr_period": int(spread_atr_period),
                        "spread_atr_multiplier": float(spread_atr_multiplier),
                        "spread_atr_timeframe": spread_atr_timeframe,
                        "daily_loss_reset_at": float(
                            current.get("daily_loss_reset_at", 0.0) or 0.0
                        ),
                        "max_position_volume": max_position_volume,
                        "break_even_enabled": break_even_enabled,
                        "break_even_trigger_r": break_even_trigger_r,
                        "break_even_entry_offset": break_even_entry_offset,
                        "partial_close_enabled": partial_close_enabled,
                        "partial_close_trigger_r": partial_close_trigger_r,
                        "partial_close_percent": partial_close_percent,
                        "sl_tp_mode": sl_tp_mode,
                        "stop_loss_value": stop_loss_value,
                        "take_profit_value": take_profit_value,
                        "enabled_strategies": enabled_strategies,
                        "ml_model": ml_model,
                        "min_confidence": min_confidence,
                        "symbol": selected_symbols[0],
                        "timeframe": settings_pair_timeframes[selected_symbols[0]],
                        "symbol_timeframes": settings_pair_timeframes,
                        "trading_start": trading_hours,
                        "auto_trading_enabled": bool(
                            current.get("auto_trading_enabled", False)
                        ),
                        "auto_trading_expires_at": float(
                            current.get("auto_trading_expires_at", 0.0) or 0.0
                        ),
                        # ML Training Settings
                        "ml_training_enabled": ml_training_enabled,
                        "retrain_schedule": retrain_schedule
                        if ml_training_enabled
                        else "manual",
                        "ml_accuracy_threshold": ml_accuracy_threshold,
                        "ml_profit_factor_threshold": ml_profit_factor_threshold,
                    }
                    _persist_saved_settings(st.session_state.saved_settings)
                    st.session_state.pop("_live_order_workflow", None)
                    _reset_live_signal_view()
                    st.success(
                        f"✅ {t('Settings saved and applied to this dashboard session.')}"
                    )
        with col_y:
            if st.button(
                f"🔄 {t('Reset Defaults')}",
                type="secondary",
                use_container_width=True,
                key="settings_reset",
            ):
                st.session_state.saved_settings = DEFAULT_SETTINGS.copy()
                _persist_saved_settings(st.session_state.saved_settings)
                st.session_state.pop("_live_order_workflow", None)
                _reset_live_signal_view()
                st.rerun()
        with col_z:
            if st.button(
                f"🔌 {t('Test MT5 Connection')}",
                use_container_width=True,
                key="settings_test",
            ):
                if mt5_connector and not mt5_connector.is_connected():
                    mt5_connector.connect(timeout_ms=5000)
                if mt5_connector and mt5_connector.is_connected():
                    st.success("✅ MT5 connection is active.")
                    try:
                        synced_files = _sync_selected_market_data()
                        if synced_files:
                            st.info(
                                f"✅ {synced_files} selected Market Watch dataset(s) saved."
                            )
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        logger.warning(
                            "Selected Market Watch data was not persisted after settings connection: %s",
                            error,
                        )
                else:
                    st.error("❌ MT5 connection is unavailable.")
        st.subheader(t("Current Applied Settings"))
        st.json(st.session_state.saved_settings)
    _legacy_settings = """
    
    # Risk Management Section
    st.subheader(f"🛡️ {t('Risk Management')}")
    
    col1, col2 = st.columns(2)
    with col1:
        risk_per_trade = st.slider("Risk per Trade (%)", 0.5, 5.0, 1.0, 0.1, help="Percentage of balance to risk per trade")
        max_positions = st.number_input("Max Concurrent Positions", 1, 10, 3, help="Maximum number of open positions at once")
    with col2:
        daily_loss_limit = st.number_input("Daily Loss Limit ($)", 100, 5000, 500, 50, help="Stop trading when daily loss reaches this amount")
        max_drawdown = st.slider("Max Drawdown (%)", 5.0, 30.0, 15.0, 1.0, help="Stop trading when drawdown exceeds this percentage")
    
    st.divider()
    
    # Strategy Selection
    st.subheader(f"🧠 {t('Strategy Configuration')}")
    enabled_strategies = st.multiselect(
        "Enable Strategies",
        ['TrendFollowing', 'MeanReversion', 'SMC'],
        default=['TrendFollowing'],
        help="Select which strategies to use for generating signals"
    )
    
    # ML Model settings
    st.subheader(f"🤖 {t('AI/ML Settings')}")
    ml_model = st.selectbox(
        "Primary ML Model",
        ['RandomForest', 'XGBoost', 'Ensemble'],
        help="Select the primary machine learning model for predictions"
    )
    min_confidence = st.slider("Minimum Confidence Threshold (%)", 30, 90, 60, 5, help="Only trade signals with confidence above this threshold")
    
    st.divider()
    
    # Data Sources
    st.subheader(f"📊 {t('Data Sources')}")
    col_a, col_b = st.columns(2)
    with col_a:
        db_connection = st.text_input("Database Connection String", type="password", placeholder="postgresql://user:pass@localhost/db")
    with col_b:
        mt5_path = st.text_input("MT5 Server Path", "C:\\Program Files\\MetaTrader 5\\terminal64.exe")
    
    # Timeframe settings
    st.subheader(f"⏱️ {t('Trading Timeframe')}")
    timeframe = st.selectbox("Primary Timeframe", ['M15', 'M30', 'H1', 'H4', 'D1'], index=2)
    trading_hours = st.time_input("Trading Start Time", value=None, help="Leave empty for 24/7 trading")
    
    st.divider()
    
    # Save button
    col_x, col_y, col_z = st.columns([1, 1, 3])
    with col_x:
        if st.button(f"💾 {t('Save Settings')}", type="primary", use_container_width=True):
            st.success(f"✅ {t('Save Settings')}")
            # In production, save to config file or database
            st.session_state.saved_settings = {
                'risk_per_trade': risk_per_trade,
                'max_positions': max_positions,
                'daily_loss_limit': daily_loss_limit,
                'enabled_strategies': enabled_strategies,
                'ml_model': ml_model,
                'min_confidence': min_confidence,
            }
    with col_y:
        if st.button(f"🔄 {t('Reset Defaults')}", type="secondary", use_container_width=True):
            st.info(t("Reset Defaults"))
            st.rerun()
    with col_z:
        # Display current settings summary
        if 'saved_settings' in st.session_state:
            st.json(st.session_state.saved_settings)

    """

# Footer
st.markdown("---")
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    st.caption("AI Smart Trading Dashboard v1.0")
with col_f2:
    st.caption(f"{t('Last Update')}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
with col_f3:
    if MODULES_AVAILABLE:
        st.caption(f"✅ {t('All Systems Operational')}")
    else:
        st.caption("⚠️ Some modules unavailable")
