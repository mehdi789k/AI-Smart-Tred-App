from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .config import DEFAULT_TIMEFRAMES, resolve_timeframes
from .historical_loader import HistoricalDataLoader
from .mt5_connector import MetaTraderConnector


def extract_market_watch_data(
    *,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    count: int = 1000,
) -> dict:
    connector = MetaTraderConnector()
    try:
        connector.connect()
        loader = HistoricalDataLoader(connector)
        payload = loader.load_market_watch_data(symbols=symbols, timeframes=timeframes, count=count)
        return {
            "summary": loader.summary(payload, loader.failures),
            "data": {
                symbol: {
                    tf: [record.as_dict() for record in records]
                    for tf, records in timeframe_map.items()
                }
                for symbol, timeframe_map in payload.items()
            },
        }
    finally:
        connector.disconnect()


def _symbol_directory_name(symbol: str) -> str:
    """Create a filesystem-safe, collision-resistant directory name for a broker symbol."""
    encoded = quote(symbol, safe="._-") or "empty"
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:10]
    return f"{encoded}-{digest}"


def _normalized_row(
    row: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    source: Path,
) -> dict[str, Any] | None:
    if row.get("symbol") != symbol or row.get("timeframe") != timeframe:
        return None

    try:
        timestamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        normalized = {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0),
            "tick_volume": row.get("tick_volume") or "",
            "spread": row.get("spread") or "",
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid OHLCV row in {source} for {symbol} ({timeframe}): {exc}"
        ) from exc
    return normalized


def _read_pair_file(path: Path, symbol: str, timeframe: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            normalized = _normalized_row(
                row, symbol=symbol, timeframe=timeframe, source=path
            )
            if normalized is not None:
                rows.append(normalized)
    return rows


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def save_extraction(payload: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Merge new bars into each symbol/timeframe CSV without duplicate timestamps."""
    root = Path(output_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    updated_at_utc = datetime.now(timezone.utc).isoformat()
    fieldnames = [
        "symbol",
        "timeframe",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "tick_volume",
        "spread",
    ]
    output_files: list[dict[str, Any]] = []
    stored_record_count = 0

    for symbol, timeframe_map in payload["data"].items():
        symbol_dir = root / _symbol_directory_name(symbol)
        symbol_dir.mkdir(parents=True, exist_ok=True)
        encoded_symbol = quote(symbol, safe="._-") or "empty"
        for timeframe, records in timeframe_map.items():
            existing_files = sorted(
                symbol_dir.glob(f"{encoded_symbol}_{timeframe}_*.csv"),
                key=lambda path: path.stat().st_mtime_ns,
            )
            existing_rows: dict[str, dict[str, Any]] = {}
            for path in existing_files:
                for row in _read_pair_file(path, symbol, timeframe):
                    existing_rows[row["timestamp"]] = row

            if not existing_files:
                legacy_files = list(root.glob(f"*/{symbol_dir.name}/{timeframe}.csv"))
                legacy_files.extend(root.glob("market_watch_*.csv"))
                for path in legacy_files:
                    for row in _read_pair_file(path, symbol, timeframe):
                        existing_rows[row["timestamp"]] = row

            incoming_rows: list[dict[str, Any]] = []
            for record in records:
                metadata = record.get("metadata") or {}
                normalized = _normalized_row(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "timestamp": record["timestamp"],
                        "open": record["open"],
                        "high": record["high"],
                        "low": record["low"],
                        "close": record["close"],
                        "volume": record["volume"],
                        "tick_volume": metadata.get("tick_volume"),
                        "spread": metadata.get("spread"),
                    },
                    symbol=symbol,
                    timeframe=timeframe,
                    source=Path("<new extraction>"),
                )
                if normalized is not None:
                    incoming_rows.append(normalized)

            if not incoming_rows:
                if existing_files:
                    stored_record_count += len(existing_rows)
                    output_files.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "record_count": len(existing_rows),
                            "new_records_count": 0,
                            "updated_records_count": 0,
                            "status": "no_new_data",
                            "updated_at_utc": updated_at_utc,
                            "csv_file": str(existing_files[-1].resolve()),
                        }
                    )
                continue

            new_records_count = 0
            updated_records_count = 0
            for row in incoming_rows:
                timestamp_key = row["timestamp"]
                previous = existing_rows.get(timestamp_key)
                if previous is None:
                    new_records_count += 1
                elif any(
                    previous[field] != row[field]
                    for field in ("open", "high", "low", "close", "volume")
                ):
                    updated_records_count += 1
                existing_rows[timestamp_key] = row

            merged_rows = [
                existing_rows[timestamp] for timestamp in sorted(existing_rows)
            ]
            if existing_files:
                csv_path = existing_files[-1]
            else:
                created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                unique_key = uuid.uuid4().hex
                csv_path = symbol_dir / (
                    f"{encoded_symbol}_{timeframe}_{created_at}_{unique_key}.csv"
                )
            _atomic_write_csv(csv_path, fieldnames, merged_rows)
            stored_record_count += len(merged_rows)
            output_files.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "record_count": len(merged_rows),
                    "new_records_count": new_records_count,
                    "updated_records_count": updated_records_count,
                    "status": "updated" if existing_files else "created",
                    "updated_at_utc": updated_at_utc,
                    "csv_file": str(csv_path.resolve()),
                }
            )

    run_id = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_"
        f"{uuid.uuid4().hex}"
    )
    summary_path = root / "runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = dict(payload["summary"])
    summary["run_id"] = run_id
    summary["output_directory"] = str(root.resolve())
    summary["stored_record_count"] = stored_record_count
    summary["output_file_count"] = len(output_files)
    summary["output_files"] = output_files
    summary["summary_file"] = str(summary_path.resolve())
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract all market-watch symbols across MT5 timeframes.")
    parser.add_argument("--symbols", help="Comma-separated MT5 symbols to export")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES), help="Comma-separated timeframes")
    parser.add_argument("--count", type=int, default=1000, help="Number of candles to download per symbol/timeframe")
    parser.add_argument(
        "--output-dir",
        help="Root directory for incremental per-symbol/timeframe CSV files (default: market_data/historical)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    symbols = None
    if args.symbols:
        symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]

    timeframes = None
    if args.timeframes:
        timeframes = [value.strip().upper() for value in args.timeframes.split(",") if value.strip()]
        resolve_timeframes(timeframes)

    if args.count <= 0:
        parser.error("--count must be a positive integer.")
    payload = extract_market_watch_data(symbols=symbols, timeframes=timeframes, count=args.count)
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = str(Path("market_data") / "historical")
    summary = save_extraction(payload, output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
