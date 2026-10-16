from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.python.data.mt5_connector import MT5ConnectionError, MetaTraderConnector
from src.python.data.mt5_connector import MT5DataUnavailableError


class DummyMT5:
    initialized = False
    last_error_value = "mock_error"
    symbol_info_value = type("Info", (), {"visible": True})()
    symbol_info_tick_value = type("Tick", (), {"bid": 1.0, "ask": 1.1})()
    rates_value = [
        type(
            "Rate",
            (),
            {
                "time": datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp(),
                "open": 1.0,
                "high": 1.2,
                "low": 0.9,
                "close": 1.1,
                "tick_volume": 10,
                "spread": 3,
                "real_volume": 100,
            },
        )()
    ]

    @staticmethod
    def initialize(**kwargs):
        DummyMT5.initialized = True
        return True

    @staticmethod
    def shutdown():
        DummyMT5.initialized = False

    @staticmethod
    def symbols_get():
        return [type("Symbol", (), {"name": "EURUSD"})()]

    @staticmethod
    def symbol_info(name):
        return DummyMT5.symbol_info_value

    @staticmethod
    def symbol_select(name, enable):
        return True

    @staticmethod
    def symbol_info_tick(name):
        return DummyMT5.symbol_info_tick_value

    @staticmethod
    def copy_rates_from_pos(symbol, timeframe, start_index, count):
        return DummyMT5.rates_value

    @staticmethod
    def last_error():
        return DummyMT5.last_error_value


def test_meta_trader_connector_connects_and_lists_symbols(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    monkeypatch.setattr(connector_module, "mt5", DummyMT5)
    connector = MetaTraderConnector()

    connector.connect()
    assert connector.connected is True
    assert connector.list_market_watch_symbols() == ["EURUSD"]


def test_meta_trader_connector_requires_connect_before_fetch():
    connector = MetaTraderConnector()

    with pytest.raises(MT5ConnectionError):
        connector.fetch_ohlcv("EURUSD", "M1")


def test_meta_trader_connector_includes_mt5_error_for_unavailable_data(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    class MissingRatesMT5(DummyMT5):
        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_index, count):
            return None

        @staticmethod
        def copy_rates_from(symbol, timeframe, date_from, count):
            return None

        @staticmethod
        def last_error():
            return (-1, "No history data")

    monkeypatch.setattr(connector_module, "mt5", MissingRatesMT5)
    connector = MetaTraderConnector()
    connector._connected = True

    with pytest.raises(MT5DataUnavailableError, match="No history data"):
        connector.fetch_ohlcv("EURUSD", "MN1")


def test_fetch_ohlcv_selects_symbol_and_falls_back_to_date_request(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    calls = []

    class HistoryFallbackMT5(DummyMT5):
        @staticmethod
        def symbol_select(symbol, enable):
            calls.append(("select", symbol, enable))
            return True

        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_index, count):
            calls.append(("from_pos", symbol))
            return []

        @staticmethod
        def copy_rates_from(symbol, timeframe, date_from, count):
            calls.append(("from", symbol))
            return DummyMT5.rates_value

    monkeypatch.setattr(connector_module, "mt5", HistoryFallbackMT5)
    connector = MetaTraderConnector()
    connector._connected = True

    rows = connector.fetch_ohlcv("EURUSD", "M1", count=1)

    assert [call[0] for call in calls] == ["select", "from_pos", "from"]
    assert len(rows) == 1
    assert rows[0]["close"] == 1.1


def test_fetch_ohlcv_reports_both_empty_history_attempts(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    class MissingHistoryMT5(DummyMT5):
        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_index, count):
            return None

        @staticmethod
        def copy_rates_from(symbol, timeframe, date_from, count):
            return []

        @staticmethod
        def last_error():
            return (-1, "No history data")

    monkeypatch.setattr(connector_module, "mt5", MissingHistoryMT5)
    connector = MetaTraderConnector()
    connector._connected = True

    with pytest.raises(MT5DataUnavailableError, match="copy_rates_from_pos") as exc_info:
        connector.fetch_ohlcv("EURUSD", "M1")

    assert "copy_rates_from returned 0 bars" in str(exc_info.value)


def test_fetch_ohlcv_does_not_use_latest_date_fallback_for_nonzero_start_index(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    calls = []

    class MissingOffsetMT5(DummyMT5):
        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_index, count):
            calls.append(("from_pos", start_index))
            return []

        @staticmethod
        def copy_rates_from(symbol, timeframe, date_from, count):
            calls.append(("from",))
            return DummyMT5.rates_value

    monkeypatch.setattr(connector_module, "mt5", MissingOffsetMT5)
    connector = MetaTraderConnector()
    connector._connected = True

    with pytest.raises(MT5DataUnavailableError, match="not attempted"):
        connector.fetch_ohlcv("EURUSD", "M1", start_index=5, count=1)

    assert calls == [("from_pos", 5)]


def test_fetch_ohlcv_reads_numpy_style_structured_records(monkeypatch):
    import src.python.data.mt5_connector as connector_module

    class StructuredRate:
        fields = {
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp(),
            "open": 1.0,
            "high": 1.2,
            "low": 0.9,
            "close": 1.1,
            "tick_volume": 10,
            "spread": 3,
            "real_volume": 100,
        }

        def __getitem__(self, name):
            return self.fields[name]

    class StructuredRatesMT5(DummyMT5):
        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_index, count):
            return [StructuredRate()]

    monkeypatch.setattr(connector_module, "mt5", StructuredRatesMT5)
    connector = MetaTraderConnector()
    connector._connected = True

    rows = connector.fetch_ohlcv("EURUSD", "M1", count=1)

    assert len(rows) == 1
    assert rows[0]["time"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert rows[0]["close"] == 1.1
