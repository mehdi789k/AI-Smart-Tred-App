"""Versioned market-candle contract validation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

CURRENT_SCHEMA_VERSION = 1
_TIMESTAMP_FIELDS = ("timestamp", "time_iso", "time", "time_msc")
_REQUIRED_PROVENANCE = {"ingestion_id", "received_at", "collector_version"}
_UNSPECIFIED_VALUES = {"", "unknown", "none", "n/a"}


class CandleValidationError(ValueError):
    """Raised when a market candle violates the versioned data contract."""


def _utc_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, bool):
        raise CandleValidationError(f"{field_name} must be an explicit UTC timestamp")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise CandleValidationError(f"{field_name} must be finite")
        epoch = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise CandleValidationError(
                f"{field_name} is outside the supported range"
            ) from error
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise CandleValidationError(
                f"{field_name} must be a valid ISO-8601 timestamp"
            ) from error
    if not isinstance(value, datetime) or value.utcoffset() != timezone.utc.utcoffset(
        value
    ):
        raise CandleValidationError(f"{field_name} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _candle_timestamp(candle: Mapping[str, Any]) -> datetime:
    for field_name in _TIMESTAMP_FIELDS:
        if candle.get(field_name) is not None:
            return _utc_datetime(candle[field_name], field_name)
    raise CandleValidationError("candle is missing timestamp")


def validate_candle_versioned(candle: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate schema, provenance, timestamp, OHLC bounds, and volumes."""
    if not isinstance(candle, Mapping):
        raise CandleValidationError(
            f"candle must be a dict, got {type(candle).__name__}"
        )

    schema_version = candle.get("schema_version")
    if type(schema_version) is not int:
        raise CandleValidationError("schema_version must be a numeric integer")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise CandleValidationError(
            f"schema_version version mismatch: got {schema_version}, "
            f"expected {CURRENT_SCHEMA_VERSION}"
        )

    metadata = candle.get("ingestion_metadata")
    if not isinstance(metadata, Mapping):
        raise CandleValidationError("ingestion_metadata must be a dict")
    missing = _REQUIRED_PROVENANCE - metadata.keys()
    if missing:
        raise CandleValidationError(
            f"ingestion_metadata missing required keys: {', '.join(sorted(missing))}"
        )
    for field_name in _REQUIRED_PROVENANCE - {"received_at"}:
        value = metadata[field_name]
        if not isinstance(value, str) or value.strip().lower() in _UNSPECIFIED_VALUES:
            raise CandleValidationError(
                f"ingestion_metadata.{field_name} must be a non-empty, specified string"
            )
    _utc_datetime(metadata["received_at"], "ingestion_metadata.received_at")

    source = candle.get("source")
    if not isinstance(source, str) or source.strip().lower() in _UNSPECIFIED_VALUES:
        raise CandleValidationError("source must identify the actual data origin")

    _candle_timestamp(candle)
    try:
        raw_prices = [candle[field] for field in ("open", "high", "low", "close")]
    except (KeyError, OverflowError, TypeError, ValueError) as error:
        raise CandleValidationError(
            "candle must contain numeric open, high, low, and close"
        ) from error
    if any(isinstance(price, bool) for price in raw_prices):
        raise CandleValidationError("OHLC prices must be numeric")
    prices = [float(price) for price in raw_prices]
    if not all(math.isfinite(price) for price in prices):
        raise CandleValidationError("OHLC prices must be finite")
    open_price, high_price, low_price, close_price = prices
    if (
        not low_price <= open_price <= high_price
        or not low_price <= close_price <= high_price
    ):
        raise CandleValidationError("OHLC bounds require low <= open/close <= high")
    for field_name in ("tick_volume", "volume"):
        if candle.get(field_name) is not None:
            if isinstance(candle[field_name], bool):
                raise CandleValidationError(
                    f"{field_name} must be finite and non-negative"
                )
            try:
                volume = float(candle[field_name])
            except (OverflowError, TypeError, ValueError) as error:
                raise CandleValidationError(f"{field_name} must be numeric") from error
            if not math.isfinite(volume) or volume < 0:
                raise CandleValidationError(
                    f"{field_name} must be finite and non-negative"
                )
    return candle


def validate_candle_batch_versioned(
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a strictly time-ordered batch before indicator/filter use."""
    if not isinstance(candles, list):
        raise CandleValidationError(
            f"candles must be a list, got {type(candles).__name__}"
        )

    previous_timestamp: datetime | None = None
    for index, candle in enumerate(candles):
        try:
            validate_candle_versioned(candle)
            timestamp = _candle_timestamp(candle)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise CandleValidationError(
                    "candle timestamps must be unique and strictly ascending"
                )
            previous_timestamp = timestamp
        except CandleValidationError as error:
            raise CandleValidationError(f"candle at index {index}: {error}") from error
    return candles
