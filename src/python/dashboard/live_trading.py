# -*- coding: utf-8 -*-
"""
موتور لایو ترید (Live Trading) — حلقه کامل: داده زنده MT5 ← استراتژی ← ریسک ← اجرا

اجزای اصلی:
    SignalEngine      : تحلیل توالی کندل‌ها با همان کلاس استراتژی‌های بک‌تست
                        (backtrader در حالت paper) و تولید سیگنال +1/-1/0.
    LiveBroker        : اجرای کاغذی (Paper) با لاگ معاملات JSONL.
    MT5LiveExecutor   : ارسال سفارش واقعی به متاتریدر ۵ از طریق MetaTraderConnector
                        (خرید/فروش/شورت/بستن + SL/TP سروری).
    RiskManager       : سقف ریسک روزانه، حداکثر پوزیشن باز، حجم بر اساس
                        stop-loss، و قفل «دموی فقط» برای محافظت از حساب واقعی.

حالت‌های اجرا (mode):
    paper  : بدون اتصال به بروکر؛ فقط شبیه‌سازی روی داده زنده/کش.
    demo   : ارسال سفارش واقعی اما فقط اگر login حساب، MT5_LIVE_ACCOUNT نباشد
             (یعنی حساب دمو) — در غیر این صورت مسدود می‌شود.
    live   : ارسال سفارش روی حساب واقعی؛ نیازمند تأیید صریح کاربر در داشبورد
             و مقداردهی MT5_LIVE_CONFIRM=YES در محیط.

در محیط‌های بدون پکیج MetaTrader5 (لینوکس/Mac) همه چیز به‌صورت خودکار روی
paper fallback می‌کند و هیچ بخشی نمی‌شکند.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

_DASH_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _DASH_ROOT.parents[2]
for _p in (str(_DASH_ROOT), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.python.data.config import load_local_env  # noqa: E402

load_local_env()

TRADES_DIR = _REPO_ROOT / "market_data" / "live_trades"

VALID_MODES = ("paper", "demo", "live")


class LiveTradingError(RuntimeError):
    """خطای سطح بالای لایو ترید (اتصال، ریسک یا اجرای سفارش)."""


class RiskLimitBreached(LiveTradingError):
    """یکی از سقف‌های ریسک نقض شده و معامله جدید مجاز نیست."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ======================================================================
# تحلیلگر سیگنال — استفاده مجدد از کلاس‌های استراتژی بک‌تست
# ======================================================================
@dataclass
class SignalResult:
    signal: int                 # +1 خرید، -1 فروش/خروج، 0 بی‌عمل
    price: float
    time: datetime
    equity: float | None = None
    position_size: float = 0.0
    entry_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "price": self.price,
            "time": self.time.isoformat(),
            "equity": self.equity,
            "position_size": self.position_size,
            "entry_price": self.entry_price,
        }


