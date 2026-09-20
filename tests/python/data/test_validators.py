from datetime import datetime, timedelta, timezone

import pytest

from src.python.data.validators import (
    find_duplicate_keys,
    validate_gaps,
    validate_ohlcv_rows,
)
from src.python.observability import MetricsRegistry


def _bar(timestamp):
    return {
        "symbol": "EURUSD",
        "timeframe": "M1",
        "timestamp": timestamp,
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1.5,
        "tick_volume": 1,
    }


def test_validator_detects_duplicate_natural_keys_and_rejects_them():
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [_bar(timestamp), _bar(timestamp)]
    assert len(find_duplicate_keys(rows)) == 1
    with pytest.raises(ValueError, match="duplicate OHLCV keys"):
        validate_ohlcv_rows(rows)


def test_validator_rejects_naive_timestamps_and_invalid_bounds():
    row = _bar(datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_ohlcv_rows([row])
    row = _bar(datetime(2024, 1, 1, tzinfo=timezone.utc))
    row["low"] = 3
    with pytest.raises(ValueError, match="bounds"):
        validate_ohlcv_rows([row])


def test_validator_reports_missing_periods():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [_bar(start), _bar(start + timedelta(minutes=2))]
    assert validate_gaps(rows, "M1") == [
        (start + timedelta(minutes=1), start + timedelta(minutes=1))
    ]


def test_validator_records_data_quality_metric():
    metrics = MetricsRegistry()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [_bar(start), _bar(start + timedelta(minutes=2))]

    validate_gaps(rows, "M1", metrics=metrics)

    assert metrics.get_counter("data_gaps_total", labels={"timeframe": "M1"}) == 1
