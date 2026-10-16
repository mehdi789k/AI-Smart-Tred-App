from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.python.data.main import _symbol_directory_name, save_extraction


def test_save_extraction_writes_separate_symbol_timeframe_csvs_and_summary(tmp_path):
    output_dir = tmp_path / "history"
    payload = {
        "summary": {
            "symbol_count": 1,
            "timeframe_count": 1,
            "record_count": 2,
            "successful_extractions": 1,
            "failure_count": 0,
            "symbols": ["EURUSD"],
            "records_by_symbol_timeframe": {"EURUSD_M1": 1},
            "failed_extractions": [],
        },
        "data": {
            "EURUSD": {
                "M1": [
                    {
                        "timestamp": "2024-01-01T00:00:00+00:00",
                        "open": 1.0,
                        "high": 1.2,
                        "low": 0.9,
                        "close": 1.1,
                        "volume": 100,
                        "metadata": {"tick_volume": 10, "spread": 3},
                    }
                ],
                "H1": [
                    {
                        "timestamp": "2024-01-01T01:00:00+00:00",
                        "open": 1.1,
                        "high": 1.3,
                        "low": 1.0,
                        "close": 1.2,
                        "volume": 200,
                        "metadata": {"tick_volume": 20, "spread": 4},
                    }
                ]
            }
        },
    }

    summary = save_extraction(payload, output_dir)
    files_by_timeframe = {
        item["timeframe"]: item["csv_file"] for item in summary["output_files"]
    }
    m1_path = Path(files_by_timeframe["M1"])
    h1_path = Path(files_by_timeframe["H1"])

    with m1_path.open(encoding="utf-8", newline="") as handle:
        m1_rows = list(csv.DictReader(handle))
    with h1_path.open(encoding="utf-8", newline="") as handle:
        h1_rows = list(csv.DictReader(handle))
    summary_path = Path(summary["summary_file"])

    assert len(m1_rows) == 1
    assert m1_rows[0]["symbol"] == "EURUSD"
    assert m1_rows[0]["timeframe"] == "M1"
    assert m1_rows[0]["tick_volume"] == "10"
    assert re.fullmatch(r"EURUSD_M1_\d{8}T\d{12}Z_[0-9a-f]{32}\.csv", m1_path.name)
    assert len(h1_rows) == 1
    assert h1_rows[0]["timeframe"] == "H1"
    assert summary["output_file_count"] == 2
    assert summary["output_directory"] == str(output_dir.resolve())
    assert json.loads(summary_path.read_text(encoding="utf-8"))["record_count"] == 2


def test_save_extraction_merges_new_and_updated_bars_without_duplicates(tmp_path):
    output_dir = tmp_path / "history"
    initial_payload = {
        "summary": {"record_count": 2, "failure_count": 0},
        "data": {
            "EURUSD": {
                "M1": [
                    {
                        "timestamp": "2024-01-01T00:00:00+00:00",
                        "open": 1.0,
                        "high": 1.2,
                        "low": 0.9,
                        "close": 1.1,
                        "volume": 100,
                        "metadata": {},
                    },
                    {
                        "timestamp": "2024-01-01T00:01:00+00:00",
                        "open": 1.1,
                        "high": 1.3,
                        "low": 1.0,
                        "close": 1.2,
                        "volume": 110,
                        "metadata": {},
                    },
                ]
            }
        },
    }
    first_summary = save_extraction(initial_payload, output_dir)
    first_file = Path(first_summary["output_files"][0]["csv_file"])

    updated_payload = {
        "summary": {"record_count": 2, "failure_count": 0},
        "data": {
            "EURUSD": {
                "M1": [
                    {
                        "timestamp": "2024-01-01T00:01:00+00:00",
                        "open": 1.1,
                        "high": 1.35,
                        "low": 1.0,
                        "close": 1.25,
                        "volume": 115,
                        "metadata": {},
                    },
                    {
                        "timestamp": "2024-01-01T00:02:00+00:00",
                        "open": 1.25,
                        "high": 1.4,
                        "low": 1.2,
                        "close": 1.3,
                        "volume": 120,
                        "metadata": {},
                    },
                ]
            }
        },
    }

    second_summary = save_extraction(updated_payload, output_dir)
    output_entry = second_summary["output_files"][0]
    updated_file = Path(output_entry["csv_file"])

    with updated_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert updated_file == first_file
    assert len(rows) == 3
    assert [row["timestamp"] for row in rows] == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-01T00:01:00+00:00",
        "2024-01-01T00:02:00+00:00",
    ]
    assert rows[1]["close"] == "1.25"
    assert output_entry["new_records_count"] == 1
    assert output_entry["updated_records_count"] == 1
    assert output_entry["record_count"] == 3
    assert output_entry["updated_at_utc"]


def test_save_extraction_imports_legacy_pair_file(tmp_path):
    output_dir = tmp_path / "history"
    symbol_dir = output_dir / "legacy-run" / _symbol_directory_name("EURUSD")
    symbol_dir.mkdir(parents=True)
    legacy_file = symbol_dir / "M1.csv"
    with legacy_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "EURUSD",
                "timeframe": "M1",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "open": 1.0,
                "high": 1.2,
                "low": 0.9,
                "close": 1.1,
                "volume": 100,
                "tick_volume": 10,
                "spread": 3,
            }
        )

    payload = {
        "summary": {"record_count": 1, "failure_count": 0},
        "data": {
            "EURUSD": {
                "M1": [
                    {
                        "timestamp": "2024-01-01T00:01:00+00:00",
                        "open": 1.1,
                        "high": 1.3,
                        "low": 1.0,
                        "close": 1.2,
                        "volume": 110,
                        "metadata": {},
                    }
                ]
            }
        },
    }

    summary = save_extraction(payload, output_dir)
    output_file = Path(summary["output_files"][0]["csv_file"])
    with output_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert summary["output_files"][0]["new_records_count"] == 1