class SignalEngine:
    """روی df تاریخی (Date/OHLCV) یک cerebro بک‌تریدر اجرا می‌کند و وضعیت
    آخرین اسلاید را به سیگنال زنده تبدیل می‌کند.

    خروجی هر call():
        signal = +1  → استراتژی در انتهای داده پوزیشن خرید دارد (LONG)
        signal = -1  → پوزیشن شورت (SHORT)
        signal =  0  → بدون پوزیشن
    همچنین last_equity و تعداد معاملات ثبت‌شده در دسترس است.
    """

    def __init__(self, spec_key: str, params: dict[str, Any],
                 initial_cash: float = 100_000.0,
                 commission_pct: float = 0.001,
                 slippage_pct: float = 0.0005) -> None:
        self.spec_key = spec_key
        self.params = dict(params)
        self.initial_cash = initial_cash
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.last_equity: float | None = None
        self.last_signal: SignalResult | None = None
        self.trade_count: int = 0

    def evaluate(self, df: pd.DataFrame) -> SignalResult:
        import backtrader as bt

        from strategies.base import build_params, get_strategy_class_from_key

        strategy_cls = get_strategy_class_from_key(self.spec_key)
        if strategy_cls is None:
            raise LiveTradingError(f"استراتژی '{self.spec_key}' یافت نشد.")

        feed_df = df.copy()
        if not isinstance(feed_df.index, pd.DatetimeIndex):
            if "Date" in feed_df.columns:
                feed_df["Date"] = pd.to_datetime(feed_df["Date"])
                feed_df = feed_df.set_index("Date")
            else:
                raise LiveTradingError("داده ایندکس زمانی یا ستون Date ندارد.")
        feed_df = feed_df.rename(columns=str.title)[
            ["Open", "High", "Low", "Close"]].dropna()
        if "Volume" not in feed_df.columns:
            feed_df["Volume"] = 0.0
        if len(feed_df) < 5:
            raise LiveTradingError("برای تحلیل سیگنال حداقل ۵ کندل لازم است.")

        cerebro = bt.Cerebro(stdstats=False)
        data = bt.feeds.PandasData(dataname=feed_df)
        cerebro.adddata(data)
        cerebro.addstrategy(strategy_cls, **build_params(self.spec_key, self.params))
        cerebro.broker.setcash(self.initial_cash)
        cerebro.broker.setcommission(commission=self.commission_pct)
        try:
            cerebro.broker.set_slippage_perc(perc=self.slippage_pct)
        except Exception:
            pass
        strat = cerebro.run()[0]

        size = 0.0
        try:
            size = float(strat.position.size)
        except Exception:
            size = 0.0
        signal = 1 if size > 0 else (-1 if size < 0 else 0)
        last_bar = feed_df.iloc[-1]
        ts = feed_df.index[-1]
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        result = SignalResult(
            signal=signal,
            price=float(last_bar["Close"]),
            time=ts,
            equity=float(cerebro.broker.getvalue()),
            position_size=size,
            entry_price=getattr(strat, "entry_price", None),
        )
        self.last_equity = result.equity
        self.last_signal = result
        self.trade_count = len(getattr(strat, "trade_log", []))
        return result


# ======================================================================
# مدیریت ریسک زنده
# ======================================================================
@dataclass
class RiskManager:
    max_open_positions: int = 3
    max_daily_loss_pct: float = 0.05     # ٪ از سرمایه اولیه
    max_risk_per_trade: float = 0.02     # ٪ از equity
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    daily_pnl: float = 0.0
    day_key: str = ""
    realized_today: float = 0.0
    breaches: list[str] = field(default_factory=list)

    def _roll_day(self) -> None:
        today = utcnow().strftime("%Y-%m-%d")
        if today != self.day_key:
            self.day_key = today
            self.realized_today = 0.0

    def record_fill(self, pnl: float) -> None:
        self._roll_day()
        self.realized_today += pnl

    def check(self, *, equity: float, open_positions: int,
              requested_volume: float, price: float) -> tuple[float, list[str]]:
        """بررسی سقف‌ها؛ در نقض، RiskLimitBreached می‌دهد. در غیر این صورت
        حجم نهایی مجاز (پس از کاهش ریسک) را برمی‌گرداند."""
        self._roll_day()
        reasons: list[str] = []
        if equity <= 0:
            raise RiskLimitBreached("حساب بدون سرمایه معتبر.")
        if open_positions >= self.max_open_positions:
            reasons.append(
                f"سقف پوزیشن باز ({self.max_open_positions}) reached")
        loss_limit = equity * self.max_daily_loss_pct
        if self.realized_today <= -loss_limit:
            reasons.append(
                f"سقف ضرر روزانه ({self.max_daily_loss_pct:.1%}) رد شده است")
        if reasons:
            self.breaches.extend(reasons)
            raise RiskLimitBreached(" | ".join(reasons))

        sl = self.stop_loss_pct if self.stop_loss_pct > 0 else 0.10
        risk_amount = equity * self.max_risk_per_trade
        affordable = risk_amount / (price * sl) if price > 0 else 0.0
        allowed = min(float(requested_volume), float(affordable))
        return max(allowed, 0.0)


