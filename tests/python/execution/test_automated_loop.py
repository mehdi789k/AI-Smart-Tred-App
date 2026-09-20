import time
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.python.execution import AutoTrader, LiveTradingLoop, TradingConfig
from src.python.execution.auto_trader import TradingCycle
from src.python.execution.live_order_workflow import (
    LiveOrderConfig,
    LiveOrderRejected,
    LiveOrderWorkflow,
)
from src.python.execution.position import Position
from src.python.strategies import Signal


class FakeMT5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1

    def __init__(self):
        self.sent = []

    def positions_get(self):
        return []

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=110.0, ask=110.2)

    def order_check(self, request):
        return SimpleNamespace(retcode=0)

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=10009, order=42)

    def last_error(self):
        return (0, "ok")


class FakeConnector:
    def __init__(self):
        self._mt5 = FakeMT5()

    def is_connected(self):
        return True

    def get_symbols_list(self, visible_only=True):
        return [{"symbol": "EURUSD"}]

    def get_history(self, days=1):
        return pd.DataFrame()

    def get_symbol_info(self, symbol):
        return {"spread": 0.1}


def make_loop():
    connector = FakeConnector()
    workflow = LiveOrderWorkflow(
        connector,
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=0.2,
            max_daily_loss=100,
            max_spread=1,
        ),
    )
    trader = AutoTrader(
        TradingConfig(
            account_balance=10_000,
            symbols=["EURUSD"],
            execution_mode="live",
            max_positions=2,
        )
    )
    return LiveTradingLoop(trader, workflow, connector, lambda *_: ({}, {})), workflow


def test_user_sl_tp_settings_support_fixed_and_percent_distances():
    trader = AutoTrader(
        TradingConfig(sl_tp_mode="fixed", stop_loss_value=2.0, take_profit_value=3.0)
    )
    buy = trader._apply_sl_tp_settings(Signal("EURUSD", "BUY", 0.8, entry_price=100.0))
    assert (buy.stop_loss, buy.take_profit) == (98.0, 103.0)

    trader.config.sl_tp_mode = "percent"
    trader.config.stop_loss_value = 1.0
    trader.config.take_profit_value = 2.0
    sell = trader._apply_sl_tp_settings(
        Signal("EURUSD", "SELL", 0.8, entry_price=100.0)
    )
    assert (sell.stop_loss, sell.take_profit) == (101.0, 98.0)


def test_fixed_sell_protection_uses_current_ask_reference():
    trader = AutoTrader(
        TradingConfig(sl_tp_mode="fixed", stop_loss_value=1.0, take_profit_value=2.0)
    )
    sell = trader._apply_sl_tp_settings(
        Signal("EURUSD", "SELL", 0.8, entry_price=100.0),
        market_price=101.5,
    )
    assert sell.stop_loss == 102.5
    assert sell.take_profit == 98.0


def test_server_gate_and_confirmation_are_required(monkeypatch):
    loop, workflow = make_loop()
    monkeypatch.delenv("MT5_AUTO_TRADING_ENABLED", raising=False)
    token = workflow.request_confirmation("start_auto_trading")
    with pytest.raises(LiveOrderRejected) as error:
        loop.start(token)
    assert error.value.code == "auto_trading_gate_disabled"
    assert not loop.active


