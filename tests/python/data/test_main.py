from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.config import DataConfig, Timeframe
from src.python.data.main import build_parser


def test_stage_a_cli_defaults_to_xauusd_m5_bar_target():
    args = build_parser().parse_args(["--stage-a"])

    assert args.stage_a is True
    assert args.bars == 30_000


def test_stage_a_cli_accepts_explicit_bar_count():
    args = build_parser().parse_args(["--stage-a", "--bars", "45000"])

    assert args.stage_a is True
    assert args.bars == 45_000


def test_data_config_rejects_invalid_environment_values():
    with pytest.raises(ValueError, match="MT5 login"):
        DataConfig(mt5_login=0)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        DataConfig(database_url="")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        DataConfig(database_url=None)

    with pytest.raises(ValueError, match="log level"):
        DataConfig(log_level="TRACE")


@pytest.mark.parametrize(
    ("mt5_login", "mt5_password", "mt5_server"),
    [
        (123, None, "demo-server"),
        (123, "demo-password", None),
        (None, "demo-password", "demo-server"),
    ],
)
def test_data_config_rejects_partial_mt5_credentials(
    mt5_login, mt5_password, mt5_server
):
    """A partially configured account must never reach the MT5 connector."""

    with pytest.raises(ValueError, match="MT5 credentials"):
        DataConfig(
            mt5_login=mt5_login,
            mt5_password=mt5_password,
            mt5_server=mt5_server,
        )


def test_data_config_from_env_rejects_partial_mt5_credentials(monkeypatch, tmp_path):
    """Dotenv/environment loading must enforce the same all-or-none contract."""

    monkeypatch.setenv("MT5_LOGIN", "123")
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.setenv("MT5_SERVER", "demo-server")

    with pytest.raises(ValueError, match="MT5 credentials"):
        DataConfig.from_env(tmp_path / "missing.env")


def test_data_config_normalizes_docker_postgres_and_symbols():
    config = DataConfig(
        mt5_login=123,
        mt5_password="secret",
        mt5_server="default",
        symbols=("eurusd", " xauusd ",),
        timeframes=("m1", "M5"),
        database_url="postgresql://postgres:postgres@db:5432/mt5_data",
    )

    assert config.symbols == ("eurusd", "xauusd")
    assert [value.value for value in config.timeframes] == ["M1", "M5"]
    assert config.database_url.startswith("postgresql://")


def test_data_config_preserves_case_sensitive_broker_symbol_suffix():
    config = DataConfig(symbols=("XAUUSD_l",))

    assert config.symbols == ("XAUUSD_l",)


def test_data_config_accepts_none_for_optional_runtime_fields():
    config = DataConfig(
        mt5_login=None,
        mt5_password=None,
        mt5_server=None,
        mt5_terminal_path=None,
        symbols=None,
        timeframes=None,
        database_url="postgresql://localhost/mt5_data",
        zmq_endpoint=None,
        log_file=None,
    )

    assert config.symbols == ()
    assert tuple(value.value for value in config.timeframes) == tuple(item.value for item in Timeframe)
    assert config.database_url == "postgresql://localhost/mt5_data"
    assert config.zmq_endpoint is None or config.zmq_endpoint.startswith("tcp://")
