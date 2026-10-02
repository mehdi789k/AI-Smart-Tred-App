"""Shared provenance helpers for versioned data-layer candle payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

CANDLE_SCHEMA_VERSION = 1
COLLECTOR_VERSION = "data-layer/dev"
MT5_OFFICIAL_SOURCE = "mt5_official"
_UNSPECIFIED = {"", "unknown", "none", "n/a"}


def _specified_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value.strip().lower() in _UNSPECIFIED:
        raise ValueError(f"{field_name} must be a non-empty, specified string")
    return value.strip()


def utc_iso_now() -> str:
    """Return the current payload-generation time as explicit UTC ISO-8601."""

    return datetime.now(timezone.utc).isoformat()


def validate_received_at(value: Any) -> str:
    """Validate that received_at names an explicit UTC instant."""

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "ingestion_metadata.received_at must be valid ISO-8601"
            ) from error
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError(
            "ingestion_metadata.received_at must be a timezone-aware UTC timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(
            "ingestion_metadata.received_at must be a timezone-aware UTC timestamp"
        )
    return parsed.astimezone(timezone.utc).isoformat()


def make_ingestion_metadata(
    *,
    ingestion_id: str | None = None,
    received_at: datetime | str | None = None,
    collector_version: str = COLLECTOR_VERSION,
) -> dict[str, str]:
    """Create truthful provenance for this ingestion/payload generation."""

    identifier = (
        str(uuid4())
        if ingestion_id is None
        else _specified_string(ingestion_id, "ingestion_metadata.ingestion_id")
    )
    timestamp = (
        utc_iso_now() if received_at is None else validate_received_at(received_at)
    )
    version = _specified_string(
        collector_version, "ingestion_metadata.collector_version"
    )
    return {
        "ingestion_id": identifier,
        "received_at": timestamp,
        "collector_version": version,
    }


def validate_candle_provenance(candle: Mapping[str, Any]) -> None:
    """Fail closed unless the candle envelope has the current schema and origin."""

    version = candle.get("schema_version")
    if type(version) is not int or version != CANDLE_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be integer {CANDLE_SCHEMA_VERSION}")
    _specified_string(candle.get("source"), "source")
    metadata = candle.get("ingestion_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("ingestion_metadata must be a mapping")
    for field_name in ("ingestion_id", "collector_version"):
        _specified_string(metadata.get(field_name), f"ingestion_metadata.{field_name}")
    validate_received_at(metadata.get("received_at"))
