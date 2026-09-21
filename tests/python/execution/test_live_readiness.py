from datetime import datetime, timedelta, timezone

from src.python.execution.live_readiness import validate_live_readiness


class FakeConnector:
    def __init__(self, *, account, symbols, visible=None, connected=True):
        self.account = account
        self.symbols = symbols
        self.visible = visible if visible is not None else set(symbols)
        self.connected = connected

    def is_connected(self):
        return self.connected

    def get_account_summary(self):
        return {"connected": self.connected, **self.account}

    def get_symbols_list(self, visible_only=True):
        return [{"symbol": symbol} for symbol in self.visible]

    def get_symbol_info(self, symbol):
        return self.symbols.get(symbol, {})


def valid_connector(now):
    return FakeConnector(
        account={"login": 123, "server": "LiveBroker", "currency": "USD"},
        symbols={
            "XAUUSD": {
                "bid": 100.0,
                "ask": 100.2,
                "spread": 0.2,
                "timestamp": now,
            }
        },
    )


def test_readiness_requires_expected_account_server_and_fresh_tick():
    now = datetime.now(timezone.utc)
    connector = FakeConnector(
        account={"login": 123, "server": "LiveBroker-Demo"},
        symbols={"XAUUSD": {"bid": 100.0, "ask": 100.2, "timestamp": now}},
    )

    report = validate_live_readiness(
        connector,
        expected_login=456,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "account_login_mismatch" in report.reasons
    assert "account_server_mismatch" in report.reasons


def test_valid_readiness_is_ready():
    now = datetime.now(timezone.utc)
    report = validate_live_readiness(
        valid_connector(now),
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is True
    assert report.reasons == ()


def test_readiness_rejects_unavailable_and_stale_data():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now - timedelta(seconds=31))
    connector.connected = False
    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "mt5_unavailable" in report.reasons

    connector.connected = True
    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )
    assert "XAUUSD:stale_tick" in report.reasons
