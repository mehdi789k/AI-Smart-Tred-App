from threading import RLock
from types import SimpleNamespace

from src.python.dashboard.mt5_connector import MT5Connection, normalize_mt5_trade_mode


def test_normalize_mt5_trade_mode_accepts_mt5_demo_values():
    mt5_module = SimpleNamespace(
        TRADE_MODE_DEMO=0,
        TRADE_MODE_REAL=2,
        TRADE_MODE_CONTEST=1,
    )

    assert normalize_mt5_trade_mode(mt5_module.TRADE_MODE_DEMO, mt5_module=mt5_module) == "demo"
    assert normalize_mt5_trade_mode("demo", mt5_module=mt5_module) == "demo"
    assert normalize_mt5_trade_mode("trade_mode_real", mt5_module=mt5_module) == "real"
    assert normalize_mt5_trade_mode("account_trade_mode_contest", mt5_module=mt5_module) == "contest"
    assert normalize_mt5_trade_mode("unknown", mt5_module=mt5_module) is None


def test_mt5_connection_is_demo_account_uses_explicit_trade_mode():
    connection = MT5Connection.__new__(MT5Connection)
    connection._mt5 = SimpleNamespace(
        TRADE_MODE_DEMO=0,
        TRADE_MODE_REAL=2,
        TRADE_MODE_CONTEST=1,
    )
    connection._api_lock = RLock()
    connection.connected = True

    connection.get_account_summary = lambda: {
        "login": 123,
        "server": "DemoServer",
        "trade_mode": 0,
    }
    assert connection.is_demo_account() is True

    connection.get_account_summary = lambda: {
        "login": 123,
        "server": "DemoServer",
        "trade_mode": 2,
    }
    assert connection.is_demo_account() is False


def test_connect_uses_configured_terminal_path(monkeypatch):
    initialize_calls = []

    class FakeMT5:
        def initialize(self, **kwargs):
            initialize_calls.append(kwargs)
            return True

        def account_info(self):
            return SimpleNamespace(login=123, server="DemoServer")

        def terminal_info(self):
            return SimpleNamespace(connected=True)

    connection = MT5Connection.__new__(MT5Connection)
    connection._mt5 = FakeMT5()
    connection._api_lock = RLock()
    connection.connected = False
    connection._last_health_check = 0.0
    connection.last_update = None
    connection.last_connection_error = None
    terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    monkeypatch.setenv("MT5_TERMINAL_PATH", terminal_path)
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)
    monkeypatch.setattr(
        "src.python.dashboard.mt5_connector.Path.is_file",
        lambda _path: True,
    )

    assert connection.connect() is True
    assert initialize_calls == [{"path": terminal_path, "timeout": 60000}]


def test_account_summary_exposes_authoritative_trade_permissions():
    connection = MT5Connection.__new__(MT5Connection)
    connection._mt5 = SimpleNamespace(
        terminal_info=lambda: SimpleNamespace(
            connected=True,
            trade_allowed=True,
            tradeapi_disabled=False,
        ),
        account_info=lambda: SimpleNamespace(
            login=123,
            server="DemoServer",
            trade_mode=0,
            trade_allowed=False,
            trade_expert=True,
            balance=1000,
            equity=1000,
            margin=0,
            margin_free=1000,
            margin_level=0,
            profit=0,
            currency="USD",
            leverage=100,
        ),
    )
    connection._api_lock = RLock()
    connection.connected = True
    connection._last_health_check = 0.0
    connection._health_check_interval = 30.0

    summary = connection.get_account_summary()

    assert summary["terminal_trade_allowed"] is True
    assert summary["terminal_tradeapi_disabled"] is False
    assert summary["account_trade_allowed"] is False
    assert summary["account_trade_expert"] is True
    assert summary["account_trade_status"] == "disabled_or_investor_mode"

    connection.get_account_summary = lambda: {
        "login": 123,
        "server": "DemoServer",
    }
    assert connection.is_demo_account() is False
