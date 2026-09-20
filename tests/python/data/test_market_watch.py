"""Tests for dashboard-driven Market Watch subscriptions."""

from src.python.mt5_account.mt5_market_watch import (
    dashboard_direct_mode_enabled,
    resolve_market_data_subscriptions,
    sync_market_data_from_connector,
)


def test_dashboard_direct_mode_disables_market_watch_owner(monkeypatch):
    monkeypatch.setenv("MT5_DASHBOARD_DIRECT", "true")

    assert dashboard_direct_mode_enabled() is True


def test_sync_market_data_from_connector_persists_selected_symbol_candles(tmp_path):
    class FakeConnector:
        def get_historical_candles(self, symbol, timeframe, count):
            return [
                {
                    "time": 1_700_000_000,
                    "open": 1.0,
                    "high": 1.2,
                    "low": 0.9,
                    "close": 1.1,
                    "tick_volume": 10,
                }
            ]

    sync_market_data_from_connector(
        FakeConnector(),
        [("XAUUSD_l", "M5", 1)],
        data_dir=tmp_path,
    )

    stored = (tmp_path / "XAUUSD_l_M5.json").read_text(encoding="utf-8")
    assert '"symbol": "XAUUSD_l"' in stored
    assert '"timeframe": "M5"' in stored
    assert '"close": 1.1' in stored


def test_sync_market_data_from_connector_writes_time_iso_for_training_loader(tmp_path):
    class FakeConnector:
        def get_historical_candles(self, symbol, timeframe, count):
            return [{"time": 1_700_000_000, "open": 1, "high": 2, "low": 0, "close": 1.5}]

    sync_market_data_from_connector(
        FakeConnector(), [("ZECUSD_l", "M5", 1)], data_dir=tmp_path
    )

    payload = __import__("json").loads(
        (tmp_path / "ZECUSD_l_M5.json").read_text(encoding="utf-8")
    )
    assert payload["candles"][0]["time_iso"].endswith("+00:00")


def test_resolve_subscriptions_uses_saved_selected_symbols_and_all_timeframes():
    subscriptions = resolve_market_data_subscriptions(
        visible_symbols=["EURUSD", "XAUUSD"],
        saved_settings={
            "symbol_timeframes": {"XAUUSD": "H1", "EURUSD": "M5"},
        },
        configured_timeframes=["M5", "H1", "D1"],
    )

    assert subscriptions == [
        ("EURUSD", "M5", 10_000),
        ("EURUSD", "H1", 10_000),
        ("EURUSD", "D1", 10_000),
        ("XAUUSD", "M5", 10_000),
        ("XAUUSD", "H1", 10_000),
        ("XAUUSD", "D1", 10_000),
    ]


def test_resolve_subscriptions_falls_back_to_visible_symbols_without_saved_selection():
    subscriptions = resolve_market_data_subscriptions(
        visible_symbols=["XAUUSD"],
        saved_settings={},
        configured_timeframes=["M5"],
    )

    assert subscriptions == [("XAUUSD", "M5", 10_000)]
