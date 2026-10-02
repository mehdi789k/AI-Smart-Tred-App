from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.candle_contract import COLLECTOR_VERSION
from src.python.data.config import DataConfig, Timeframe
from src.python.data.data_streamer import MarketDataStreamer
from src.python.indicators.candle_validator import validate_candle_versioned


class FakeSocket:
    def __init__(self):
        self.messages = []
        self.bound = None

    def bind(self, endpoint):
        self.bound = endpoint

    def send_multipart(self, message):
        self.messages.append(message)

    def close(self, **_kwargs):
        pass


class FakeConnector:
    def iter_ticks(self, symbols):
        return iter([])

    def get_rates(self, symbol, timeframe, count=1):
        return [
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1,
            }
        ]


def test_streamer_uses_topic_and_json_contract():
    socket = FakeSocket()
    streamer = MarketDataStreamer(
        FakeConnector(),
        DataConfig(symbols=("EURUSD",), timeframes=(Timeframe.M1,)),
        socket=socket,
    )

    streamer.start()
    message = streamer.publish_bar(
        {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "close": 1.2,
            "source": "mt5_official",
            "schema_version": 1,
            "ingestion_metadata": {
                "ingestion_id": "stream-test",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "collector_version": COLLECTOR_VERSION,
            },
        }
    )

    assert socket.bound == "tcp://127.0.0.1:5556"
    assert socket.messages[0][0] == b"EURUSD_M1"
    assert json.loads(socket.messages[0][1]) == message
    assert message["schema_version"] == 1
    assert message["type"] == "ohlcv"
    assert message["source"] == "mt5_official"
    assert message["ingestion_metadata"]["collector_version"] == COLLECTOR_VERSION


def test_stream_once_publishes_latest_bars():
    socket = FakeSocket()
    streamer = MarketDataStreamer(
        FakeConnector(),
        DataConfig(symbols=("EURUSD",), timeframes=(Timeframe.M1,)),
        socket=socket,
    )

    assert streamer.stream_once() == 1
    assert socket.messages[0][0] == b"EURUSD_M1"
    body = json.loads(socket.messages[0][1])
    assert body["schema_version"] == 1
    assert body["source"] == "mt5_official"
    assert body["ingestion_metadata"]["collector_version"] == COLLECTOR_VERSION
    assert streamer.stream_once() == 0


def test_stream_once_persists_a_versioned_ohlcv_payload():
    class Repository:
        def __init__(self):
            self.rows = []

        def bulk_upsert_candles(self, rows):
            self.rows.extend(rows)
            return len(rows)

    repository = Repository()
    streamer = MarketDataStreamer(
        FakeConnector(),
        DataConfig(symbols=("EURUSD",), timeframes=(Timeframe.M1,)),
        repository=repository,
        socket=FakeSocket(),
    )

    assert streamer.stream_once() == 1
    assert len(repository.rows) == 1
    row = repository.rows[0]
    assert row["schema_version"] == 1
    assert row["source"] == "mt5_official"
    assert row["ingestion_metadata"]["collector_version"] == COLLECTOR_VERSION
    validate_candle_versioned(row)
