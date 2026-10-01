import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from risk.manager import DailyLossFilter, evaluate_daily_loss


def test_default_five_consecutive_losses_stop_trading():
    result = evaluate_daily_loss(
        [{"pnl": -1, "date": "2026-01-02"} for _ in range(5)],
        1000,
    )
    assert result["trading_allowed"] is False
    assert "consecutive" in result["stop_reason"]


def test_profit_and_open_trade_reset_loss_chain():
    trades = [
        {"pnl": -10, "date": "2026-01-02"},
        {"pnl": 2, "date": "2026-01-02"},
        {"pnl": -10, "date": "2026-01-02", "status": "open"},
        {"pnl": 2, "date": "2026-01-02"},
        {"pnl": -10, "date": "2026-01-02"},
    ]
    assert evaluate_daily_loss(trades, 1000)["trading_allowed"] is True


def test_days_are_independent_and_percent_limit_is_inclusive():
    result = evaluate_daily_loss(
        [{"pnl": -10, "date": "2026-01-01"}, {"pnl": -10, "date": "2026-01-02"}],
        1000,
    )
    assert result["trading_allowed"] is True
    result = evaluate_daily_loss([{"pnl": -20, "date": "2026-01-02"}], 1000)
    assert result["trading_allowed"] is False
    assert "daily loss" in result["stop_reason"]


def test_configurable_limits_and_missing_date():
    result = DailyLossFilter(1000, consecutive_loss_limit=3).check(
        [{"pnl": -1}, {"pnl": -1}]
    )
    assert result["trading_allowed"] is True
    assert evaluate_daily_loss([], 100, current_date=date.today())["trading_allowed"]


def test_timestamp_resets_chain_after_default_one_hour():
    trades = [
        {"pnl": -1, "timestamp": "2026-01-02T10:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T10:30:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:31:00"},
        {"pnl": -1, "timestamp": "2026-01-02T12:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T12:00:00"},
    ]
    assert evaluate_daily_loss(trades, 1000)["trading_allowed"] is True


def test_losses_less_than_one_hour_keep_chain():
    trades = [
        {"pnl": -1, "timestamp": f"2026-01-02T10:{minute:02d}:00"}
        for minute in (0, 10, 20, 30, 40)
    ]
    assert not evaluate_daily_loss(trades, 1000)["trading_allowed"]


def test_timestamp_order_is_used_when_records_are_out_of_order():
    # Chronological order is five losses within one hour.  Input order would
    # otherwise incorrectly make the first loss appear more than an hour ago.
    trades = [
        {"pnl": -1, "timestamp": "2026-01-02T12:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T10:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T10:30:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:30:00"},
    ]
    assert evaluate_daily_loss(trades, 1000)["trading_allowed"] is False


def test_custom_loss_reset_interval_and_input_order_fallback():
    trades = [
        {"pnl": -1, "timestamp": "2026-01-02T10:00:00"},
        {"pnl": -1, "timestamp": "2026-01-02T10:30:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:31:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:40:00"},
        {"pnl": -1, "timestamp": "2026-01-02T11:50:00"},
    ]
    assert not evaluate_daily_loss(trades, 1000, reset_interval_hours=2)[
        "trading_allowed"
    ]
    assert evaluate_daily_loss([{"pnl": -1} for _ in range(4)], 1000)["trading_allowed"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reset_interval_hours": 0},
        {"loss_reset_interval_hours": -1},
        {"reset_interval_hours": 1, "loss_reset_interval_hours": 2},
    ],
)
def test_invalid_reset_interval_raises(kwargs):
    with pytest.raises(ValueError, match="interval"):
        evaluate_daily_loss([], 100, **kwargs)


def test_uncomparable_timestamp_kinds_raise():
    with pytest.raises(ValueError, match="comparable"):
        evaluate_daily_loss(
            [
                {"pnl": -1, "timestamp": "2026-01-02T10:00:00"},
                {"pnl": -1, "timestamp": "2026-01-02T11:00:00+00:00"},
            ],
            100,
        )


@pytest.mark.parametrize(
    "trades,capital,match",
    [
        ([{"pnl": "bad"}], 100, "pnl"),
        ([{"pnl": 1}], 0, "base_capital"),
        ([{"pnl": 1}], 100, "consecutive_loss_limit"),
        ([{"pnl": float("nan")}], 100, "pnl"),
    ],
)
def test_invalid_inputs_raise_clear_value_errors(trades, capital, match):
    kwargs = {"consecutive_loss_limit": 0} if match == "consecutive_loss_limit" else {}
    with pytest.raises(ValueError, match=match):
        evaluate_daily_loss(trades, capital, **kwargs)
