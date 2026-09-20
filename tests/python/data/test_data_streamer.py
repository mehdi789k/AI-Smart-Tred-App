from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.config import DataConfig, Timeframe
from src.python.data.data_streamer import MarketDataStreamer


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
        return [{
            "symbol": symbol,
            "timeframe": timeframe.value,
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0,
            "close": 1,
        }]


def test_streamer_uses_topic_and_json_contract():
    socket = FakeSocket()
    streamer = MarketDataStreamer(
        FakeConnector(), DataConfig(symbols=("EURUSD",), timeframes=(Timeframe.M1,)),
        socket=socket,
    )

    streamer.start()
    message = streamer.publish_bar(
        {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "close": 1.2,
        }
    )

    assert socket.bound == "tcp://127.0.0.1:5556"
    assert socket.messages[0][0] == b"EURUSD_M1"
    assert json.loads(socket.messages[0][1]) == message
    assert message["schema_version"] == 1
    assert message["type"] == "ohlcv"


def test_stream_once_publishes_latest_bars():
    socket = FakeSocket()
    streamer = MarketDataStreamer(
        FakeConnector(), DataConfig(symbols=("EURUSD",), timeframes=(Timeframe.M1,)),
        socket=socket,
    )

    assert streamer.stream_once() == 1
    assert socket.messages[0][0] == b"EURUSD_M1"
    assert streamer.stream_once() == 0
