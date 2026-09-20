from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.config import Timeframe
from src.python.data.historical_loader import (
    HistoricalDataQualityError,
    HistoricalLoader,
    normalize_mt5_export,
    validate_overlap,
    find_gaps,
    is_expected_market_gap,
)
import pytest


class FakeConnector:
    def get_rates(self, symbol, timeframe, **_kwargs):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            {"time": base.timestamp(), "open": 1, "high": 2, "low": 0, "close": 1},
            {"time": base.timestamp(), "open": 1, "high": 2, "low": 0, "close": 1},
            {
                "time": (base + timedelta(minutes=1)).timestamp(),
                "open": 2,
                "high": 3,
                "low": 1,
                "close": 2,
            },
        ]


class FakeRepository:
    def __init__(self):
        self.rows = []

    async def bulk_upsert_candles(self, rows, **_kwargs):
        self.rows.extend(rows)
        return len(rows)


def test_historical_loader_deduplicates_and_reports_progress():
    repo = FakeRepository()
    progress = []
    loader = HistoricalLoader(
        FakeConnector(), repo, batch_size=1, progress_callback=lambda *args: progress.append(args)
    )

    result = asyncio.run(loader.load_symbol("EURUSD", Timeframe.M1, count=1_000))

    assert result.fetched == 3
    assert result.stored == 2
    assert result.duplicates_removed == 1
    assert len(repo.rows) == 2
    assert len(progress) == 2
    assert repo.rows[0]["source"] == "mt5_official"
    assert repo.rows[0]["ingestion_metadata"]["collector"] == "historical_loader"
    assert repo.rows[0]["ingestion_metadata"]["ingestion_id"]


def test_find_gaps_detects_missing_bars_but_not_one_bar_jitter():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base},
        {"timestamp": base + timedelta(minutes=1)},
        {"timestamp": base + timedelta(minutes=4)},
    ]
    assert find_gaps(rows, Timeframe.M1, tolerance=1) == [
        (base + timedelta(minutes=2), base + timedelta(minutes=3))
    ]


def test_expected_market_gaps_are_reported_but_not_quality_failures():
    saturday = datetime(2024, 1, 6, 0, 5, tzinfo=timezone.utc)
    monday = datetime(2024, 1, 8, 0, 5, tzinfo=timezone.utc)
    maintenance_start = datetime(2024, 1, 9, 0, 5, tzinfo=timezone.utc)
    maintenance_end = datetime(2024, 1, 9, 0, 55, tzinfo=timezone.utc)
    holiday_reopen_start = datetime(2024, 5, 27, 21, 20, tzinfo=timezone.utc)
    holiday_reopen_end = datetime(2024, 5, 28, 0, 55, tzinfo=timezone.utc)

    assert is_expected_market_gap(saturday, monday)
    assert is_expected_market_gap(maintenance_start, maintenance_end)
    assert is_expected_market_gap(holiday_reopen_start, holiday_reopen_end)
    assert is_expected_market_gap(
        datetime(2024, 1, 9, 10, 5, tzinfo=timezone.utc),
        datetime(2024, 1, 9, 10, 15, tzinfo=timezone.utc),
    )
    assert not is_expected_market_gap(
        datetime(2024, 1, 9, 10, 5, tzinfo=timezone.utc),
        datetime(2024, 1, 9, 10, 55, tzinfo=timezone.utc),
    )
def test_historical_loader_requires_at_least_1000_bars():
    loader = HistoricalLoader(FakeConnector(), FakeRepository())
    with pytest.raises(ValueError, match="at least 1000"):
        asyncio.run(loader.load_symbol("EURUSD", Timeframe.M1, count=999))


def test_stage_a_requires_xauusd_m5_and_30000_bars():
    loader = HistoricalLoader(FakeConnector(), FakeRepository())
    with pytest.raises(ValueError, match="at least 30000"):
        asyncio.run(loader.load_stage_a(count=29_999))
    with pytest.raises(ValueError, match="XAUUSD"):
        asyncio.run(loader.load_stage_a(symbol="EURUSD", count=30_000))


def test_stage_a_happy_path_loads_30000_unique_bars():
    class StageAConnector:
        def get_rates(self, *_args, **_kwargs):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            return [
                {
                    "time": (base + timedelta(minutes=5 * index)).timestamp(),
                    "open": 1,
                    "high": 2,
                    "low": 0,
                    "close": 1,
                    "tick_volume": 1,
                }
                for index in range(30_000)
            ]

    repo = FakeRepository()
    result = asyncio.run(
        HistoricalLoader(StageAConnector(), repo, batch_size=5_000).load_stage_a()
    )
    assert result.fetched == result.stored == 30_000
    assert result.gaps == []
    assert result.quality_valid is True
    assert len(repo.rows) == 30_000