def test_confirmed_loop_starts_without_sending_an_order(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    token = workflow.request_confirmation("start_auto_trading")
    loop.start(token)
    assert loop.active
    assert loop.run_once() is None
    assert workflow.connector._mt5.positions_get() == []
    loop.stop()
    assert not loop.active


def test_authorized_loop_can_resume_after_dashboard_rerun(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()
    assert loop.active
    assert loop.last_status == "restored"
    loop.stop()


def test_loop_reconciles_position_closed_by_broker(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()

    position = Position(
        symbol="EURUSD",
        direction="BUY",
        volume=0.1,
        entry_price=1.0,
        current_price=1.0,
        stop_loss=None,
        take_profit=None,
    )
    loop.trader.position_manager.positions["EURUSD"] = position
    loop.connector._mt5.positions_get = lambda: []
    loop.data_provider = lambda *_: ({"EURUSD": {}}, {"EURUSD": 1.1})

    loop.run_once()

    assert "EURUSD" not in loop.trader.position_manager.positions
    loop.stop()


def test_loop_fetches_each_symbol_with_its_configured_timeframe(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    connector = FakeConnector()
    workflow = LiveOrderWorkflow(
        connector,
        LiveOrderConfig(
            frozenset({"EURUSD", "GBPUSD"}),
            magic=7,
            max_position_volume=0.2,
            max_daily_loss=100,
            max_spread=1,
        ),
    )
    trader = AutoTrader(
        TradingConfig(
            account_balance=10_000,
            symbols=["EURUSD", "GBPUSD"],
            timeframes=["M5", "H1"],
            symbol_timeframes={"EURUSD": "M5", "GBPUSD": "H1"},
            execution_mode="live",
            max_positions=2,
        )
    )
    calls = []

    def provider(symbols, timeframe):
        calls.append((symbols, timeframe))
        return {}, {}

    loop = LiveTradingLoop(trader, workflow, connector, provider)
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()
    assert loop.run_once() is None
    assert calls == [(["EURUSD"], "M5"), (["GBPUSD"], "H1")]


def test_loop_stops_after_broker_disables_trading(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()
    loop.data_provider = lambda *_: ({"EURUSD": {"close": 110.0}}, {"EURUSD": 110.0})
    loop.trader.run_cycle = lambda *_: loop.trader.cycle_history.append(
        TradingCycle(
            timestamp=datetime.now(),
            signals_generated=1,
            errors=["Order failed for EURUSD: MT5 error: retcode 10017"],
        )
    ) or loop.trader.cycle_history[-1]

    loop.run_once()

    assert loop.active is False
    assert loop.last_status == "broker_trading_disabled"


def test_live_loop_moves_managed_buy_stop_to_break_even(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    broker_position = SimpleNamespace(
        symbol="EURUSD",
        ticket=42,
        magic=7,
        volume=0.1,
        type=0,
        price_open=100.0,
        price_current=110.0,
        sl=95.0,
        tp=120.0,
    )
    loop.connector._mt5.positions_get = lambda: [broker_position]
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()

    loop.data_provider = lambda *_: ({}, {})
    assert loop.run_once() is None

    assert len(loop.connector._mt5.sent) == 1
    request = loop.connector._mt5.sent[0]
    assert request["action"] == loop.connector._mt5.TRADE_ACTION_SLTP
    assert request["position"] == 42
    assert request["sl"] == 100.0
    assert loop.last_break_even_actions == [
        {"symbol": "EURUSD", "ticket": 42, "stop_loss": 100.0, "status": "applied"}
    ]


def test_live_loop_does_not_move_break_even_before_threshold(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    broker_position = SimpleNamespace(
        symbol="EURUSD",
        ticket=42,
        magic=7,
        volume=0.1,
        type=0,
        price_open=100.0,
        price_current=104.0,
        sl=95.0,
        tp=120.0,
    )
    loop.connector._mt5.positions_get = lambda: [broker_position]
    loop.connector._mt5.symbol_info_tick = lambda symbol: SimpleNamespace(
        bid=104.0, ask=104.2
    )
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()
    loop.data_provider = lambda *_: ({}, {})

    assert loop.run_once() is None
    assert loop.connector._mt5.sent == []


def test_live_loop_partial_close_does_not_log_stale_break_even_error(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    loop, workflow = make_loop()
    loop.trader.config.partial_close_enabled = True
    loop.trader.config.partial_close_trigger_r = 1.0
    loop.trader.config.partial_close_percent = 50.0
    broker_position = SimpleNamespace(
        symbol="EURUSD",
        ticket=42,
        magic=7,
        volume=0.1,
        type=0,
        price_open=100.0,
        price_current=110.0,
        sl=95.0,
        tp=120.0,
    )
    loop.connector._mt5.positions_get = lambda: [broker_position]
    workflow.restore_automation_authorization(time.time() + 30)
    loop.restore_active()
    loop.data_provider = lambda *_: ({}, {})

    assert loop.run_once() is None

    assert len(loop.connector._mt5.sent) == 2
    assert loop.last_status != "error"
