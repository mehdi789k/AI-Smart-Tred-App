"""Validation helpers for the OHLCV data contract.

These checks are deliberately independent of SQLAlchemy so they can be used
before persistence, in stream consumers, and by unit tests.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from ..observability import MetricsRegistry
from .config import Timeframe


def _timestamp(row: Mapping[str, Any]) -> datetime:
    value = row.get("timestamp", row.get("time", row.get("time_msc")))
    if value is None:
        raise ValueError("OHLCV row is missing timestamp")
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError("OHLCV timestamp must be datetime, ISO string, or epoch")
    if value.tzinfo is None:
        raise ValueError("OHLCV timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def ohlcv_key(row: Mapping[str, Any]) -> tuple[str, str, datetime]:
    """Return the database natural key in canonical UTC form."""

    return str(row["symbol"]), Timeframe(row["timeframe"]).value, _timestamp(row)


def find_duplicate_keys(
    rows: Iterable[Mapping[str, Any]],
) -> list[tuple[str, str, datetime]]:
    """Return duplicate ``symbol/timeframe/timestamp`` keys once each."""

    keys = [ohlcv_key(row) for row in rows]
    return [key for key, count in Counter(keys).items() if count > 1]


def validate_ohlcv_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    timeframe: Timeframe | str | None = None,
    reject_duplicates: bool = True,
) -> list[tuple[str, str, datetime]]:
    """Validate OHLC bounds, finite values, UTC timestamps and duplicates.

    Returns detected duplicate keys when duplicate rejection is disabled.
    """

    materialized = list(rows)
    duplicates = find_duplicate_keys(materialized)
    if reject_duplicates and duplicates:
        raise ValueError(f"duplicate OHLCV keys: {duplicates[0]}")
    for row in materialized:
        timestamp = _timestamp(row)
        values = [float(row[name]) for name in ("open", "high", "low", "close")]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("OHLCV prices must be finite")
        if (
            not values[2] <= values[0] <= values[1]
            or not values[2] <= values[3] <= values[1]
        ):
            raise ValueError("OHLCV bounds require low <= open/close <= high")
        for name in ("tick_volume", "volume"):
            if row.get(name) is not None and (
                not math.isfinite(float(row[name])) or float(row[name]) < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if timestamp.tzinfo is None:
            raise ValueError("OHLCV timestamp must be timezone-aware UTC")
    return duplicates


def validate_gaps(
    rows: Iterable[Mapping[str, Any]],
    timeframe: Timeframe | str,
    *,
    tolerance: int = 0,
    metrics: MetricsRegistry | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return missing nominal periods between consecutive OHLCV bars."""

    timeframe = Timeframe(timeframe)
    if timeframe.seconds is None:
        return []
    timestamps = sorted({_timestamp(row) for row in rows})
    expected = timeframe.seconds
    gaps = []
    for previous, current in zip(timestamps, timestamps[1:]):
        missing = int((current - previous).total_seconds() // expected) - 1
        if missing > tolerance:
            from datetime import timedelta

            gaps.append(
                (
                    previous + timedelta(seconds=expected),
                    current - timedelta(seconds=expected),
                )
            )
    if metrics is not None and gaps:
        metrics.inc(
            "data_gaps_total",
            value=len(gaps),
            labels={"timeframe": timeframe.value},
        )
    return gaps


__all__ = ["find_duplicate_keys", "ohlcv_key", "validate_gaps", "validate_ohlcv_rows"]