def test_invalid_ohlcv_fails_before_persistence():
    class InvalidConnector(FakeConnector):
        def get_rates(self, *_args, **_kwargs):
            return [{
                "time": datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp(),
                "open": 10, "high": 9, "low": 8, "close": 10,
            }]

    repo = FakeRepository()
    loader = HistoricalLoader(InvalidConnector(), repo)
    with pytest.raises(HistoricalDataQualityError, match="OHLC bounds"):
        asyncio.run(loader.load_symbol("XAUUSD", Timeframe.M5, count=1_000))
    assert repo.rows == []


def test_official_mt5_export_is_normalized_and_deduplicated():
    rows = [
        {"Date": "2024.01.01", "Time": "00:00", "Open": "1", "High": "2",
         "Low": "0.5", "Close": "1.5", "Tick Volume": "10", "Volume": "0"},
        {"Date": "2024.01.01", "Time": "00:01", "Open": "1.5", "High": "2",
         "Low": "1", "Close": "1.7", "Tick Volume": "11", "Volume": "0"},
        {"Date": "2024.01.01", "Time": "00:01", "Open": "1.5", "High": "2",
         "Low": "1", "Close": "1.7", "Tick Volume": "11", "Volume": "0"},
    ]
    normalized = normalize_mt5_export(rows, symbol="XAUUSD", timeframe="M1")
    assert len(normalized) == 2
    assert normalized[0]["source"] == "mt5_official"
    assert normalized[0]["timestamp"].tzinfo == timezone.utc
    metadata = normalized[0]["ingestion_metadata"]
    assert metadata["ingestion_id"]
    assert metadata["received_at"].endswith("+00:00")
    assert metadata["collector"] == "historical_loader"
    assert metadata["format"] == "mt5_official_export"
    assert metadata["provider"] == "MetaTrader5"


def test_overlap_validation_rejects_conflicting_provider_history():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    incoming = [{"symbol": "XAUUSD", "timeframe": "M1", "timestamp": base,
                 "open": 1, "high": 2, "low": 0, "close": 1}]
    existing = [{"symbol": "XAUUSD", "timeframe": "M1", "timestamp": base,
                 "open": 1.1, "high": 2, "low": 0, "close": 1}]
    with pytest.raises(HistoricalDataQualityError, match="overlap mismatch"):
        validate_overlap(incoming, existing)


def test_load_export_rows_persists_provenance_metadata():
    rows = [{
        "Date": "2024.01.01", "Time": "00:00", "Open": "1",
        "High": "2", "Low": "0.5", "Close": "1.5", "Tick Volume": "10",
    }]
    repo = FakeRepository()
    result = asyncio.run(HistoricalLoader(None, repo).load_export_rows(
        rows, symbol="XAUUSD", timeframe="M1"
    ))
    assert result.stored == 1
    metadata = repo.rows[0]["ingestion_metadata"]
    assert {"ingestion_id", "received_at", "collector"} <= metadata.keys()
    assert metadata["provider"] == "MetaTrader5"


def test_backfill_pages_backwards_and_reports_coverage():
    class PagedConnector:
        def get_rates_from_pos(self, _symbol, _timeframe, position, count):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            newest = 12
            return [
                {
                    "time": (base + timedelta(minutes=5 * (newest - index))).timestamp(),
                    "open": 1, "high": 2, "low": 0, "close": 1, "tick_volume": 1,
                }
                for index in range(position, min(position + count, newest))
            ]

    repo = FakeRepository()
    result = asyncio.run(
        HistoricalLoader(PagedConnector(), repo, batch_size=3).backfill_symbol(
            "XAUUSD", Timeframe.M5, target_bars=8, chunk_size=3
        )
    )
    assert result.pages == 3
    assert result.fetched == result.stored == 8
    assert result.requested_bars == result.available_bars == 8
    assert result.exhausted is False
    assert result.coverage_start < result.coverage_end


