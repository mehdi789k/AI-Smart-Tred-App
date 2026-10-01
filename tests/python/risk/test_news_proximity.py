import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from risk.news_proximity import NewsProximityFilter, evaluate_news_proximity


def test_high_impact_positive_profit_blocks_without_changing_open_orders():
    result = evaluate_news_proximity(
        "2026-09-04T18:00:00+00:00",
        "2026-09-04T18:30:00Z",
        12,
        "critical",
        open_orders=[{"id": 1, "stop_loss": 90}],
    )
    assert result["trading_allowed"] is False
    assert result["risk_free_required"] is False
    assert result["risk_free"] is False
    assert "stop_loss_action" not in result["open_orders"][0]


def test_risk_free_action_is_configurable_and_input_is_not_mutated():
    orders = [{"id": 1}]
    result = evaluate_news_proximity(
        datetime(2026, 9, 4, 18, 0),
        datetime(2026, 9, 4, 18, 30),
        1,
        "red",
        user_action="risk_free",
        open_orders=orders,
    )
    assert result["risk_free"] is True
    assert result["trading_allowed"] is False
    assert "stop_loss_action" not in orders[0]


def test_configurable_mode_uses_user_action():
    result = evaluate_news_proximity(
        "2026-09-04T18:00:00Z",
        "2026-09-04T18:30:00Z",
        1,
        "high",
        mode="configurable",
        user_action="risk_free",
        open_orders=[{"id": 1}],
    )
    assert result["risk_free_required"] is True
    assert result["open_orders"][0]["stop_loss_action"] == "move_to_break_even"


def test_configurable_mode_requires_valid_user_action():
    with pytest.raises(ValueError, match="user_action"):
        evaluate_news_proximity(
            "2026-09-04T18:00:00Z",
            "2026-09-04T18:30:00Z",
            1,
            "high",
            mode="configurable",
        )


@pytest.mark.parametrize("impact", ["low", "medium", "yellow"])
def test_non_high_impact_does_not_trigger(impact):
    result = evaluate_news_proximity(
        "2026-09-04T18:00:00", "2026-09-04T18:30:00", 10, impact
    )
    assert result["trading_allowed"] is True
    assert result["risk_free_required"] is False


def test_non_positive_profit_and_threshold_boundary_do_not_trigger():
    assert evaluate_news_proximity(
        "2026-09-04T18:00:00Z", "2026-09-04T19:00:00Z", 1, "high"
    )["trading_allowed"]
    assert evaluate_news_proximity(
        "2026-09-04T18:00:00Z", "2026-09-04T18:30:00Z", 0, "high"
    )["trading_allowed"]


def test_timezone_offsets_are_compared_as_instants():
    result = evaluate_news_proximity(
        datetime(2026, 9, 4, 21, tzinfo=timezone.utc),
        "2026-09-04T23:30:00+03:00",
        1,
        "high",
    )
    assert result["news_proximity"] is True


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"timestamp": "bad"}, "timestamp"),
        ({"news_now": "bad"}, "news_now"),
        ({"current_profit": math.inf}, "current_profit"),
        ({"impact": "unknown"}, "impact"),
        ({"mode": "pause"}, "mode"),
        ({"user_action": "pause"}, "user_action"),
        ({"threshold_hours": -1}, "threshold"),
    ],
)
def test_invalid_inputs_are_rejected(kwargs, match):
    values = {
        "timestamp": "2026-09-04T18:00:00Z",
        "news_now": "2026-09-04T18:30:00Z",
        "current_profit": 1,
        "impact": "high",
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=match):
        evaluate_news_proximity(**values)


def test_filter_wrapper_and_invalid_orders():
    filt = NewsProximityFilter(mode="risk_free")
    assert filt.check("2026-09-04T18:00:00Z", "2026-09-04T18:30:00Z", 1, "high")[
        "risk_free"
    ]
    with pytest.raises(ValueError, match="mapping"):
        evaluate_news_proximity(
            "2026-09-04T18:00:00Z",
            "2026-09-04T18:30:00Z",
            1,
            "high",
            open_orders=[None],
        )
