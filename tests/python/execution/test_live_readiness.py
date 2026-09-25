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

    def is_demo_account(self):
        return self.account.get("trade_mode") == "demo"

    def get_symbols_list(self, visible_only=True):
        return [{"symbol": symbol} for symbol in self.visible]

    def get_symbol_info(self, symbol):
        return self.symbols.get(symbol, {})


def valid_connector(now):
    return FakeConnector(
        account={
            "login": 123,
            "server": "LiveBroker",
            "trade_mode": "demo",
            "currency": "USD",
            "account_trade_allowed": True,
            "terminal_trade_allowed": True,
            "terminal_tradeapi_disabled": False,
        },
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
        account={"login": 123, "server": "LiveBroker-Demo", "trade_mode": "demo"},
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


def test_readiness_requires_account_and_terminal_trade_permissions():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now)
    connector.account.update(
        account_trade_allowed=False,
        terminal_trade_allowed=True,
        terminal_tradeapi_disabled=False,
    )

    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "account_trade_disabled" in report.reasons

    connector.account.update(
        account_trade_allowed=True,
        terminal_trade_allowed=False,
    )
    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "terminal_trade_disabled" in report.reasons


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


def test_readiness_accepts_trade_enabled_demo_and_live_accounts():
    now = datetime.now(timezone.utc)
    for trade_mode in ("demo", "real"):
        account = {
            "login": 123,
            "server": "LiveBroker",
            "trade_mode": trade_mode,
            "account_trade_allowed": True,
            "terminal_trade_allowed": True,
            "terminal_tradeapi_disabled": False,
        }
        report = validate_live_readiness(
            FakeConnector(
                account=account,
                symbols={"XAUUSD": {"bid": 100.0, "ask": 100.2, "timestamp": now}},
            ),
            expected_login=123,
            expected_server="LiveBroker",
            allowed_symbols=frozenset({"XAUUSD"}),
            now=now,
        )
        assert report.ready is True
        assert report.reasons == ()


def test_readiness_rejects_unsupported_or_unknown_account_mode():
    now = datetime.now(timezone.utc)
    for trade_mode in ("contest", None):
        account = {
            "login": 123,
            "server": "LiveBroker",
            "trade_mode": trade_mode,
            "account_trade_allowed": True,
            "terminal_trade_allowed": True,
            "terminal_tradeapi_disabled": False,
        }
        report = validate_live_readiness(
            FakeConnector(
                account=account,
                symbols={"XAUUSD": {"bid": 100.0, "ask": 100.2, "timestamp": now}},
            ),
            expected_login=123,
            expected_server="LiveBroker",
            allowed_symbols=frozenset({"XAUUSD"}),
            now=now,
        )
        assert report.ready is False
        assert "account_mode_unsupported" in report.reasons


def test_readiness_account_identity_is_redacted_to_known_fields():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now)
    connector.account["password"] = "must-not-be-returned"
    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )
    assert report.account["trade_mode"] == "demo"
    assert "password" not in report.account


def test_readiness_rejects_invisible_symbol():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now)
    connector.visible = set()

    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "XAUUSD:symbol_not_visible" in report.reasons


def test_readiness_rejects_malformed_non_positive_and_reversed_prices():
    now = datetime.now(timezone.utc)
    for bid, ask in (
        ("bad", 100.2),
        (0.0, 100.2),
        (100.2, 100.0),
    ):
        connector = valid_connector(now)
        connector.symbols["XAUUSD"].update(bid=bid, ask=ask)
        report = validate_live_readiness(
            connector,
            expected_login=123,
            expected_server="LiveBroker",
            allowed_symbols=frozenset({"XAUUSD"}),
            now=now,
        )
        assert report.ready is False
        assert "XAUUSD:invalid_tick" in report.reasons
