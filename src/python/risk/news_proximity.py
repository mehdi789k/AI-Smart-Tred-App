"""Pure high-impact-news proximity risk filtering.

The filter deliberately returns plain dictionaries so it can be used by an API
or an execution adapter without introducing side effects.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

DEFAULT_NEWS_THRESHOLD_HOURS = 1.0
HIGH_IMPACT_NEWS = frozenset({"high", "red", "critical"})
VALID_IMPACTS = frozenset(
    {"low", "medium", "yellow", "orange", "high", "red", "critical"}
)
NEWS_MODES = frozenset({"block_new_trades", "risk_free", "configurable"})
NEWS_ACTIONS = frozenset({"block_new_trades", "risk_free"})


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _time(value: Any, name: str) -> datetime:
    """Parse a datetime and normalize both naive and aware values to UTC.

    Naive values are interpreted as UTC.  This makes data from MT5 and ISO
    APIs comparable while still rejecting malformed timestamps.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a valid datetime") from exc
    else:
        raise ValueError(f"{name} must be a datetime or ISO datetime string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _impact(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in VALID_IMPACTS:
        raise ValueError("impact must be a supported news impact level")
    return value.strip().lower()


def _mode(value: Any, name: str = "mode") -> str:
    if not isinstance(value, str) or value.strip().lower() not in NEWS_MODES:
        raise ValueError(
            f"{name} must be 'block_new_trades', 'risk_free', or 'configurable'"
        )
    return value.strip().lower()


def _action(value: Any, name: str = "user_action") -> str:
    if not isinstance(value, str) or value.strip().lower() not in NEWS_ACTIONS:
        raise ValueError(f"{name} must be 'block_new_trades' or 'risk_free'")
    return value.strip().lower()


def _orders(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("open_orders must be a sequence of mappings")
    result = []
    for index, order in enumerate(value):
        if not isinstance(order, Mapping):
            raise ValueError(f"open_orders item at index {index} must be a mapping")
        result.append(dict(order))
    return result


def evaluate_news_proximity(
    timestamp: Any,
    news_now: Any,
    current_profit: Any,
    impact: Any,
    threshold_hours: Any = DEFAULT_NEWS_THRESHOLD_HOURS,
    mode: str = "block_new_trades",
    user_action: str | None = None,
    open_orders: Any = None,
) -> dict[str, Any]:
    """Evaluate one news event against the current market time.

    A stop is active only for high-impact news, a *strictly* less-than
    threshold distance, and positive current profit.
    """
    now = _time(timestamp, "timestamp")
    news = _time(news_now, "news_now")
    profit = _finite(current_profit, "current_profit")
    impact_value = _impact(impact)
    threshold = _finite(threshold_hours, "threshold_hours")
    if threshold < 0:
        raise ValueError("threshold_hours must not be negative")
    selected_mode = _mode(mode)
    action = None if user_action is None else _action(user_action)
    if selected_mode == "configurable":
        if action is None:
            raise ValueError("user_action is required when mode is 'configurable'")
        selected_action = action
    else:
        selected_action = action or selected_mode
    distance_hours = abs((news - now).total_seconds()) / 3600.0
    triggered = (
        impact_value in HIGH_IMPACT_NEWS and distance_hours < threshold and profit > 0
    )
    orders = _orders(open_orders)
    risk_free_selected = triggered and selected_action == "risk_free"
    if risk_free_selected:
        for order in orders:
            order["stop_loss_action"] = "move_to_break_even"
    return {
        "news_proximity": triggered,
        "trading_allowed": not triggered,
        "new_trades_allowed": not triggered,
        "risk_free": risk_free_selected,
        "risk_free_required": risk_free_selected,
        "mode": selected_mode,
        "impact": impact_value,
        "distance_hours": distance_hours,
        "open_orders": orders,
        "stop_loss_action": "move_to_break_even" if triggered else None,
    }


class NewsProximityFilter:
    """Reusable configuration wrapper around :func:`evaluate_news_proximity`."""

    def __init__(
        self,
        threshold_hours: Any = DEFAULT_NEWS_THRESHOLD_HOURS,
        mode: str = "block_new_trades",
        user_action: str | None = None,
    ) -> None:
        self.threshold_hours = _finite(threshold_hours, "threshold_hours")
        if self.threshold_hours < 0:
            raise ValueError("threshold_hours must not be negative")
        self.mode = _mode(mode)
        self.user_action = None if user_action is None else _action(user_action)

    def evaluate(
        self,
        timestamp: Any,
        news_now: Any,
        current_profit: Any,
        impact: Any,
        open_orders: Any = None,
    ) -> dict[str, Any]:
        return evaluate_news_proximity(
            timestamp,
            news_now,
            current_profit,
            impact,
            self.threshold_hours,
            self.mode,
            self.user_action,
            open_orders,
        )

    check = evaluate


check_news_proximity = evaluate_news_proximity
