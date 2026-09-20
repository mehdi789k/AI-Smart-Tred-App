from types import SimpleNamespace

import pytest

from src.python.mt5_account import mt5_manage_orders, mt5_trade_orders
from src.python.mt5_account.mt5_trade_orders import validate_legacy_order_gate


def _enable_demo_legacy(monkeypatch):
    monkeypatch.setenv("MT5_LEGACY_ORDER_PATH_ENABLED", "true")
    monkeypatch.setenv("MT5_DEMO_ENABLED", "true")
    monkeypatch.setenv("MT5_LIVE_SYMBOLS", "XAUUSD, EURUSD")
    monkeypatch.setenv("MT5_MAX_POSITION_VOLUME", "0.10")
    monkeypatch.setenv("MT5_LIVE_MAGIC", "26090901")


def test_legacy_path_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MT5_LEGACY_ORDER_PATH_ENABLED", raising=False)
    monkeypatch.setenv("MT5_DEMO_ENABLED", "true")

    with pytest.raises(RuntimeError, match="Legacy MT5 order path is disabled"):
        validate_legacy_order_gate("XAUUSD", 0.01)


def test_legacy_path_requires_demo_flag(monkeypatch):
    monkeypatch.setenv("MT5_LEGACY_ORDER_PATH_ENABLED", "true")
    monkeypatch.delenv("MT5_DEMO_ENABLED", raising=False)

    with pytest.raises(RuntimeError, match="MT5_DEMO_ENABLED=true"):
        validate_legacy_order_gate("XAUUSD", 0.01)


def test_legacy_gate_enforces_symbol_volume_and_magic(monkeypatch):
    _enable_demo_legacy(monkeypatch)

    with pytest.raises(ValueError, match="MT5_LIVE_SYMBOLS"):
        validate_legacy_order_gate("GBPUSD", 0.01, magic=26090901)
    with pytest.raises(ValueError, match="MT5_MAX_POSITION_VOLUME"):
        validate_legacy_order_gate("XAUUSD", 0.11, magic=26090901)
    with pytest.raises(ValueError, match="magic"):
        validate_legacy_order_gate("XAUUSD", 0.01, magic=7)


def test_non_dry_run_requires_confirmation(monkeypatch):
    _enable_demo_legacy(monkeypatch)

    with pytest.raises(RuntimeError, match="confirmation_token"):
        validate_legacy_order_gate("XAUUSD", 0.01, magic=26090901, dry_run=False)


def test_dry_run_returns_normalized_limits(monkeypatch):
    _enable_demo_legacy(monkeypatch)

    result = validate_legacy_order_gate("xauusd", 0.01, magic=26090901)

    assert result["max_position_volume"] == 0.10
    assert result["magic"] == 26090901
    assert result["allowed_symbols"] == {"XAUUSD", "EURUSD"}


def test_market_dry_run_never_calls_order_send(monkeypatch):
    _enable_demo_legacy(monkeypatch)
    info = SimpleNamespace(
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        trade_stops_level=0,
        point=0.01,
    )
    tick = SimpleNamespace(bid=100.0, ask=100.1)
    monkeypatch.setattr(mt5_trade_orders, "_prepare_symbol", lambda _: (info, tick))
    monkeypatch.setattr(
        mt5_trade_orders,
        "_send_order",
        lambda _: pytest.fail("order_send must not run in dry-run mode"),
    )

    result = mt5_trade_orders.send_market_order("XAUUSD", "BUY", 0.01)

    assert result["status"] == "dry_run"
    assert result["request"]["magic"] == 26090901


def test_legacy_live_submission_is_not_a_public_order_entrypoint(monkeypatch):
    _enable_demo_legacy(monkeypatch)

    with pytest.raises(RuntimeError, match="LiveOrderWorkflow"):
        mt5_trade_orders.send_market_order(
            "XAUUSD",
            "BUY",
            0.01,
            dry_run=False,
            confirmation_token="legacy-token",
        )


def test_management_path_fails_closed_before_mt5_queries(monkeypatch):
    monkeypatch.delenv("MT5_LEGACY_ORDER_PATH_ENABLED", raising=False)
    monkeypatch.setattr(
        mt5_manage_orders.mt5,
        "positions_get",
        lambda **_: pytest.fail("MT5 must not be queried when legacy path is disabled"),
    )

    with pytest.raises(RuntimeError, match="Legacy MT5 order path is disabled"):
        mt5_manage_orders.close_all_positions()