# ======================================================================
# لاگر معاملات
# ======================================================================
class TradeJournal:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else TRADES_DIR / "live_trades.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: dict[str, Any]) -> dict[str, Any]:
        record = {"ts": utcnow().isoformat(), **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return list(reversed(rows[-limit:]))


# ======================================================================
# کارگزاران (Executors)
# ======================================================================
class PaperExecutor:
    """اجرای کاغذی: پوزیشن‌ها در حافظه + لاگ JSONL. همیشه کار می‌کند."""

    name = "paper"

    def __init__(self, journal: TradeJournal | None = None,
                 cash: float = 100_000.0) -> None:
        self.journal = journal or TradeJournal()
        self.cash = cash
        self.positions: dict[str, dict[str, Any]] = {}
        self.mode = "paper"

    # -- helpers -------------------------------------------------------
    def _key(self, symbol: str) -> str:
        return symbol.upper()

    def account_snapshot(self) -> dict[str, Any]:
        unrealized = 0.0
        for pos in self.positions.values():
            unrealized += (pos["last_price"] - pos["avg_price"]) * pos["volume"] * pos["side"]
        return {
            "mode": self.mode,
            "balance": self.cash,
            "equity": self.cash + unrealized,
            "open_positions": len(self.positions),
        }

    def open_position(self, symbol: str, side: int, volume: float,
                      price: float, *, sl: float | None = None,
                      tp: float | None = None, comment: str = "") -> dict[str, Any]:
        key = self._key(symbol)
        pos = self.positions.get(key)
        if pos and pos["side"] == side:
            total = pos["volume"] + volume
            pos["avg_price"] = (pos["avg_price"] * pos["volume"] + price * volume) / total
            pos["volume"] = total
        elif pos and pos["side"] != side:
            self._close(pos, price, reason="reverse")
        ticket = uuid.uuid4().hex[:8]
        self.positions[key] = {
            "ticket": ticket, "symbol": key, "side": side, "volume": volume,
            "avg_price": price, "last_price": price, "sl": sl, "tp": tp,
            "opened_at": utcnow().isoformat(),
        }
        self.cash -= price * volume * 0.0001  # کارمزد نمادین
        rec = self.journal.log({
            "executor": self.name, "action": "buy" if side > 0 else "sell",
            "symbol": key, "volume": volume, "price": price,
            "sl": sl, "tp": tp, "comment": comment, "ticket": ticket,
        })
        return {"retcode": 10009, "order": ticket, "logged": rec}

    def close_position(self, symbol: str, price: float,
                       reason: str = "manual") -> dict[str, Any]:
        pos = self.positions.get(self._key(symbol))
        if not pos:
            return {"retcode": 10004, "error": "no open position"}
        self._close(pos, price, reason=reason)
        return {"retcode": 10009, "order": pos["ticket"]}

    def _close(self, pos: dict[str, Any], price: float, reason: str) -> None:
        pnl = (price - pos["avg_price"]) * pos["volume"] * pos["side"]
        self.cash += pnl
        self.positions.pop(self._key(pos["symbol"]), None)
        self.journal.log({
            "executor": self.name, "action": "close", "symbol": pos["symbol"],
            "volume": pos["volume"], "price": price, "pnl": round(pnl, 2),
            "reason": reason, "ticket": pos["ticket"],
        })

    def mark_prices(self, prices: dict[str, float]) -> None:
        for sym, px in prices.items():
            pos = self.positions.get(self._key(sym))
            if pos:
                pos["last_price"] = px

    def check_exits(self) -> list[dict[str, Any]]:
        """SL/TP محلی برای حالت paper."""
        closed = []
        for pos in list(self.positions.values()):
            px = pos["last_price"]
            hit = None
            if pos["side"] > 0:
                if pos.get("sl") and px <= pos["sl"]:
                    hit = "stop_loss"
                elif pos.get("tp") and px >= pos["tp"]:
                    hit = "take_profit"
            else:
                if pos.get("sl") and px >= pos["sl"]:
                    hit = "stop_loss"
                elif pos.get("tp") and px <= pos["tp"]:
                    hit = "take_profit"
            if hit:
                self.close_position(pos["symbol"], px, reason=hit)
                closed.append({"symbol": pos["symbol"], "reason": hit, "price": px})
        return closed


class MT5LiveExecutor:
    """ارسال سفارش واقعی به ترمینال MetaTrader 5 (demo/live)."""

    name = "mt5"

    def __init__(self, connector=None, mode: str = "demo",
                 journal: TradeJournal | None = None,
                 magic: int = 20261016) -> None:
        if mode not in ("demo", "live"):
            raise LiveTradingError(f"نامعتبر mode: {mode}")
        if mode == "live" and os.getenv("MT5_LIVE_CONFIRM", "").strip().upper() != "YES":
            raise LiveTradingError(
                "ترید واقعی (live) نیازمند تأیید صریح است: "
                "متغیر محیطی MT5_LIVE_CONFIRM=YES را تنظیم کنید.")
        if connector is None:
            from src.python.data.mt5_connector import MetaTraderConnector

            connector = MetaTraderConnector()
            connector.connect()
        self.connector = connector
        self.mode = mode
        self.journal = journal or TradeJournal()
        self.magic = magic
        self._guard_account()

    def _guard_account(self) -> None:
        info = self.connector.account_info()
        login = int(info.get("login") or 0)
        server = str(info.get("server") or "")
        live_login = os.getenv("MT5_LIVE_ACCOUNT", "").strip()
        is_real = bool(live_login) and login == int(live_login)
        if self.mode == "demo" and is_real:
            raise LiveTradingError(
                f"حساب واردشده ({login}@{server}) حساب واقعی است؛ حالت demo "
                "از ارسال سفارش روی آن جلوگیری کرد. برای ترید واقعی حالت live "
                "با MT5_LIVE_CONFIRM=YES استفاده کنید.")
        self.account = {"login": login, "server": server,
                        "balance": float(info.get("balance") or 0.0),
                        "equity": float(info.get("equity") or 0.0),
                        "currency": str(info.get("currency") or ""),
                        "real_account": is_real}

    # ------------------------------------------------------------------
    def _digits_round(self, symbol: str, price: float) -> float:
        try:
            digits = int(self.connector.symbol_info(symbol).get("digits") or 2)
        except Exception:
            digits = 2
        return round(price, digits)

    def _normalize_volume(self, symbol: str, lots: float) -> float:
        try:
            info = self.connector.symbol_info(symbol)
        except Exception:
            return max(lots, 0.01)
        vmin = float(info.get("volume_min") or 0.01)
        vmax = float(info.get("volume_max") or 100.0)
        step = float(info.get("volume_step") or 0.01) or 0.01
        lots = max(vmin, min(vmax, lots))
        steps = round(lots / step)
        return round(steps * step, 8)

    def tick_price(self, symbol: str) -> float:
        tick = self.connector.symbol_tick(symbol)
        return float(tick.get("bid") or tick.get("ask") or 0.0)

    def account_snapshot(self) -> dict[str, Any]:
        info = self.connector.account_info()
        positions = self.connector.positions_get()
        return {
            "mode": self.mode,
            "balance": float(info.get("balance") or 0.0),
            "equity": float(info.get("equity") or 0.0),
            "margin_free": float(info.get("margin_free") or 0.0),
            "currency": str(info.get("currency") or ""),
            "login": int(info.get("login") or 0),
            "open_positions": len(positions),
        }

    def open_position(self, symbol: str, side: int, volume: float,
                      price: float, *, sl: float | None = None,
                      tp: float | None = None, comment: str = "") -> dict[str, Any]:
        tick = self.connector.symbol_tick(symbol)
        ask = float(tick.get("ask") or price)
        bid = float(tick.get("bid") or price)
        request = {
            "action": 1 if side > 0 else 2,           # ORDER_TYPE_BUY/SELL
            "symbol": symbol,
            "volume": self._normalize_volume(symbol, volume),
            "type": 0 if side > 0 else 1,             # ORDER_TYPE_BUY/SELL
            "price": self._digits_round(symbol, ask if side > 0 else bid),
            "deviation": 20,
            "magic": self.magic,
            "comment": comment or f"ai-smart-{self.mode}",
            "type_time": 0,                            # ORDER_TIME_GTC
            "type_filling": 1,                         # ORDER_FILLING_IOC
        }
        if sl:
            request["sl"] = self._digits_round(symbol, sl)
        if tp:
            request["tp"] = self._digits_round(symbol, tp)
        result = self.connector.order_send(request)
        retcode = int(result.get("retcode") or 0)
        self.journal.log({
            "executor": "mt5", "mode": self.mode,
            "action": "buy" if side > 0 else "sell",
            "symbol": symbol, "volume": request["volume"],
            "price": request["price"], "sl": sl, "tp": tp,
            "retcode": retcode, "comment": comment,
            "ticket": result.get("order"),
        })
        if retcode != 10009:  # TRADE_RETCODE_DONE
            raise LiveTradingError(
                f"سفارش MT5 رد شد (retcode={retcode}): {result}")
        return result

    def close_position(self, symbol: str, price: float | None = None,
                       reason: str = "manual") -> dict[str, Any]:
        results = []
        for pos in self.connector.positions_get(symbol=symbol):
            ticket = int(pos.get("ticket") or 0)
            side = 1 if int(pos.get("type") or 0) == 0 else -1  # BUY→sell
            request = {
                "action": 4,                     # ORDER_TYPE_DEAL
                "position": ticket,
                "type": 1 if side > 0 else 0,    # DEAL type opposite
                "volume": float(pos.get("volume") or 0.0),
                "deviation": 20,
                "magic": self.magic,
                "comment": f"close-{reason}",
                "type_time": 0,
                "type_filling": 1,
            }
            tick = self.connector.symbol_tick(symbol)
            request["price"] = self._digits_round(
                symbol, float(tick.get("bid" if side > 0 else "ask") or price or 0.0))
            result = self.connector.order_send(request)
            retcode = int(result.get("retcode") or 0)
            self.journal.log({
                "executor": "mt5", "mode": self.mode, "action": "close",
                "symbol": symbol, "ticket": ticket, "reason": reason,
                "retcode": retcode, "volume": request["volume"],
                "price": request["price"],
            })
            if retcode != 10009:
                raise LiveTradingError(
                    f"بستن پوزیشن {ticket} رد شد (retcode={retcode}).")
            results.append(result)
        if not results:
            return {"retcode": 10004, "error": "no open position"}
        return results[-1]

    def open_positions(self) -> list[dict[str, Any]]:
        return [p for p in self.connector.positions_get()
                if int(p.get("magic") or 0) in (0, self.magic)] \
            or self.connector.positions_get()


# ======================================================================
# ارکستراتور: چرخه کامل زنده
# ======================================================================
@dataclass
class CycleReport:
    ok: bool
    mode: str
    symbol: str
    bars: int
    signal: int
    price: float
    action_taken: str
    detail: str
    account: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = utcnow().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


class LiveTrader:
    """یک حلقه کامل: دریافت کندل‌های تازه MT5 (یا کش CSV) → سیگنال استراتژی →
    بررسی ریسک → ارسال سفارش (paper/demo/live) → لاگ."""

    def __init__(self, spec_key: str, params: dict[str, Any], *,
                 symbol: str, timeframe: str = "M5",
                 mode: str = "paper",
                 initial_cash: float = 100_000.0,
                 risk: RiskManager | None = None,
                 connector=None,
                 journal: TradeJournal | None = None) -> None:
        if mode not in VALID_MODES:
            raise LiveTradingError(f"نامعتبر mode: {mode}. مجاز: {VALID_MODES}")
        self.spec_key = spec_key
        self.params = dict(params)
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode
        self.initial_cash = initial_cash
        self.journal = journal or TradeJournal()
        self.risk = risk or RiskManager(
            stop_loss_pct=float(params.get("stop_loss_pct", 0.05) or 0.05),
            take_profit_pct=float(params.get("take_profit_pct", 0.15) or 0.15),
            max_risk_per_trade=float(params.get("risk_per_trade", 0.02) or 0.02),
        )
        self.engine = SignalEngine(spec_key, params, initial_cash=initial_cash)
        self.connector = None
        self.executor: Any = PaperExecutor(self.journal, cash=initial_cash)
        self.last_report: CycleReport | None = None
        self.status_message = ""

        if mode in ("demo", "live"):
            self._connect_mt5(connector)

    def _connect_mt5(self, connector=None) -> None:
        from src.python.data.mt5_connector import (MetaTraderConnector,
                                                   MT5ConnectionError)

        try:
            if connector is None:
                connector = MetaTraderConnector()
                connector.connect()
            self.connector = connector
            self.executor = MT5LiveExecutor(connector, mode=self.mode,
                                            journal=self.journal)
            self.status_message = (
                f"🟢 لایو ترید ({self.mode}) روی حساب "
                f"{self.executor.account['login']}@{self.executor.account['server']} فعال است.")
        except MT5ConnectionError as exc:
            raise LiveTradingError(
                f"اتصال MT5 برای حالت {self.mode} ممکن نشد: {exc}. "
                "از حالت paper استفاده کنید یا .env/ترمینال را بررسی نمایید."
            ) from exc

    # ------------------------------------------------------------------
    def fetch_live_frame(self, count: int = 300) -> pd.DataFrame:
        """کندل‌های زنده: ترجیحاً از API MT5، در غیر این صورت از کش CSV."""
        if self.connector is not None:
            rows = self.connector.fetch_ohlcv(self.symbol, self.timeframe,
                                              start_index=0, count=count)
            df = pd.DataFrame(rows)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.set_index("time").sort_index()
            df.index.name = "Date"
            return df.rename(columns={"open": "Open", "high": "High",
                                      "low": "Low", "close": "Close",
                                      "tick_volume": "Volume"})[
                ["Open", "High", "Low", "Close", "Volume"]]
        from data_source import mt5_frame

        df = mt5_frame(self.symbol, self.timeframe)
        if df is None or df.empty:
            raise LiveTradingError(
                f"داده‌ای برای {self.symbol} ({self.timeframe}) در کش MT5 نیست؛ "
                "ابتدا همگام‌سازی را اجرا کنید.")
        return df.tail(count)

    def cycle(self, count: int = 300) -> CycleReport:
        """اجرای یک دور کامل چرخه."""
        df = self.fetch_live_frame(count)
        sig = self.engine.evaluate(df)
        account = self.executor.account_snapshot()
        equity = float(account.get("equity") or sig.equity or self.initial_cash)

        action, detail = "hold", "بدون تغییر پوزیشن"
        try:
            open_pos = self._open_positions_for_symbol()
            if sig.signal != 0 and not any(
                    (p.get("side", 1 if p.get("type", 0) == 0 else -1) == sig.signal)
                    for p in open_pos):
                volume = self._size_order(equity, sig.price, open_pos)
                if volume > 0:
                    sl, tp = self._stops(sig.price, sig.signal)
                    res = self.executor.open_position(
                        self.symbol, sig.signal, volume, sig.price,
                        sl=sl, tp=tp, comment=f"{self.spec_key}-live")
                    action = "opened"
                    detail = (f"{'BUY' if sig.signal > 0 else 'SELL'} "
                              f"{volume} lot @ {sig.price:.5f} "
                              f"(retcode={res.get('retcode')})")
                    self.risk.record_fill(0.0)
                else:
                    action = "blocked"
                    detail = "حجم مجاز پس از کنترل ریسک صفر شد."
            elif sig.signal == 0 and open_pos:
                res = self.executor.close_position(self.symbol, sig.price,
                                                   reason="signal-flat")
                action = "closed"
                detail = f"بستن پوزیشن با سیگنال خنثی (retcode={res.get('retcode')})"
            if hasattr(self.executor, "check_exits"):
                hits = self.executor.check_exits()
                if hits:
                    action = "exited"
                    detail = "; ".join(f"{h['symbol']}:{h['reason']}" for h in hits)
        except RiskLimitBreached as exc:
            action, detail = "risk-blocked", str(exc)
        except LiveTradingError as exc:
            action, detail = "error", str(exc)

        report = CycleReport(
            ok=action != "error", mode=self.mode, symbol=self.symbol,
            bars=len(df), signal=sig.signal, price=sig.price,
            action_taken=action, detail=detail, account=account)
        self.last_report = report
        self.journal.log({"executor": "cycle", "mode": self.mode,
                          "symbol": self.symbol, **report.to_dict()})
        return report

    # ------------------------------------------------------------------
    def _open_positions_for_symbol(self) -> list[dict[str, Any]]:
        if isinstance(self.executor, MT5LiveExecutor):
            return self.connector.positions_get(symbol=self.symbol)
        pos = self.executor.positions.get(self.symbol.upper())
        return [pos] if pos else []

    def _size_order(self, equity: float, price: float,
                    open_pos: list[dict[str, Any]]) -> float:
        requested = 0.1  # لُط پایه
        allowed = self.risk.check(
            equity=equity,
            open_positions=len(open_pos),
            requested_volume=requested,
            price=price,
        )
        if isinstance(self.executor, MT5LiveExecutor):
            return allowed  # نرمال‌سازی در executor انجام می‌شود
        return allowed

    def _stops(self, price: float, side: int) -> tuple[float | None, float | None]:
        sl_pct = self.risk.stop_loss_pct
        tp_pct = self.risk.take_profit_pct
        sl = round(price * (1 - sl_pct), 5) if sl_pct else None
        tp = round(price * (1 + tp_pct), 5) if tp_pct else None
        if side < 0:
            sl = round(price * (1 + sl_pct), 5) if sl_pct else None
            tp = round(price * (1 - tp_pct), 5) if tp_pct else None
        return sl, tp


def resolve_executor_mode(mode_label: str) -> str:
    """نگاشت برچسب فارسی UI به حالت موتور."""
    mapping = {"کاغذی (Paper)": "paper", "دمو (Demo)": "demo",
               "واقعی (Live)": "live", "paper": "paper", "demo": "demo",
               "live": "live"}
    mode = mapping.get(str(mode_label).strip(), None)
    if mode is None:
        raise LiveTradingError(f"حالت ناشناخته: {mode_label}")
    return mode


# ======================================================================
# حلقه خودکار (Daemon) — چرخه کامل بدون کلیک کاربر
# ======================================================================
class LiveTradingLoop:
    """مدل Actor: یک thread پس‌زمینه که هر `interval_sec` یک دور کامل
    (داده زنده ← سیگنال ← ریسک ← سفارش ← لاگ) اجرا می‌کند.

    شروع/توقف از داشبورد یا CLI:
        loop = LiveTradingLoop(trader, interval_sec=60)
        loop.start(); ...; loop.stop(wait=True)
    """

    def __init__(self, trader: "LiveTrader", interval_sec: float = 60.0,
                 max_cycles: int | None = None,
                 journal: TradeJournal | None = None) -> None:
        import threading

        self.trader = trader
        self.interval_sec = max(float(interval_sec), 1.0)
        self.max_cycles = max_cycles
        self.journal = journal or trader.journal
        self._thread = None
        self._stop = threading.Event()
        self.cycles_done = 0
        self.last_error: str | None = None
        self.reports: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        import threading

        if self.running:
            return
        # شروع دوباره بعد از اتمام دورهای قبلی: شمارنده و Event تازه.
        if self._thread is not None and not self._thread.is_alive():
            self.cycles_done = 0
            self.reports = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.journal.log({"executor": "loop", "action": "start",
                          "mode": self.trader.mode,
                          "symbol": self.trader.symbol,
                          "interval_sec": self.interval_sec})

    def _has_open_position(self) -> bool:
        try:
            return bool(self._open_positions_snapshot())
        except Exception:
            return False

    def _open_positions_snapshot(self) -> list[dict[str, Any]]:
        return self.trader._open_positions_for_symbol()

    @staticmethod
    def _thread_cls():
        import threading

        return threading.Thread

    def stop(self, wait: bool = True, timeout: float = 15.0) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=timeout)
        self.journal.log({"executor": "loop", "action": "stop",
                          "cycles_done": self.cycles_done})

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                report = self.trader.cycle()
                self.reports.append(report.to_dict())
                self.reports = self.reports[-50:]
                self.last_error = None if report.ok else report.detail
            except Exception as exc:  # هیچ خطایی نباید حلقه را بکشد
                self.last_error = str(exc)
                self.journal.log({"executor": "loop", "action": "error",
                                  "detail": str(exc)})
            self.cycles_done += 1
            keep_running = (self.max_cycles is None
                            or self._has_open_position()
                            or self.cycles_done < self.max_cycles)
            if not keep_running:
                break
            self._stop.wait(self.interval_sec)


