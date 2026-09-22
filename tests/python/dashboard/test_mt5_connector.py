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

    connection.get_account_summary = lambda: {
        "login": 123,
        "server": "DemoServer",
    }
    assert connection.is_demo_account() is False
