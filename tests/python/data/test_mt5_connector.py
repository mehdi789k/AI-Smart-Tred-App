from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.config import DataConfig, Timeframe
from src.python.data.mt5_connector import MT5Connector, MT5LoginError


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, initialize_results=(True,)):
        self.initialize_results = iter(initialize_results)
        self.shutdown_calls = 0

    def initialize(self, **_kwargs):
        return next(self.initialize_results, True)

    def shutdown(self):
        self.shutdown_calls += 1

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def account_info(self):
        return SimpleNamespace(login=123)

    def last_error(self):
        return (1, "bad login")

    def copy_rates_from_pos(self, symbol, timeframe, _position, _count):
        return [
            {
                "time": 1_700_000_000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "tick_volume": 10,
            }
        ]

    def copy_ticks_from(self, symbol, _start, _count, _flags):
        return [{"time": 1_700_000_000, "bid": 1.1, "ask": 1.2}]

    def symbol_info_tick(self, symbol):
        return {"time": 1_700_000_000, "bid": 1.1, "ask": 1.2}

    def symbols_get(self):
        return [
            SimpleNamespace(name="EURUSD", visible=True),
            SimpleNamespace(name="HIDDEN", visible=False),
            {"name": "XAUUSD", "visible": True},
        ]


def config() -> DataConfig:
    return DataConfig(mt5_login=123, mt5_password="secret", mt5_server="demo")


def test_connector_normalizes_rates_and_ticks_to_utc():
    connector = MT5Connector(config(), mt5_module=FakeMT5())

    rates = connector.get_rates("EURUSD", Timeframe.M1, count=1)
    tick = connector.get_ticks("EURUSD", count=1)[0]

    assert rates[0]["symbol"] == "EURUSD"
    assert rates[0]["timeframe"] == "M1"
    assert rates[0]["timestamp"].tzinfo == timezone.utc
    assert tick["timestamp"].tzinfo == timezone.utc


def test_login_failure_is_explicit():
    connector = MT5Connector(config(), mt5_module=FakeMT5((False,)))

    with pytest.raises(MT5LoginError, match="login failed"):
        connector.connect()


def test_reconnect_retries_and_uses_backoff():
    waits: list[float] = []
    connector = MT5Connector(
        config(), mt5_module=FakeMT5((False, True)), sleep=waits.append
    )

    assert connector.reconnect(attempts=2)
    assert waits == [config().reconnect_backoff_seconds]


def test_visible_symbols_reads_market_watch_and_filters_hidden():
    connector = MT5Connector(config(), mt5_module=FakeMT5())
    assert connector.visible_symbols() == ("EURUSD", "XAUUSD")


def test_paged_rates_normalize_and_validate_position():
    connector = MT5Connector(config(), mt5_module=FakeMT5())
    rows = connector.get_rates_from_pos("EURUSD", Timeframe.M1, 4, 2)
    assert len(rows) == 1
    assert rows[0]["timeframe"] == "M1"
    assert rows[0]["timestamp"].tzinfo == timezone.utc


def test_rate_read_reconnects_after_transient_terminal_failure():
    class FlakyMT5(FakeMT5):
        def __init__(self):
            super().__init__()
            self.rate_calls = 0

        def copy_rates_from_pos(self, symbol, timeframe, position, count):
            self.rate_calls += 1
            if self.rate_calls == 1:
                raise RuntimeError("terminal is restarting")
            return super().copy_rates_from_pos(symbol, timeframe, position, count)

    waits: list[float] = []
    connector = MT5Connector(config(), mt5_module=FlakyMT5(), sleep=waits.append)

    rows = connector.get_rates("EURUSD", Timeframe.M1, count=1)

    assert len(rows) == 1
    assert waits == [config().reconnect_backoff_seconds]