def run_loop_cli(argv: list[str] | None = None) -> int:
    """اجرای حلقه لایو ترید از ترمینال:
        python -m src.python.dashboard.live_trading --symbol XAUUSD_l --timeframe M5 \
            --strategy MA-Crossover --mode paper --interval 60
    """
    import argparse

    parser = argparse.ArgumentParser(description="AI Smart Trade live-trading loop")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--strategy", default="MA-Crossover")
    parser.add_argument("--mode", default="paper", choices=VALID_MODES)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--cycles", type=int, default=None,
                        help="تعداد دور؛ پیش‌فرض بی‌نهایت (Ctrl+C برای توقف)")
    args = parser.parse_args(argv)

    from strategies.base import discover_strategies

    specs = discover_strategies()
    if args.strategy not in specs:
        print(f"استراتژی '{args.strategy}' یافت نشد. موجود: {list(specs)}")
        return 2
    params = {f["key"]: f["default"] for f in specs[args.strategy].get("fields", [])}
    params.update({f["key"]: f["default"] for f in _risk_defaults()})

    trader = LiveTrader(args.strategy, params, symbol=args.symbol,
                        timeframe=args.timeframe, mode=args.mode)
    loop = LiveTradingLoop(trader, interval_sec=args.interval,
                           max_cycles=args.cycles)
    print(trader.status_message or f"🟢 حلقه {args.mode} روی {args.symbol} ({args.timeframe})…")
    loop.start()
    try:
        while loop.running:
            report = trader.last_report
            if report:
                print(f"[{report.timestamp[:19]}] signal={report.signal} "
                      f"action={report.action_taken} — {report.detail}")
            loop._stop.wait(min(loop.interval_sec, 5.0))
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        print(f"⏹ متوقف شد پس از {loop.cycles_done} دور.")
    return 0


def _risk_defaults():
    from strategies.base import RISK_FIELDS

    return RISK_FIELDS


if __name__ == "__main__":
    sys.exit(run_loop_cli())
