# -*- coding: utf-8 -*-
"""تست‌های موتور لایو ترید (paper / ریسک / ژورنال / حلقه)."""
from __future__ import annotations

import json
import tempfile

import numpy as np
import pandas as pd
import pytest


def make_df(n=400, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    trend = np.linspace(0, 0.5, n)
    noise = rng.normal(0, 0.02, n).cumsum()
    close = 100 * np.exp(trend + noise)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = low + (high - low) * rng.random(n)
    vol = rng.integers(100_000, 900_000, n).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=dates)


LIVE_PARAMS = {
    "fast_period": 10, "slow_period": 30,
    "risk_per_trade": 0.02, "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15, "trailing_stop_pct": 0.0,
    "allow_short": True,
}


@pytest.fixture()
def journal_path():
    return tempfile.mktemp(suffix=".jsonl")


def test_signal_engine_produces_signal(journal_path):
    from live_trading import SignalEngine

    engine = SignalEngine("MA-Crossover", LIVE_PARAMS)
    sig = engine.evaluate(make_df())
    assert sig.signal in (-1, 0, 1)
    assert sig.price > 0
    assert sig.equity is not None and sig.equity > 0


def test_paper_cycle_opens_and_holds(journal_path):
    from live_trading import LiveTrader, TradeJournal

    trader = LiveTrader("MA-Crossover", LIVE_PARAMS, symbol="TESTUSD",
                        timeframe="D1", mode="paper",
                        journal=TradeJournal(journal_path))
    # تزریق داده به‌جای کش CSV
    df = make_df()
    trader.fetch_live_frame = lambda count=300: df.tail(count)

    rep = trader.cycle()
    assert rep.ok
    assert rep.action_taken in ("opened", "hold", "closed", "exited", "blocked")

    rep2 = trader.cycle()
    assert rep2.ok
    # دور دوم نباید پوزیشن دوتایی بسازد
    positions = trader._open_positions_for_symbol()
    assert len(positions) <= 1

    events = [json.loads(line) for line in open(journal_path)]
    assert any(e.get("executor") == "cycle" for e in events)


def test_risk_manager_blocks_daily_loss():
    from live_trading import RiskLimitBreached, RiskManager

    risk = RiskManager(max_open_positions=2, max_daily_loss_pct=0.05,
                       stop_loss_pct=0.05)
    risk.record_fill(-6_000)  # بیش از ۵٪ equity صد هزار
    with pytest.raises(RiskLimitBreached):
        risk.check(equity=100_000, open_positions=0,
                   requested_volume=0.1, price=100.0)


def test_risk_manager_caps_position_count():
    from live_trading import RiskLimitBreached, RiskManager

    risk = RiskManager(max_open_positions=1)
    with pytest.raises(RiskLimitBreached):
        risk.check(equity=100_000, open_positions=1,
                   requested_volume=0.1, price=100.0)


def test_risk_manager_scales_volume_to_stop_distance():
    from live_trading import RiskManager

    risk = RiskManager(max_risk_per_trade=0.02, stop_loss_pct=0.05)
    # ریسک مجاز = ۲۰۰۰، فاصله استاپ هر واحد = ۵ → سقف حجم ۴۰۰
    allowed = risk.check(equity=100_000, open_positions=0,
                         requested_volume=10_000, price=100.0)
    assert allowed == pytest.approx(400.0)


def test_paper_executor_stops_and_journalizes(journal_path):
    from live_trading import PaperExecutor, TradeJournal

    ex = PaperExecutor(TradeJournal(journal_path), cash=10_000)
    res = ex.open_position("XAUUSD", +1, 1.0, 2000.0, sl=1900.0, tp=2200.0)
    assert res["retcode"] == 10009
    assert "XAUUSD" in ex.positions
    # فعال شدن حد ضرر در دور بعدی
    ex.mark_prices({"XAUUSD": 1890.0})
    hits = ex.check_exits()
    assert hits and hits[0]["reason"] == "stop_loss"
    assert not ex.positions
    events = [json.loads(line) for line in open(journal_path)]
    closes = [e for e in events if e.get("action") == "close"]
    assert closes and closes[-1]["pnl"] < 0


def test_live_mode_requires_confirmation(monkeypatch):
    from live_trading import LiveTradingError, MT5LiveExecutor

    monkeypatch.delenv("MT5_LIVE_CONFIRM", raising=False)
    with pytest.raises(LiveTradingError):
        MT5LiveExecutor(connector=object(), mode="live")


def test_demo_mode_blocks_real_account(monkeypatch, journal_path):
    from live_trading import LiveTradingError, MT5LiveExecutor

    class FakeConnector:
        def account_info(self):
            return {"login": 9999, "server": "Real-Server",
                    "balance": 5000, "equity": 5000, "currency": "USD"}

    monkeypatch.setenv("MT5_LIVE_ACCOUNT", "9999")
    with pytest.raises(LiveTradingError):
        MT5LiveExecutor(connector=FakeConnector(), mode="demo",
                        journal=TradeJournal(journal_path))


def test_loop_runs_cycles_and_stops(journal_path):
    import time

    from live_trading import LiveTrader, LiveTradingLoop, TradeJournal

    trader = LiveTrader("MA-Crossover", LIVE_PARAMS, symbol="TESTUSD",
                        timeframe="D1", mode="paper",
                        journal=TradeJournal(journal_path))
    df = make_df(n=120)
    trader.fetch_live_frame = lambda count=300: df.tail(count)

    loop = LiveTradingLoop(trader, interval_sec=0.5)
    loop.start()
    time.sleep(2.5)
    assert loop.running
    loop.stop(wait=True, timeout=30)
    assert not loop.running
    assert loop.cycles_done >= 2
    assert all(r["ok"] for r in loop.reports)


def test_resolve_executor_mode_labels():
    from live_trading import resolve_executor_mode

    assert resolve_executor_mode("کاغذی (Paper)") == "paper"
    assert resolve_executor_mode("دمو (Demo)") == "demo"
    assert resolve_executor_mode("واقعی (Live)") == "live"


def test_connector_trading_primitives_missing_package():
    """در محیط بدون پکیج MetaTrader5 فراخوانی‌ها باید خطای مشخص بدهند."""
    from src.python.data.mt5_connector import (MT5ConnectionError,
                                               MetaTraderConnector)

    conn = MetaTraderConnector()
    with pytest.raises(MT5ConnectionError):
        conn.account_info()
