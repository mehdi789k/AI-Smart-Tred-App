"""Fail-closed break-even stop planning.

The planner is deliberately side-effect free.  It calculates a favourable
stop-loss change, but never talks to a broker or mutates a position.  Callers
must explicitly apply the returned decision (and live callers must use their
existing confirmation gate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BreakEvenConfig:
    """Rules used to decide when a stop may be moved to break-even."""

    trigger_r: float = 1.0
    entry_offset: float = 0.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.trigger_r)) or self.trigger_r <= 0:
            raise ValueError("trigger_r must be a positive finite number")
        if not math.isfinite(float(self.entry_offset)) or self.entry_offset < 0:
            raise ValueError("entry_offset must be a finite non-negative number")


@dataclass(frozen=True)
class BreakEvenDecision:
    """A proposed stop update; ``should_move`` is false when no action is safe."""

    should_move: bool
    new_stop_loss: float | None = None
    reason: str = ""


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def plan_break_even(
    position: Mapping[str, Any],
    current_price: Any = None,
    config: BreakEvenConfig | None = None,
) -> BreakEvenDecision:
    """Return a safe break-even proposal without changing ``position``.

    ``position`` needs ``direction``, ``entry_price`` and ``stop_loss``.
    The initial risk is the original stop distance.  The stop is only moved
    once, in the profitable direction, after ``trigger_r`` times that risk.
    """
    rules = config or BreakEvenConfig()
    if not rules.enabled:
        return BreakEvenDecision(False, reason="disabled")
    direction = str(position.get("direction", "")).upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    entry = _number(position.get("entry_price"), "entry_price")
    stop = _number(position.get("stop_loss"), "stop_loss")
    price = _number(
        position.get("current_price") if current_price is None else current_price,
        "current_price",
    )
    risk = entry - stop if direction == "BUY" else stop - entry
    if risk <= 0:
        return BreakEvenDecision(False, reason="invalid_initial_stop")
    profit = price - entry if direction == "BUY" else entry - price
    if profit < risk * rules.trigger_r:
        return BreakEvenDecision(False, reason="threshold_not_reached")
    proposed = (
        entry + rules.entry_offset if direction == "BUY" else entry - rules.entry_offset
    )
    # Never loosen a stop and never place it beyond the current market.
    if direction == "BUY":
        if proposed <= stop:
            return BreakEvenDecision(False, reason="stop_already_at_or_better")
        if proposed >= price:
            return BreakEvenDecision(False, reason="offset_not_reached")
    else:
        if proposed >= stop:
            return BreakEvenDecision(False, reason="stop_already_at_or_better")
        if proposed <= price:
            return BreakEvenDecision(False, reason="offset_not_reached")
    return BreakEvenDecision(True, proposed, "threshold_reached")


def apply_break_even(
    position: Any,
    current_price: Any = None,
    config: BreakEvenConfig | None = None,
) -> BreakEvenDecision:
    """Apply a planned update to a mutable position object explicitly."""
    decision = plan_break_even(
        position.to_dict() if hasattr(position, "to_dict") else position,
        current_price,
        config,
    )
    if decision.should_move:
        position.stop_loss = decision.new_stop_loss
    return decision
