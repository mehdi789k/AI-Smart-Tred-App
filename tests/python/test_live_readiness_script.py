from datetime import datetime, timezone

from scripts.verify_live_readiness import validate_live_runtime


class FakeConnector:
    def is_connected(self):
        return True

    def connect(self, **_kwargs):
        return True

    def get_account_summary(self):
        return {
            "connected": True,
            "login": 123,
            "server": "LiveBroker",
            "equity": 1000,
            "currency": "USD",
        }

    def get_symbols_list(self, visible_only=True):
        return [{"name": "XAUUSD", "visible": True}]

    def get_symbol_info(self, symbol):
        return {
            "bid": 100.0,
            "ask": 100.2,
            "spread": 0.2,
            "last_update": datetime.now(timezone.utc),
        }

    def get_history(self, days=1):
        return []


def test_live_readiness_script_never_calls_order_endpoint(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "true")
    monkeypatch.setenv("MT5_DEMO_ENABLED", "false")
    monkeypatch.setenv("MT5_LOGIN", "123")
    monkeypatch.setenv("MT5_SERVER", "LiveBroker")
    monkeypatch.setenv("MT5_LIVE_SYMBOLS", "XAUUSD")
    monkeypatch.setattr(
        "scripts.verify_live_readiness.fetch_json",
        lambda *_args, **_kwargs: {"data": {"status": "ok"}},
    )

    result = validate_live_runtime(
        base_url="http://127.0.0.1:8000",
        allow_direct_dashboard=True,
        connector=FakeConnector(),
    )

    assert result["ready"] is True
    assert result["circuit_breaker"] == "armed"
