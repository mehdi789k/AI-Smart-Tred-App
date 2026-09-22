from __future__ import annotations

import tomllib
from pathlib import Path

from src.python.execution import LiveTradingLoop
from src.python.execution.live_order_workflow import LiveOrderConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_NON_SECRET_ENV = {
    "MT5_LIVE_SYMBOLS": "XAUUSD_l",
    "MT5_MAX_POSITION_VOLUME": "0.02",
    "MT5_MAX_DAILY_LOSS": "10",
    "MT5_DEMO_MAX_DAILY_LOSS": "10",
    "MT5_AUTO_TRADING_ENABLED": "false",
}


def _pyproject() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_static_analysis_targets_canonical_python_runtime() -> None:
    config = _pyproject()
    tool = config["tool"]
    assert isinstance(tool, dict)
    ruff = tool["ruff"]
    mypy = tool["mypy"]
    assert isinstance(ruff, dict)
    assert isinstance(mypy, dict)
    assert ruff["target-version"] == "py312"
    assert mypy["python_version"] == "3.12"


def test_bounded_demo_configuration_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("MT5_LIVE_SYMBOLS", "XAUUSD_l")
    monkeypatch.setenv("MT5_MAX_POSITION_VOLUME", "0.02")
    monkeypatch.setenv("MT5_MAX_DAILY_LOSS", "10")
    monkeypatch.setenv("MT5_DEMO_MAX_DAILY_LOSS", "10")
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "false")

    config = LiveOrderConfig.from_environment()

    assert not LiveTradingLoop.enabled_by_server()
    assert config.allowed_symbols == frozenset({"XAUUSD_L"})
    assert config.max_position_volume == 0.02
    assert config.max_daily_loss == 10
    assert config.demo_activation is not None
    assert config.demo_activation.max_daily_loss == 10.0


def test_project_version_file_is_python_312() -> None:
    assert (PROJECT_ROOT / ".python-version").read_text(
        encoding="utf-8"
    ).strip() == "3.12"


def test_env_example_preserves_canonical_non_secret_values() -> None:
    values: dict[str, str] = {}
    env_example = PROJECT_ROOT / ".env.example"
    with env_example.open(encoding="utf-8") as handle:
        for line in handle:
            key, separator, value = line.partition("=")
            if separator and key.strip() in CANONICAL_NON_SECRET_ENV:
                values[key.strip()] = value.strip()

    assert values == CANONICAL_NON_SECRET_ENV


def test_local_demo_startup_enables_guarded_auto_trading_for_selected_mode() -> None:
    startup_script = (PROJECT_ROOT / "scripts" / "start_local_demo.ps1").read_text(
        encoding="utf-8"
    )

    assert '$env:MT5_AUTO_TRADING_ENABLED = "true"' in startup_script
    assert 'if ($TradingMode -eq "Demo")' in startup_script
    assert '$env:MT5_DEMO_ENABLED = "true"' in startup_script
    assert '$env:MT5_LEGACY_ORDER_PATH_ENABLED = "false"' in startup_script