def test_backfill_persists_each_chunk_and_reports_broker_shortfall():
    class TrackingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def bulk_upsert_candles(self, rows, **kwargs):
            self.calls.append(list(rows))
            return await super().bulk_upsert_candles(rows, **kwargs)

    class ShortConnector:
        def get_rates_from_pos(self, _symbol, _timeframe, position, count):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            available = 3
            return [
                {
                    "time": (base + timedelta(minutes=index)).timestamp(),
                    "open": 1, "high": 2, "low": 0, "close": 1,
                }
                for index in range(position, min(position + count, available))
            ]

    repository = TrackingRepository()
    result = asyncio.run(
        HistoricalLoader(ShortConnector(), repository, batch_size=2).backfill_symbol(
            "EURUSD", Timeframe.M1, target_bars=10, chunk_size=2
        )
    )

    assert [len(call) for call in repository.calls] == [2, 1]
    assert result.requested_bars == 10
    assert result.available_bars == result.stored == 3
    assert result.exhausted is True
    assert result.coverage == (result.coverage_start, result.coverage_end)


def test_backfill_is_idempotent_against_sqlite_repository():
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")
    from src.python.data.database import AsyncDatabase

    class Connector:
        def get_rates_from_pos(self, _symbol, _timeframe, position, count):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            return [
                {
                    "time": (base + timedelta(minutes=index)).timestamp(),
                    "open": 1, "high": 2, "low": 0, "close": 1,
                }
                for index in range(position, min(position + count, 4))
            ]

    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        loader = HistoricalLoader(
            Connector(), database.repository, batch_size=2
        )
        first = await loader.backfill_symbol(
            "EURUSD", Timeframe.M1, target_bars=4, chunk_size=2
        )
        second = await loader.backfill_symbol(
            "EURUSD", Timeframe.M1, target_bars=4, chunk_size=2
        )
        candles = await database.repository.candles("EURUSD", "M1")
        await database.dispose()
        return first, second, candles

    first, second, candles = asyncio.run(scenario())

    assert first.stored == second.stored == 4
    assert len(candles) == 4


def test_backfill_stops_between_pages_without_fake_bars():
    class StopEvent:
        def __init__(self):
            self.calls = 0
        def is_set(self):
            self.calls += 1
            return self.calls > 1

    class Connector:
        def get_rates_from_pos(self, *_args, **_kwargs):
            return [{"time": 1_700_000_000, "open": 1, "high": 2, "low": 0, "close": 1}]

    result = asyncio.run(
        HistoricalLoader(Connector(), FakeRepository(), batch_size=1).backfill_symbol(
            "XAUUSD", Timeframe.M1, target_bars=10, chunk_size=1,
            stop_event=StopEvent(),
        )
    )
    assert result.stopped is True


def test_non_chronological_history_fails_explicitly():
    class UnorderedConnector(FakeConnector):
        def get_rates(self, *_args, **_kwargs):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            row = lambda timestamp: {
                "time": timestamp.timestamp(), "open": 1, "high": 2,
                "low": 0, "close": 1,
            }
            return [row(base + timedelta(minutes=1)), row(base)]

    with pytest.raises(HistoricalDataQualityError, match="not chronological"):
        asyncio.run(
            HistoricalLoader(UnorderedConnector(), FakeRepository()).load_symbol(
                "XAUUSD", Timeframe.M5, count=1_000
            )
        )


def test_stage_a_rejects_duplicate_shortfall_before_persistence():
    class DuplicateConnector:
        def get_rates(self, *_args, **_kwargs):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            rows = [
                {
                    "time": (base + timedelta(minutes=5 * index)).timestamp(),
                    "open": 1,
                    "high": 2,
                    "low": 0,
                    "close": 1,
                    "tick_volume": 1,
                }
                for index in range(29_999)
            ]
            rows.append(dict(rows[-1]))
            return rows

    repo = FakeRepository()
    with pytest.raises(HistoricalDataQualityError, match="unique bars"):
        asyncio.run(
            HistoricalLoader(DuplicateConnector(), repo).load_stage_a(count=30_000)
        )
    assert repo.rows == []


def test_stage_a_rejects_gaps_before_persistence():
    class GappedConnector:
        def get_rates(self, *_args, **_kwargs):
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            rows = []
            for index in range(30_000):
                # Skip four five-minute intervals to create a blocking gap.
                offset = index if index < 15_000 else index + 4
                rows.append(
                    {
                        "time": (base + timedelta(minutes=5 * offset)).timestamp(),
                        "open": 1,
                        "high": 2,
                        "low": 0,
                        "close": 1,
                        "tick_volume": 1,
                    }
                )
            return rows

    repo = FakeRepository()
    with pytest.raises(HistoricalDataQualityError, match="gap"):
        asyncio.run(
            HistoricalLoader(GappedConnector(), repo).load_stage_a(count=30_000)
        )
    assert repo.rows == []
