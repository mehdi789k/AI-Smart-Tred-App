from __future__ import annotations

from datetime import datetime, timezone

from src.python.data.historical_loader import HistoricalDataLoader
from src.python.data.models import MarketDataRecord
from src.python.data.mt5_connector import MT5DataUnavailableError


class DummyConnector:
    def list_market_watch_symbols(self):
        return ["EURUSD", "GBPUSD"]

    def fetch_ohlcv(self, symbol, timeframe, start_index=0, count=1000):
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            {
                "time": timestamp,
                "open": 1.0,
                "high": 1.2,
                "low": 0.9,
                "close": 1.1,
                "tick_volume": 10,
                "spread": 3,
                "real_volume": 100,
            }
        ]


def test_load_market_watch_data_returns_symbol_and_timeframe_records():
    loader = HistoricalDataLoader(DummyConnector())

    payload = loader.load_market_watch_data(count=1)

    assert "EURUSD" in payload
    assert "M1" in payload["EURUSD"]
    assert isinstance(payload["EURUSD"]["M1"][0], MarketDataRecord)
    assert payload["EURUSD"]["M1"][0].symbol == "EURUSD"


def test_load_market_watch_data_continues_after_unavailable_symbol_timeframe():
    class PartiallyUnavailableConnector(DummyConnector):
        def fetch_ohlcv(self, symbol, timeframe, start_index=0, count=1000):
            if symbol == "EURUSD" and timeframe == "M1":
                raise MT5DataUnavailableError(
                    "MT5 returned no OHLCV data for EURUSD (M1); last_error=(-1, 'No history')."
                )
            return super().fetch_ohlcv(symbol, timeframe, start_index, count)

    loader = HistoricalDataLoader(PartiallyUnavailableConnector())

    payload = loader.load_market_watch_data(
        symbols=["EURUSD", "GBPUSD"], timeframes=["M1", "H1"], count=1
    )

    assert payload["EURUSD"]["M1"] == []
    assert len(payload["EURUSD"]["H1"]) == 1
    assert len(payload["GBPUSD"]["M1"]) == 1
    assert loader.failures == [
        {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "reason": "MT5 returned no OHLCV data for EURUSD (M1); last_error=(-1, 'No history').",
        }
    ]
    summary = loader.summary(payload, loader.failures)
    assert summary["failed_extractions"] == loader.failures
    assert summary["record_count"] == 3


def test_load_market_watch_data_reports_empty_result_and_continues():
    class EmptyDataConnector(DummyConnector):
        def fetch_ohlcv(self, symbol, timeframe, start_index=0, count=1000):
            if symbol == "EURUSD" and timeframe == "M1":
                return []
            return super().fetch_ohlcv(symbol, timeframe, start_index, count)

    loader = HistoricalDataLoader(EmptyDataConnector())

    payload = loader.load_market_watch_data(
        symbols=["EURUSD", "GBPUSD"], timeframes=["M1"], count=1
    )

    assert payload["EURUSD"]["M1"] == []
    assert len(payload["GBPUSD"]["M1"]) == 1
    assert loader.failures[0]["reason"] == (
        "MT5 returned zero historical bars for this symbol/timeframe."
    )


def test_load_market_watch_data_rejects_non_positive_count():
    loader = HistoricalDataLoader(DummyConnector())

    import pytest

    with pytest.raises(ValueError, match="count must be a positive integer"):
        loader.load_market_watch_data(symbols=["EURUSD"], timeframes=["M1"], count=0)


def test_validate_records_filters_invalid_ranges():
    valid = MarketDataRecord("EURUSD", "M1", datetime.now(timezone.utc), 1.0, 1.2, 0.9, 1.1)
    invalid = MarketDataRecord("EURUSD", "M1", datetime.now(timezone.utc), 0.0, 0.0, 1.0, 0.0)

    result = HistoricalDataLoader.validate_records([valid, invalid])

    assert result == [valid]
