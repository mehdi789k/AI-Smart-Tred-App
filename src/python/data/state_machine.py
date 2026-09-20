"""Explicit lifecycle rules for persisted orders and positions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ORDER_TRANSITIONS: Mapping[str | None, frozenset[str]] = {
    None: frozenset({"pending"}),
    "pending": frozenset({"accepted", "rejected", "cancelled", "expired", "unknown"}),
    "accepted": frozenset({"partial", "filled", "cancelled", "rejected", "unknown"}),
    "partial": frozenset({"partial", "filled", "cancelled", "unknown"}),
    "unknown": frozenset({"accepted", "partial", "filled", "rejected", "cancelled", "expired"}),
    "filled": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
}

POSITION_TRANSITIONS: Mapping[str | None, frozenset[str]] = {
    None: frozenset({"open"}),
    "open": frozenset({"partial", "closed"}),
    "partial": frozenset({"partial", "open", "closed"}),
    "closed": frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a lifecycle transition is not explicitly permitted."""


def _status_value(status: Any) -> str | None:
    if status is None:
        return None
    return str(getattr(status, "value", status)).lower()


def validate_transition(
    transitions: Mapping[str | None, frozenset[str]],
    current: Any,
    target: Any,
) -> tuple[str | None, str]:
    """Validate and normalize a lifecycle transition."""

    current_value = _status_value(current)
    target_value = _status_value(target)
    if target_value not in transitions.get(current_value, frozenset()):
        source = current_value or "<new>"
        raise InvalidTransition(
            f"invalid lifecycle transition: {source} -> {target_value}"
        )
    return current_value, target_value


def validate_order_transition(current: Any, target: Any) -> tuple[str | None, str]:
    """Validate an order lifecycle transition."""

    return validate_transition(ORDER_TRANSITIONS, current, target)


def validate_position_transition(current: Any, target: Any) -> tuple[str | None, str]:
    """Validate a position lifecycle transition."""

    return validate_transition(POSITION_TRANSITIONS, current, target)
