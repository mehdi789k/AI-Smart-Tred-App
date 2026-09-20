"""Daily loss and timestamp-aware consecutive-loss risk filters."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

DEFAULT_CONSECUTIVE_LOSS_LIMIT = 5
DEFAULT_LOSS_RESET_INTERVAL_HOURS = 1.0


def _positive_number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _limit(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("consecutive_loss_limit must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("consecutive_loss_limit must be a positive integer") from exc
    if result <= 0 or result != float(value):
        raise ValueError("consecutive_loss_limit must be a positive integer")
    return result


def _reset_interval(
    reset_interval_hours: Any = None, loss_reset_interval_hours: Any = None
) -> float:
    values = [
        v for v in (reset_interval_hours, loss_reset_interval_hours) if v is not None
    ]
    if not values:
        return DEFAULT_LOSS_RESET_INTERVAL_HOURS
    try:
        result = float(values[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("reset interval must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("reset interval must be a positive finite number")
    if len(values) == 2:
        try:
            other = float(values[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("reset interval must be a positive finite number") from exc
        if not math.isfinite(other) or other <= 0:
            raise ValueError("reset interval must be a positive finite number")
        if other != result:
            raise ValueError(
                "reset_interval_hours and loss_reset_interval_hours must match"
            )
    return result


def _trade_time(
    trade: Mapping[str, Any], default_day: date
) -> tuple[date, datetime | None]:
    value = trade.get("timestamp", trade.get("date"))
    if value is None:
        return default_day, None
    if isinstance(value, datetime):
        return value.date(), value
    if isinstance(value, date):
        return value, datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date(), parsed
        except ValueError:
            try:
                parsed_date = date.fromisoformat(value)
                return parsed_date, datetime.combine(parsed_date, datetime.min.time())
            except ValueError as exc:
                raise ValueError(
                    "trade timestamp/date must be a valid ISO date"
                ) from exc
    raise ValueError("trade timestamp/date must be a date, datetime, or ISO string")


def _current_day(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError("current_date must be a valid ISO date") from exc
    raise ValueError("current_date must be a date or ISO date string")


def evaluate_daily_loss(
    trades: Sequence[Mapping[str, Any]],
    base_capital: Any,
    consecutive_loss_limit: Any = DEFAULT_CONSECUTIVE_LOSS_LIMIT,
    max_daily_loss_percent: Any = 2.0,
    current_date: date | str | None = None,
    reset_interval_hours: Any = None,
    loss_reset_interval_hours: Any = None,
) -> dict[str, Any]:
    """Evaluate closed trades; calendar days have independent loss chains."""
    capital = _positive_number(base_capital, "base_capital")
    limit = _limit(consecutive_loss_limit)
    percent = _positive_number(max_daily_loss_percent, "max_daily_loss_percent")
    reset_hours = _reset_interval(reset_interval_hours, loss_reset_interval_hours)
    if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
        raise ValueError("trades must be a sequence of mappings")
    default_day = _current_day(current_date)
    grouped: dict[date, list[tuple[int, float, bool, datetime | None]]] = {}
    awareness: bool | None = None
    for index, trade in enumerate(trades):
        if not isinstance(trade, Mapping):
            raise ValueError(f"trade at index {index} must be a mapping")
        if "pnl" not in trade:
            raise ValueError(f"trade at index {index} is missing 'pnl'")
        try:
            pnl = float(trade["pnl"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"trade at index {index} pnl must be a finite number"
            ) from exc
        if not math.isfinite(pnl):
            raise ValueError(f"trade at index {index} pnl must be a finite number")
        day, timestamp = _trade_time(trade, default_day)
        if timestamp is not None:
            aware = timestamp.tzinfo is not None and timestamp.utcoffset() is not None
            if awareness is not None and aware != awareness:
                raise ValueError(
                    "trade timestamps must be comparable (all naive or all timezone-aware)"
                )
            awareness = aware
        status = str(trade.get("status", "")).lower()
        is_open = (
            bool(trade.get("is_open", False))
            or trade.get("closed") is False
            or status in {"open", "pending"}
        )
        grouped.setdefault(day, []).append((index, pnl, is_open, timestamp))

    threshold = capital * percent / 100.0
    total_pnl = 0.0
    max_consecutive = 0
    stopped_day: date | None = None
    stop_reason: str | None = None
    for day in sorted(grouped):
        chain = 0
        last_loss_time: datetime | None = None
        day_pnl = 0.0
        day_trades = grouped[day]
        # A timestamp gives us the actual trade chronology (callers may provide
        # records in any order).  If even one record has no timestamp, retain
        # the legacy input-order behavior for the whole day so that an
        # un-timestamped record is never placed arbitrarily in the chain.
        if day_trades and all(item[3] is not None for item in day_trades):
            ordered_trades = sorted(
                day_trades,
                key=lambda item: item[3] if item[3] is not None else datetime.min,
            )
        else:
            ordered_trades = sorted(day_trades, key=lambda item: item[0])
        for _, pnl, is_open, timestamp in ordered_trades:
            if is_open:
                chain, last_loss_time = 0, None
                continue
            day_pnl += pnl
            if pnl < 0:
                if last_loss_time is not None and timestamp is not None:
                    try:
                        elapsed = timestamp - last_loss_time
                    except TypeError as exc:
                        raise ValueError("trade timestamps must be comparable") from exc
                    if elapsed >= timedelta(hours=reset_hours):
                        chain = 0
                chain += 1
                max_consecutive = max(max_consecutive, chain)
                if timestamp is not None:
                    last_loss_time = timestamp
            else:
                chain, last_loss_time = 0, None
            if stopped_day is None and chain >= limit:
                stopped_day = day
                stop_reason = (
                    f"{limit} consecutive losing closed positions on {day.isoformat()}"
                )
        total_pnl += day_pnl
        if stopped_day is None and day_pnl <= -threshold:
            stopped_day = day
            stop_reason = (
                f"daily loss of {abs(day_pnl):.2f} reached "
                f"{percent:g}% of base capital on {day.isoformat()}"
            )
    return {
        "trading_allowed": stopped_day is None,
        "stop_reason": stop_reason,
        "daily_pnl": total_pnl,
        "max_consecutive_losses": max_consecutive,
        "stopped_day": stopped_day.isoformat() if stopped_day else None,
    }


class DailyLossFilter:
    def __init__(
        self,
        base_capital: Any,
        consecutive_loss_limit: Any = DEFAULT_CONSECUTIVE_LOSS_LIMIT,
        max_daily_loss_percent: Any = 2.0,
        reset_interval_hours: Any = None,
        loss_reset_interval_hours: Any = None,
    ) -> None:
        self.base_capital = _positive_number(base_capital, "base_capital")
        self.consecutive_loss_limit = _limit(consecutive_loss_limit)
        self.max_daily_loss_percent = _positive_number(
            max_daily_loss_percent, "max_daily_loss_percent"
        )
        self.reset_interval_hours = _reset_interval(
            reset_interval_hours, loss_reset_interval_hours
        )
        self.loss_reset_interval_hours = self.reset_interval_hours

    def evaluate(
        self,
        trades: Sequence[Mapping[str, Any]],
        current_date: date | str | None = None,
    ) -> dict[str, Any]:
        return evaluate_daily_loss(
            trades,
            self.base_capital,
            self.consecutive_loss_limit,
            self.max_daily_loss_percent,
            current_date,
            self.reset_interval_hours,
        )

    check = evaluate


MaxDailyLossFilter = DailyLossFilter
check_daily_loss = evaluate_daily_loss
check_max_daily_loss = evaluate_daily_loss
