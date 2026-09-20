import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from risk.break_even import BreakEvenConfig, apply_break_even, plan_break_even

from src.python.execution.position import Position, PositionManager


def position(direction="BUY", price=110, stop=95):
    return {
        "direction": direction,
        "entry_price": 100,
        "current_price": price,
        "stop_loss": stop,
    }


def test_break_even_requires_configured_r_multiple():
    assert not plan_break_even(
        position(price=104), config=BreakEvenConfig(trigger_r=1)
    ).should_move
    decision = plan_break_even(position(price=105), config=BreakEvenConfig(trigger_r=1))
    assert decision.should_move and decision.new_stop_loss == 100


def test_sell_and_positive_offset_move_stop_favourably():
    decision = plan_break_even(
        position("SELL", price=90, stop=105),
        config=BreakEvenConfig(trigger_r=1, entry_offset=0.2),
    )
    assert decision.new_stop_loss == pytest.approx(99.8)


def test_never_loosen_stop_or_place_offset_beyond_market():
    assert (
        plan_break_even(position(price=110, stop=100.5)).reason
        == "invalid_initial_stop"
    )
    assert (
        plan_break_even(
            position(price=105.1, stop=95),
            config=BreakEvenConfig(trigger_r=1, entry_offset=6),
        ).reason
        == "offset_not_reached"
    )


def test_invalid_initial_stop_and_disabled_rules_are_safe():
    assert (
        plan_break_even(position(stop=100), config=BreakEvenConfig()).reason
        == "invalid_initial_stop"
    )
    assert (
        plan_break_even(position(), config=BreakEvenConfig(enabled=False)).reason
        == "disabled"
    )


def test_apply_is_explicit_and_mutates_only_after_decision():
    target = type("Position", (), position())()
    target.to_dict = lambda: position()
    decision = apply_break_even(target, config=BreakEvenConfig())
    assert decision.should_move
    assert target.stop_loss == 100


def test_position_manager_applies_break_even_only_to_existing_local_position():
    manager = PositionManager()
    manager.positions["EURUSD"] = Position("EURUSD", "BUY", 1, 100, 105, stop_loss=95)
    decision = manager.apply_break_even("EURUSD", BreakEvenConfig())
    assert decision.should_move
    assert manager.positions["EURUSD"].stop_loss == 100
    assert manager.apply_break_even("MISSING").reason == "position_not_found"
