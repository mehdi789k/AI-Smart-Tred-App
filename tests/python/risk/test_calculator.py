import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from risk.calculator import (
    DEFAULT_MINIMUM_RR,
    calculate_position_size,
    calculate_risk_reward_ratio,
    filter_minimum_risk_reward,
    meets_minimum_risk_reward,
    normalize_protection_prices,
)


def test_buy_ratio_and_default_threshold_are_two_to_one():
    assert DEFAULT_MINIMUM_RR == 2.0
    assert calculate_risk_reward_ratio(100, 95, 110, "buy") == 2.0
    assert meets_minimum_risk_reward(100, 95, 110)


def test_sell_ratio_and_filter_remove_setups_below_threshold():
    setups = [
        {
            "id": "accepted",
            "entry_price": 100,
            "stop_loss": 105,
            "take_profit": 90,
            "direction": "sell",
        },
        {
            "id": "removed",
            "entry_price": 100,
            "stop_loss": 105,
            "take_profit": 95,
            "direction": "sell",
        },
    ]
    assert [item["id"] for item in filter_minimum_risk_reward(setups)] == ["accepted"]


def test_custom_threshold_is_supported():
    assert not meets_minimum_risk_reward(100, 95, 110, minimum_rr=2.1)
    assert filter_minimum_risk_reward(
        [{"entry_price": 100, "stop_loss": 95, "take_profit": 115}],
        minimum_rr=3,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entry_price": 100, "stop_loss": 100, "take_profit": 110},
        {"entry_price": 100, "stop_loss": 95, "take_profit": 100},
        {"entry_price": 100, "stop_loss": 105, "take_profit": 110},
    ],
)
def test_invalid_trade_geometry_is_rejected(kwargs):
    with pytest.raises(ValueError, match="positive"):
        calculate_risk_reward_ratio(**kwargs)


def test_invalid_threshold_and_non_finite_values_have_clear_errors():
    with pytest.raises(ValueError, match="minimum_rr"):
        meets_minimum_risk_reward(100, 95, 110, minimum_rr=0)
    with pytest.raises(ValueError, match="finite"):
        calculate_risk_reward_ratio(math.inf, 95, 110)


def test_missing_or_invalid_setup_is_rejected():
    with pytest.raises(ValueError, match="missing 'take_profit'"):
        filter_minimum_risk_reward([{"entry_price": 100, "stop_loss": 95}])
    with pytest.raises(ValueError, match="mapping"):
        filter_minimum_risk_reward([None])


@pytest.mark.parametrize(
    ("direction", "sl", "tp"),
    [("BUY", 98, 103), ("SELL", 102, 97)],
)
def test_protection_prices_use_executable_bid_ask(direction, sl, tp):
    normalized = normalize_protection_prices(
        direction, bid=100, ask=101, stop_loss=sl, take_profit=tp
    )
    assert normalized == (sl, tp)


def test_sell_sl_at_entry_is_rejected_against_ask():
    with pytest.raises(ValueError, match="invalid"):
        normalize_protection_prices(
            "SELL", bid=100, ask=101, stop_loss=100.5, take_profit=98
        )


def test_invalid_protection_can_be_safely_corrected():
    sl, tp = normalize_protection_prices(
        "SELL",
        bid=100,
        ask=101,
        stop_loss=100,
        take_profit=102,
        minimum_distance=0.1,
        auto_correct=True,
    )
    assert sl > 101 and tp < 100


def test_fixed_fractional_position_size_and_cap():
    assert (
        calculate_position_size(10_000, 100, 95, risk_percent=1, value_per_unit=1) == 20
    )
    assert (
        calculate_position_size(10_000, 100, 95, risk_percent=1, max_position_size=10)
        == 10
    )
