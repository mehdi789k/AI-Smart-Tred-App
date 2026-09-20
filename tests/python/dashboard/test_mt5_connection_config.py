from types import SimpleNamespace

from src.python.dashboard.mt5_connector import MT5Connection


class FakeMetaTrader:
    def __init__(self):
        self.initialize_calls = []
        self.terminal_info_calls = 0

    def initialize(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return True

    def terminal_info(self):
        self.terminal_info_calls += 1
        return SimpleNamespace(connected=True)

    def account_info(self):
        return SimpleNamespace(login=91426216, server="LiteFinance-MT5-Demo")


def make_connection(monkeypatch, fake_mt5):
    monkeypatch.setattr(
        "src.python.dashboard.mt5_connector._load_dotenv_if_present",
        lambda: None,
    )
    connection = MT5Connection()
    connection._mt5 = fake_mt5
    return connection


def test_connect_uses_demo_credentials_and_terminal(monkeypatch, tmp_path):
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"terminal")
    monkeypatch.setenv("MT5_LOGIN", "91426216")
    monkeypatch.setenv("MT5_PASSWORD", "demo-secret")
    monkeypatch.setenv("MT5_SERVER", "LiteFinance-MT5-Demo")
    monkeypatch.setenv("MT5_TERMINAL_PATH", str(terminal))
    monkeypatch.setenv("MT5_CONNECT_TIMEOUT_MS", "45000")
    fake_mt5 = FakeMetaTrader()
    connection = make_connection(monkeypatch, fake_mt5)

    assert connection.connect() is True
    assert fake_mt5.initialize_calls == [{"timeout": 45000}]


def test_connect_rejects_partial_credentials(monkeypatch):
    monkeypatch.setenv("MT5_LOGIN", "91426216")
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.setenv("MT5_SERVER", "LiteFinance-MT5-Demo")
    fake_mt5 = FakeMetaTrader()
    connection = make_connection(monkeypatch, fake_mt5)

    assert connection.connect() is False
    assert fake_mt5.initialize_calls == []


def test_connect_rejects_missing_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("MT5_LOGIN", "91426216")
    monkeypatch.setenv("MT5_PASSWORD", "demo-secret")
    monkeypatch.setenv("MT5_SERVER", "LiteFinance-MT5-Demo")
    monkeypatch.setenv("MT5_TERMINAL_PATH", str(tmp_path / "missing.exe"))
    fake_mt5 = FakeMetaTrader()
    connection = make_connection(monkeypatch, fake_mt5)

    assert connection.connect() is False
    assert fake_mt5.initialize_calls == []


def test_connect_uses_running_terminal_without_credentials(monkeypatch):
    for key in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_TERMINAL_PATH"):
        monkeypatch.delenv(key, raising=False)
    fake_mt5 = FakeMetaTrader()
    connection = make_connection(monkeypatch, fake_mt5)

    assert connection.connect() is True
    assert fake_mt5.initialize_calls == [{"timeout": 60000}]


def test_repeated_connection_checks_use_health_cache(monkeypatch):
    for key in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_TERMINAL_PATH"):
        monkeypatch.delenv(key, raising=False)
    fake_mt5 = FakeMetaTrader()
    connection = make_connection(monkeypatch, fake_mt5)

    assert connection.connect() is True
    connection._last_health_check = 0
    assert connection.is_connected() is True
    assert connection.is_connected() is True

    assert fake_mt5.terminal_info_calls == 1
