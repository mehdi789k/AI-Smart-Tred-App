from pathlib import Path

import pytest

from src.python.execution import (
    Order,
    OrderType,
    ShadowOrderLedger,
    TradingExecution,
)
from src.python.strategies import Signal


def test_shadow_order_is_persisted_without_mt5(tmp_path: Path):
    ledger = ShadowOrderLedger(tmp_path / "shadow.jsonl")
    executor = TradingExecution(mode="shadow", shadow_ledger=ledger)
    order = Order(
        symbol="EURUSD",
        direction="BUY",
        order_type=OrderType.MARKET,
        volume=0.2,
        stop_loss=99.0,
        take_profit=105.0,
    )

    report = executor.execute_order(order, current_price=100.0)

    assert report.success
    assert report.order.status.value == "FILLED"
    assert len(ledger.records()) == 1
    record = ledger.records()[0]
    assert record.status == "would_be_filled"
    assert record.entry_price == 100.0
    assert not hasattr(executor, "mt5_connector") or executor.mt5_connector is None


def test_shadow_order_is_evaluated_at_a_later_real_price(tmp_path: Path):
    ledger = ShadowOrderLedger(tmp_path / "shadow.jsonl")
    executor = TradingExecution(mode="shadow", shadow_ledger=ledger)
    open_order = Order(
        symbol="EURUSD",
        direction="BUY",
        order_type=OrderType.MARKET,
        volume=1.0,
    )
    executor.execute_order(open_order, current_price=100.0)

    close_order = Order(
        symbol="EURUSD",
        direction="SELL",
        order_type=OrderType.MARKET,
        volume=1.0,
        metadata={
            "action": "close_position",
            "shadow_order_id": open_order.order_id,
            "reason": "TP",
        },
    )
    report = executor.execute_order(close_order, current_price=103.0)

    assert report.success
    record = ledger.get(open_order.order_id)
    assert record is not None
    assert record.status == "evaluated"
    assert record.exit_price == 103.0
    assert record.pnl == pytest.approx(2.96)
    assert record.exit_reason == "TP"


def test_shadow_execution_fails_closed_without_a_real_price(tmp_path: Path):
    executor = TradingExecution(
        mode="shadow",
        shadow_ledger=ShadowOrderLedger(tmp_path / "shadow.jsonl"),
    )
    order = Order("EURUSD", "BUY", OrderType.MARKET, 0.1)

    report = executor.execute_order(order)

    assert not report.success
    assert order.status.value == "REJECTED"


def test_shadow_execution_fails_closed_if_ledger_is_unavailable():
    executor = TradingExecution(mode="shadow")
    executor.shadow_ledger = None
    order = Order("EURUSD", "BUY", OrderType.MARKET, 0.1)

    with pytest.raises(RuntimeError, match="shadow ledger"):
        executor.execute_order(order, current_price=100.0)


def test_hold_signal_does_not_create_an_order():
    executor = TradingExecution(mode="simulated")
    signal = Signal("EURUSD", "HOLD", 0.5)

    assert executor.create_order_from_signal(signal, volume=0.1) is None
