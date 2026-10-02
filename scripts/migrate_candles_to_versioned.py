"""Migrate legacy candle JSON with explicitly supplied origin provenance."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.python.indicators.candle_validator import CURRENT_SCHEMA_VERSION


def migrate_candle_to_versioned(
    candle: dict[str, Any],
    *,
    source: str,
    collector_version: str,
    ingestion_id: str | None = None,
    received_at: str | None = None,
) -> dict[str, Any]:
    """Annotate a legacy candle with caller-provided origin and migration provenance."""
    if not isinstance(candle, dict):
        raise TypeError("candle must be a dict")
    if not source.strip() or source.strip().lower() in {"unknown", "none", "n/a"}:
        raise ValueError("source must identify the actual data origin")
    if not collector_version.strip() or collector_version.strip().lower() in {
        "unknown",
        "none",
        "n/a",
    }:
        raise ValueError("collector_version must be explicitly identified")

    migrated = dict(candle)
    migrated["source"] = migrated.get("source") or source.strip()
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    metadata = dict(migrated.get("ingestion_metadata") or {})
    metadata["ingestion_id"] = (
        metadata.get("ingestion_id") or ingestion_id or str(uuid4())
    )
    metadata["received_at"] = (
        metadata.get("received_at")
        or received_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    metadata["collector_version"] = (
        metadata.get("collector_version") or collector_version.strip()
    )
    migrated["ingestion_metadata"] = metadata
    return migrated


def migrate_candle_batch_to_versioned(
    candles: list[dict[str, Any]],
    *,
    source: str,
    collector_version: str,
    ingestion_id: str | None = None,
    received_at: str | None = None,
) -> list[dict[str, Any]]:
    """Upgrade a batch while recording one migration event across its candles."""
    migration_id = ingestion_id or str(uuid4())
    migration_time = received_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    return [
        migrate_candle_to_versioned(
            candle,
            source=source,
            collector_version=collector_version,
            ingestion_id=migration_id,
            received_at=migration_time,
        )
        for candle in candles
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Legacy candle JSON file")
    parser.add_argument("output", type=Path, help="Versioned candle JSON file")
    parser.add_argument(
        "--source",
        required=True,
        help="Verified original data origin, for example mt5_official or replay",
    )
    parser.add_argument(
        "--collector-version",
        required=True,
        help="Known collector version for the input data",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candles = payload if isinstance(payload, list) else payload.get("candles", payload)
    if not isinstance(candles, list):
        raise ValueError(
            "input JSON must be a list of candles or contain a 'candles' list"
        )

    migrated = migrate_candle_batch_to_versioned(
        candles,
        source=args.source,
        collector_version=args.collector_version,
    )
    args.output.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
