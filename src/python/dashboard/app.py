# -*- coding: utf-8 -*-
"""
داشبورد مدیریت استراتژی (Streamlit)

اجرا (در ترمینال/CMD، نه با اجرای مستقیم در IDLE):
    streamlit run src/python/dashboard/app.py

اگر این فایل را مستقیماً با Python/IDLE اجرا کنید، پیام زیر ظاهر می‌شود:
    WARNING ... missing ScriptRunContext!
این پیام فقط یک هشدار است و نشانه خطا نیست؛ چون Streamlit برای نمایش
داشبورد در مرورگر طراحی شده و باید با دستور بالا اجرا شود.
"""
import datetime as dt
import io
import json
import os
import sys

# اطمینان از در دسترس بودن ریشه پکیج داشبورد در sys.path (چه با streamlit
# از هر مسیری اجرا شود، چه به‌صورت ماژول import شود).
_DASH_ROOT = os.path.dirname(os.path.abspath(__file__))
if _DASH_ROOT not in sys.path:
    sys.path.insert(0, _DASH_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_source import (fetch_history, load_csv, LiveFeed,
                         mt5_frame, mt5_available_symbols,
                         mt5_symbol_timeframes)
from engine import run_backtest, optimize_strategy
import strategies.base as sb

# ======================================================================
# چرخه خودکار: بالا آوردن متاتریدر ۵ + استخراج داده هنگام استارت داشبورد
# ======================================================================
_PROJECT_ROOT = os.path.normpath(os.path.join(_DASH_ROOT, "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@st.cache_resource(show_spinner="🔌 اتصال خودکار به MetaTrader 5 و همگام‌سازی داده…")
def bootstrap_mt5():
    """تلاش برای راه‌اندازی MT5 و استخراج افزایشی داده. فقط یک بار در هر سشن سرور.

    با متغیر محیطی MT5_AUTO_BOOTSTRAP=0 غیرفعال می‌شود. در محیط‌های بدون
    ویندوز/MT5 خطا نمی‌دهد و فقط وضعیت را گزارش می‌کند.
    """
    if os.environ.get("MT5_AUTO_BOOTSTRAP", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    try:
        from scripts.mt5_bootstrap import ensure_mt5_pipeline

        return ensure_mt5_pipeline()
    except Exception as exc:  # هر خرابی نباید جلوی بالا آمدن داشبورد را بگیرد
        class _Failed:
            ok = False
            connected = False
            message = f"bootstrap ناموفق بود: {exc}"
            extracted_symbols: list = []
            terminal_running = False
            launched_terminal = False
        return _Failed()


_MT5_STATUS = bootstrap_mt5()

# منبع حقیقت استراتژی‌ها: پوشه strategies/ — هر فایل *.py یک استراتژی مستقل.
# این تابع هر بار (بعد از آپلود/ویرایش/حذف در همان سشن) دوباره اسکن می‌کند.
@st.cache_data(show_spinner=False)
def _scan_token():
    """توکن تغییر: با هر ویرایش/آپلود/حذف invalidate می‌شود تا selectboxها تازه شوند."""
    return True


def get_specs():
    return sb.discover_strategies()


STRATEGY_TEMPLATE = '''# -*- coding: utf-8 -*-
"""استراتژی سفارشی من — این فایل را در پوشه strategies/ ذخیره کنید.

قوانین:
  • کلاس باید از RiskManagedStrategy ارث ببرد و متد _entry_logic را بنویسد.
  • به self.position (پوزیشن باز)، self._place_entry(+1/-1) و
    self.order = self.close() برای خروج دسترسی دارید.
  • اندیکاتورها را در __init__ با bt.indicators بسازید (بک‌تریدر خودش
    دوره گرم‌شدن را مدیریت می‌کند).
  • توابع خطرناک (exec/eval/open/import شبکه) مجاز نیست؛ فقط
    backtrader / pandas / numpy / math / datetime.
"""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class MyCustomStrategy(RiskManagedStrategy):
    # پارامترهای ریسک مشترک (BASE_PARAMS) + پارامترهای اختصاصی شما
    params = BASE_PARAMS + (
        ("fast_period", 10),
        ("slow_period", 30),
        ("strategy_name", "My-Custom"),   # کلید یکتای استراتژی
    )

    def __init__(self):
        super().__init__()
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)

    def _entry_logic(self):
        if self.crossover > 0:
            self._place_entry(+1)          # خرید با مدیریت ریسک پایه
        elif self.crossover < 0:
            self._place_entry(-1)          # فروش/خروج


# ---- مشخصات برای داشبورد: فرم تنظیمات خودکار از روی fields ساخته می‌شود ----
SPEC = {
    "key": "My-Custom",                    # باید با strategy_name یکی باشد
    "title": "استراتژی سفارشی من",
    "desc": "توضیح کوتاه منطق ورود/خروج.",
    "fields": [
        {"key": "fast_period", "label": "دوره MA سریع", "type": "int",
         "min": 2, "max": 120, "default": 10},
        {"key": "slow_period", "label": "دوره MA کند", "type": "int",
         "min": 5, "max": 300, "default": 30},
        # type های مجاز: int | float | bool | select | text
        # select: {"key": ..., "type": "select", "options": ["A","B"], "default": "A"}
    ],
}


def get_strategy_class():
    return MyCustomStrategy
'''


def _ensure_streamlit_runtime():
    """اگر فایل خارج از محیط Streamlit (مثلاً با python app.py یا IDLE)
    اجرا شده باشد، راهنمای درست اجرا را نشان می‌دهد."""
    if st.runtime.exists():  # داخل سرور Streamlit هستیم — حالت عادی
        return
    print("=" * 70)
    print("⚠️  این فایل یک داشبورد Streamlit است و نباید مستقیم اجرا شود.")
    print("    برای مشاهده برنامه در مرورگر، در ترمینال (CMD یا PowerShell)")
    print("    وارد این پوشه شوید و دستور زیر را اجرا کنید:\n")
    print("        streamlit run app.py\n")
    print("    سپس آدرس http://localhost:8501 به صورت خودکار باز می‌شود.")
    print("    (پیام 'missing ScriptRunContext' که دیدید فقط یک هشدار است.)")
    print("=" * 70)
    sys.exit(0)


_ensure_streamlit_runtime()

st.set_page_config(page_title="سیستم مدیریت استراتژی ترید", layout="wide")

REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")
HISTORY_DB = os.path.join(REPORT_DIR, "runs_history.jsonl")


# ======================================================================
# ساخت فرم داینامیک از SPEC — کاربر همه بخش‌های استراتژی را ویرایش می‌کند
# ======================================================================
def render_field(field, prefix, container):
    ftype = field["type"]
    key = f"{prefix}_{field['key']}"
    label = field["label"]
    default = field["default"]
    if ftype == "int":
        return container.number_input(label, min_value=int(field["min"]),
                                      max_value=int(field["max"]),
                                      value=int(default), step=1, key=key)
    if ftype == "float":
        return container.number_input(label, min_value=float(field["min"]),
                                      max_value=float(field["max"]),
                                      value=float(default),
                                      step=float(field.get("step", 0.01)), key=key)
    if ftype == "bool":
        return container.checkbox(label, value=bool(default), key=key)
    if ftype == "select":
        idx = field["options"].index(default)
        return container.selectbox(label, field["options"], index=idx, key=key)
    # text / code
    return container.text_input(label, value=str(default), key=key)


def collect_params(spec_key, section, specs=None):
    specs = specs or get_specs()
    params = {}
    with st.container():
        cols = st.columns(2)
        for i, f in enumerate(specs[spec_key]["fields"]):
            c = cols[i % 2]
            params[f["key"]] = render_field(f, f"{section}_{spec_key}", c)
    return params


def collect_risk_params(section):
    params = {}
    cols = st.columns(2)
    for i, f in enumerate(sb.RISK_FIELDS):
        c = cols[i % 2]
        params[f["key"]] = render_field(f, f"{section}_risk", c)
    return params


def save_run_record(record):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(HISTORY_DB, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_run_records():
    if not os.path.exists(HISTORY_DB):
        return []
    rows = []
    with open(HISTORY_DB, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return list(reversed(rows))


# ======================================================================
# سایدبار — تنظیمات داده و اجرا
# ======================================================================
st.title("📊 داشبورد مدیریت، بک‌تست و اجرای زنده استراتژی")

# ======================================================================
# بنر وضعیت چرخه MT5 (خودکار هنگام استارت داشبورد اجرا شده است)
# ======================================================================
_mt5_cached_symbols = mt5_available_symbols()
if _MT5_STATUS is None:
    st.caption("⏭️ بوت‌استرپ خودکار MT5 غیرفعال است (MT5_AUTO_BOOTSTRAP=0).")
elif getattr(_MT5_STATUS, "connected", False):
    st.success(
        f"🟢 **متاتریدر ۵ متصل است** — {_MT5_STATUS.message}\n\n"
        f"نمادهای همگام‌سازی‌شده: {', '.join(_MT5_STATUS.extracted_symbols) or '—'}"
    )
else:
    st.info(
        f"🟡 **اتصال زنده MT5 در این محیط در دسترس نیست** — {_MT5_STATUS.message}\n\n"
        "داشبورد به‌صورت خودکار روی داده‌های کش‌شدهٔ MT5 "
        f"(`market_data/historical` — {_mt5_cached_symbols and len(_mt5_cached_symbols) or 0} نماد) "
        "کار می‌کند. روی ویندوز با MT5 نصب‌شده، همین چرخه باعث بالا آمدن ترمینال و "
        "استخراج تازه داده می‌شود.",
        icon="🔄",
    )

with st.sidebar:
    st.header("⚙️ منبع داده")
    data_mode = st.radio("حالت داده", ["داده تاریخی (یاهو)", "داده استخراج MT5",
                                       "آپلود CSV", "فید زنده (Paper Trading)"],
                         index=0)

    df = None
    ticker_label = ""
    if data_mode == "داده تاریخی (یاهو)":
        ticker = st.text_input("نماد (Ticker)", value="AAPL").strip().upper()
        col_a, col_b = st.columns(2)
        start_d = col_a.date_input("تاریخ شروع", dt.date(2020, 1, 1))
        end_d = col_b.date_input("تاریخ پایان", dt.date(2023, 1, 1))
        interval = st.selectbox("بازه زمانی", ["1d", "1wk", "1h", "15m"], index=0)
        use_cache = st.checkbox("استفاده از کش محلی", value=True)
        if st.button("📥 دانلود/بارگذاری داده"):
            with st.spinner("در حال دریافت داده…"):
                df = fetch_history(ticker, start_d, end_d, interval, use_cache)
            if df is None or df.empty:
                st.error("داده‌ای یافت نشد! نماد یا بازه را بررسی کنید.")
            else:
                st.session_state["df"] = df
                st.session_state["ticker_label"] = f"{ticker} ({interval})"
                st.success(f"{len(df)} رک بارگذاری شد.")
    elif data_mode == "داده استخراج MT5":
        # کش CSV تولیدشده توسط src/python/data/main.py (استخراج از متاتریدر ۵)
        mt5_symbols = mt5_available_symbols()
        if not mt5_symbols:
            st.warning(
                "هیچ داده استخراج‌شده MT5 در market_data/historical یافت نشد.\n\n"
                "ابتدا ابزار استخراج را اجرا کنید:\n\n"
                "`python -m src.python.data.main --symbols XAUUSD --timeframes D1,H1`\n\n"
                "(نیاز به ترمینال MetaTrader 5 — فایل .env را تنظیم کنید)")
        else:
            mt5_sym = st.selectbox("نماد MT5", mt5_symbols)
            mt5_tfs = mt5_symbol_timeframes(mt5_sym) or ["D1"]
            mt5_tf = st.selectbox("بازه زمانی (Timeframe)", mt5_tfs,
                                  index=mt5_tfs.index("D1") if "D1" in mt5_tfs else 0)
            col_load, col_sync = st.columns([2, 1])
            with col_load:
                load_btn = st.button("📥 بارگذاری داده MT5")
            with col_sync:
                sync_btn = st.button("🔄 همگام‌سازی از MT5", help=(
                    "در ویندوز: ترمینال را بالا می‌آورد، به API وصل شده و داده افزایشی "
                    "استخراج می‌کند؛ سپس تازه‌ترین CSV همین نماد را بارگذاری می‌کند."))
            if sync_btn:
                status = None
                try:
                    from scripts.mt5_bootstrap import ensure_mt5_pipeline

                    with st.spinner("در حال راه‌اندازی MT5 و استخراج داده…"):
                        status = ensure_mt5_pipeline()
                except Exception as exc:
                    st.error(f"خطا در بوت‌استرپ MT5: {exc}")
                if status is not None and getattr(status, "connected", False):
                    st.success(f"✅ {status.message}")
                    st.cache_data.clear()  # کش فهرست/فریم‌های MT5 تازه شود
                    mt5_symbols = mt5_available_symbols()
                    if mt5_sym not in mt5_symbols and mt5_symbols:
                        mt5_sym = mt5_symbols[0]
                    load_btn = True  # بلافاصله تازه‌ترین کش را بارگذاری کن
                elif status is not None:
                    st.warning(f"⚠️ {status.message} — کش فعلی استفاده می‌شود.")
            if load_btn:
                df = mt5_frame(mt5_sym, mt5_tf)
                if df is None or df.empty:
                    st.error("برای این نماد/بازبازه داده‌ای یافت نشد!")
                else:
                    st.session_state["df"] = df
                    st.session_state["ticker_label"] = f"{mt5_sym} ({mt5_tf} · MT5)"
                    st.success(f"{len(df)} رک از خروجی استخراج MT5 بارگذاری شد.")
    elif data_mode == "آپلود CSV":
        up = st.file_uploader("فایل CSV با ستون‌های Date/OHLCV", type=["csv"])
        if up is not None:
            try:
                df = load_csv(up)
                st.session_state["df"] = df
                st.session_state["ticker_label"] = up.name
                st.success(f"{len(df)} رک خوانده شد.")
            except Exception as e:
                st.error(f"خطا در فایل: {e}")
    else:  # فید زنده شبیه‌سازی‌شده
        base_price = st.number_input("قیمت پایه شبیه‌ساز", value=100.0)
        n_bars = st.slider("تعداد بار زنده", 50, 1000, 300)
        if st.button("🔴 اتصال به فید زنده (شبیه‌سازی)"):
            feed = LiveFeed(base_price=base_price)
            bars = [feed.next_bar() for _ in range(n_bars)]
            live_df = pd.DataFrame(bars).set_index("time")
            live_df = live_df.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume"})[
                ["Open", "High", "Low", "Close", "Volume"]]
            st.session_state["df"] = live_df
            st.session_state["ticker_label"] = "LIVE-SIM"
            st.success("فید زنده آماده شد (برای هر بار اجرای دکمه، جریان تازه).")

    st.divider()
    st.header("💰 تنظیمات بروکر")
    initial_cash = st.number_input("سرمایه اولیه", value=100_000, step=1000)
    commission_pct = st.number_input("کارمزد (٪ هر معامله)", value=0.10,
                                     min_value=0.0, step=0.05) / 100
    slippage_pct = st.number_input("اسلیپیج (٪)", value=0.05,
                                   min_value=0.0, step=0.05) / 100

df = st.session_state.get("df")
ticker_label = st.session_state.get("ticker_label", "")

if df is None:
    st.info("ابتدا از پنل سمت چپ داده را بارگذاری کنید "
            "(دانلود/آپلود/فید زنده) و سپس استراتژی را اجرا نمایید.")
    st.stop()

# ======================================================================
# تب‌ها
# ======================================================================
tab_bt, tab_opt, tab_live, tab_lib, tab_hist = st.tabs(
    ["🧪 بک‌تست و گزارش", "🎛 بهینه‌سازی پارامتر", "🔴 سیگنال زنده",
     "📁 مدیریت فایل استراتژی‌ها", "🗂 تاریخچه اجراها"])

SPECS = get_specs()
strategy_keys = list(SPECS.keys())
if not strategy_keys:
    st.error("هیچ استراتژی سالمی در پوشه strategies/ پیدا نشد! "
             "از تب «مدیریت فایل استراتژی‌ها» برای افزودن استفاده کنید.")
    st.stop()

# ----------------------------------------------------------------------
# تب ۱: بک‌تست
# ----------------------------------------------------------------------
with tab_bt:
    st.subheader("انتخاب استراتژی و تنظیمات کامل")
    spec_key = st.selectbox(
        "استراتژی", strategy_keys,
        format_func=lambda k: f"{SPECS[k]['title']} [{k}]")
    st.caption(SPECS[spec_key]["desc"])

    st.markdown("**پارامترهای اختصاصی استراتژی**")
    s_params = collect_params(spec_key, "bt", SPECS)
    st.markdown("**مدیریت ریسک (مشترک همه استراتژی‌ها)**")
    r_params = collect_risk_params("bt")
    all_params = {**s_params, **r_params}

    run_btn = st.button("🚀 اجرای بک‌تست و تولید گزارش", type="primary")

    if run_btn:
        with st.spinner("در حال اجرای بک‌تست…"):
            try:
                res = run_backtest(df, spec_key, all_params, initial_cash,
                                   commission_pct, slippage_pct)
            except Exception as e:
                st.error(f"خطا در اجرا: {e}")
                st.stop()
        m = res["metrics"]
        st.session_state["last_result"] = dict(
            equity=res["equity"], trades=res["trades"], metrics=m,
            spec_key=spec_key, params=all_params, df=df,
            ticker=ticker_label, cash=initial_cash)

        save_run_record(dict(
            time=str(dt.datetime.now()), ticker=ticker_label,
            strategy=spec_key, params=all_params,
            net_pnl=round(m["net_pnl"], 2),
            total_return_pct=round(m["total_return_pct"], 3),
            sharpe=(None if pd.isna(m["sharpe"]) else round(m["sharpe"], 3)),
            max_dd_pct=round(m["max_drawdown_pct"], 2),
            trades=m["total_trades"]))

    last = st.session_state.get("last_result")
    if last:
        m = last["metrics"]
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("سود خالص", f"{m['net_pnl']:,.0f}")
        c2.metric("بازده کل", f"{m['total_return_pct']:+.2f}%")
        c3.metric("شارپ", f"{m['sharpe']:.2f}" if pd.notna(m["sharpe"]) else "—")
        c4.metric("حداکثر افت", f"{m['max_drawdown_pct']:.1f}%")
        c5.metric("تعداد معامله", m["total_trades"])
        c6.metric("وین‌ریت", f"{m['win_rate_pct']:.0f}%")

        eq = last["equity"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="ارزش پرتفو"))
        dfee = last["df"]["Close"]
        fig.add_trace(go.Scatter(x=dfee.index, y=dfee.values / dfee.iloc[0] * eq.iloc[0],
                                 name="Buy & Hold", opacity=0.5))
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")

        if not last["trades"].empty:
            st.subheader("📒 گزارش معاملات")
            tr = last["trades"].copy()
            tr["pnl_net"] = tr["pnl_net"].round(2)
            st.dataframe(tr, width="stretch", hide_index=True)
            st.download_button("⬇ دانلود CSV معاملات",
                               tr.to_csv(index=False).encode("utf-8-sig"),
                               "trades.csv", "text/csv")

        # گزارش QuantStats
        st.subheader("📉 گزارش حرفه‌ای عملکرد (QuantStats)")
        if st.button("تولید گزارش HTML"):
            try:
                import quantstats as qs
                returns = eq.pct_change().fillna(0)
                buf = io.StringIO()
                qs.reports.html(returns, output=buf,
                                title=f"{last['spec_key']} روی {last['ticker']}",
                                download=False)
                html = buf.getvalue()
                st.components.v1.html(html, height=900, scrolling=True)
                st.download_button("⬇ دانلود فایل گزارش",
                                   html.encode("utf-8"),
                                   "report.html", "text/html")
            except Exception as e:
                st.warning(f"گزارش QuantStats قابل تولید نبود: {e}")

# ----------------------------------------------------------------------
# تب ۲: بهینه‌سازی
# ----------------------------------------------------------------------
with tab_opt:
    st.subheader("🎛 جستجوی شبکه‌ای پارامترها (Grid Search)")
    spec_key_o = st.selectbox("استراتژی برای بهینه‌سازی", strategy_keys,
                              key="opt_strat",
                              format_func=lambda k: SPECS[k]["title"])
    int_fields = [f for f in SPECS[spec_key_o]["fields"]
                  if f["type"] in ("int", "float")]
    if not int_fields:
        st.info("این استراتژی پارامتر عددی قابل بهینه‌سازی ندارد.")
    else:
        chosen = st.multiselect("پارامترهای بهینه‌سازی",
                                [f["key"] for f in int_fields],
                                default=[int_fields[0]["key"]])
        grids = {}
        for f in int_fields:
            if f["key"] in chosen:
                vals = st.text_input(
                    f"مقادیر {f['label']} (با کاما)",
                    value=",".join(str(f["default"])))
                try:
                    cast = float if f["type"] == "float" else int
                    grids[f["key"]] = [cast(v.strip()) for v in vals.split(",") if v.strip()]
                except ValueError:
                    st.error("مقادیر باید عددی باشند.")
        base_r = collect_risk_params("opt")
        base_s = {f["key"]: f["default"] for f in SPECS[spec_key_o]["fields"]}
        if st.button("▶ شروع بهینه‌سازی", type="primary") and grids:
            with st.spinner("در حال اجرای ترکیبات…"):
                out = optimize_strategy(df, spec_key_o, {**base_s, **base_r},
                                        grids, initial_cash,
                                        commission_pct, slippage_pct)
            st.dataframe(out, width="stretch", hide_index=True)
            if "sharpe" in out.columns and not out.empty:
                best = out.iloc[0]
                st.success("بهترین ترکیب بر اساس شارپ: " +
                           ", ".join(f"{k}={best[k]}" for k in grids))

# ----------------------------------------------------------------------
# تب ۳: سیگنال زنده
# ----------------------------------------------------------------------
with tab_live:
    st.subheader("🔴 پایش زنده و صدور سیگنال (Paper Trading)")
    st.caption("این بخش استراتژی انتخابی را روی جریان زنده اجرا کرده و "
               "آخرین سیگنال را نشان می‌دهد. برای اتصال واقعی، حلقه فید را به "
               "ccxt/کارگزار وصل کنید (data_source.LiveFeed).")
    spec_key_l = st.selectbox("استراتژی روی داده زنده", strategy_keys,
                              key="live_strat",
                              format_func=lambda k: SPECS[k]["title"])
    l_params = {**collect_params(spec_key_l, "live", SPECS),
                **collect_risk_params("live")}
    if st.button("🔄 اجرای تحلیل روی آخرین داده"):
        with st.spinner("تحلیل…"):
            try:
                res = run_backtest(df, spec_key_l, l_params, initial_cash,
                                   commission_pct, slippage_pct)
            except Exception as e:
                st.error(str(e)); st.stop()
        eq = res["equity"]
        last_pos = res["strategy"].position.size if hasattr(res["strategy"], "position") else 0
        sig = "🟢 خرید/هلد" if last_pos > 0 else ("🔴 فروش/شورت" if last_pos < 0 else "⚪ بدون پوزیشن")
        st.markdown(f"### وضعیت فعلی: {sig}")
        st.line_chart(eq)
        st.write(f"آخرین قیمت: `{df.Close.iloc[-1]:.2f}` | "
                 f"ارزش کاغذی پرتفو: `{eq.iloc[-1]:,.0f}` | "
                 f"تعداد سیگنال تاکنون: `{res['metrics']['total_trades']}`")

# ----------------------------------------------------------------------
# تب ۴: مدیریت فایل استراتژی‌ها (آپلود / ویرایش / حذف)
# ----------------------------------------------------------------------
with tab_lib:
    st.subheader("📁 هر استراتژی = یک فایل *.py در پوشه strategies/")
    st.caption(f"مسیر پوشه: `{os.path.join(os.path.dirname(__file__), 'strategies')}` — "
               "فایل‌های سالم به‌صورت خودکار اسکن و در همه تب‌ها قابل اجرا می‌شوند.")

    specs_now = get_specs()
    keys_now = list(specs_now.keys())

    # ---- جدول وضعیت فایل‌ها ----
    lib_rows = []
    for k in keys_now:
        e = specs_now[k]
        lib_rows.append({"کلید": k, "عنوان": e["title"], "فایل": e["_file"],
                         "تعداد پارامتر": len(e.get("fields", []))})
    if lib_rows:
        st.dataframe(pd.DataFrame(lib_rows), width="stretch", hide_index=True)

    col_up, col_edit = st.columns(2)

    # ================= آپلود فایل استراتژی =================
    with col_up:
        st.markdown("#### ⬆ آپلود فایل استراتژی (.py)")
        upl = st.file_uploader("فایل پایتون استراتژی را انتخاب کنید",
                               type=["py"], key="strategy_upload")
        upl_name = st.text_input("نام فایل مقصد (اختیاری)",
                                 value="", placeholder="my_strategy.py",
                                 key="upload_name")
        if st.button("💾 ذخیره فایل آپلودی در strategies/", key="do_upload"):
            if upl is None:
                st.error("ابتدا فایل را انتخاب کنید.")
            else:
                source = upl.getvalue().decode("utf-8-sig")
                fname = (upl_name.strip() or upl.name)
                ok, msg = sb.save_strategy_source(fname, source)
                if ok:
                    st.cache_data.clear()
                    st.success(f"✅ ذخیره شد: {msg}")
                    st.rerun()
                else:
                    st.error(f"❌ فایل ذخیره نشد — {msg}")

        st.markdown("---")
        st.markdown("#### ➕ ساخت استراتژی جدید از قالب آماده")
        new_name = st.text_input("نام فایل جدید", placeholder="my_strategy.py",
                                 key="new_strat_name")
        if st.button("🧩 ایجاد از قالب", key="do_new_template"):
            if not new_name.strip():
                st.error("نام فایل را وارد کنید.")
            else:
                ok, msg = sb.save_strategy_source(new_name.strip(), STRATEGY_TEMPLATE)
                if ok:
                    st.cache_data.clear()
                    st.success(f"✅ قالب ذخیره شد: {msg} — حالا در ستون ویرایش بازنویسی‌اش کنید.")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")
        st.download_button("⬇ دانلود قالب نمونه (template.py)",
                           STRATEGY_TEMPLATE.encode("utf-8"),
                           "strategy_template.py", "text/python")

    # ================= ویرایش سورس =================
    with col_edit:
        st.markdown("#### ✏️ ویرایش سورس استراتژی")
        if not keys_now:
            st.info("استراتژی سالمی برای ویرایش نیست؛ ابتدا آپلود کنید.")
        else:
            edit_key = st.selectbox("استراتژی برای ویرایش", keys_now,
                                    key="edit_strat",
                                    format_func=lambda k: f"{specs_now[k]['title']} [{k}]")
            # پارامتر language در اکثر نسخه‌های Streamlit پشتیبانی نمی‌شود؛
            # فقط در صورت وجود اضافه می‌گردد (سازگاری با همه نسخه‌ها)
            import inspect as _inspect
            _ta_kwargs = dict(value=sb.get_strategy_source(edit_key) or "",
                              height=420, key=f"editor_{edit_key}")
            if "language" in _inspect.signature(st.text_area).parameters:
                _ta_kwargs["language"] = "python"
            src_text = st.text_area("سورس فعلی (ویرایش کنید و ذخیره بزنید)",
                                    **_ta_kwargs)
            b1, b2 = st.columns(2)
            if b1.button("💾 ذخیره تغییرات", key="do_save_edit", type="primary"):
                ok, msg = sb.save_strategy_source(
                    specs_now[edit_key]["_file"], src_text)
                if ok:
                    st.cache_data.clear()
                    st.success("✅ ذخیره و بارگذاری شد.")
                    st.rerun()
                else:
                    st.error(f"❌ ذخیره نشد — {msg}")
            if b2.button("🗑 حذف (انتقال به _archive)", key="do_delete"):
                ok, msg = sb.delete_strategy_file(edit_key)
                if ok:
                    st.cache_data.clear()
                    st.warning(f"فایل به {msg} منتقل شد.")
                    st.rerun()
                else:
                    st.error(msg)

# ----------------------------------------------------------------------
# تب ۵: تاریخچه
# ----------------------------------------------------------------------
with tab_hist:
    st.subheader("🗂 تاریخچه اجراها (برای مقایسه نتایج)")
    recs = load_run_records()
    if recs:
        hist_df = pd.DataFrame(recs)
        st.dataframe(hist_df, width="stretch", hide_index=True)
        st.download_button("⬇ دانلود تاریخچه JSON",
                           json.dumps(recs, ensure_ascii=False, indent=1).encode("utf-8"),
                           "history.json", "application/json")
    else:
        st.info("هنوز اجرایی ثبت نشده است.")
