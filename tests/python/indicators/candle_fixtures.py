from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def versioned_candles(
    candles: list[Mapping[str, Any]], *, ingestion_id: str = "test-ingestion"
) -> list[dict[str, Any]]:
    """Add explicit test-only source and ingestion metadata to sample candles."""
    result = []
    for index, candle in enumerate(candles):
        row = dict(candle)
        close = float(row.get("close", row.get("open", 1.0)))
        row.setdefault("open", close)
        row.setdefault("high", close)
        row.setdefault("low", close)
        row.setdefault("close", close)
        if not any(
            row.get(field) is not None
            for field in ("timestamp", "time_iso", "time", "time_msc")
        ):
            row["time"] = 1_700_000_000 + index * 60
        elif (
            isinstance(row.get("time_iso"), str)
            and not row["time_iso"].endswith("Z")
            and "+" not in row["time_iso"]
        ):
            row["time_iso"] += "Z"
        row["source"] = "test_fixture"
        row["schema_version"] = 1
        row["ingestion_metadata"] = {
            "ingestion_id": ingestion_id,
            "received_at": "2026-01-01T00:00:00Z",
            "collector_version": "test-suite",
        }
        result.append(row)
    return result
