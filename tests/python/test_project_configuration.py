from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from tempfile import gettempdir

import yaml

from src.python.dashboard.env_manager import build_demo_profile
from src.python.execution import LiveTradingLoop
from src.python.execution.live_order_workflow import (
    DemoActivationConfig,
    LiveOrderConfig,
    LiveOrderWorkflow,
)

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


def test_api_auto_trading_flag_is_separate_from_dashboard_demo_flag() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert (
        compose["services"]["api"]["environment"]["MT5_AUTO_TRADING_ENABLED"]
        == "${API_MT5_AUTO_TRADING_ENABLED:-false}"
    )
    assert (
        compose["services"]["dashboard"]["environment"]["MT5_AUTO_TRADING_ENABLED"]
        == "${MT5_AUTO_TRADING_ENABLED:-false}"
    )


def test_local_demo_startup_enables_guarded_auto_trading_for_selected_mode() -> None:
    startup_script = (PROJECT_ROOT / "scripts" / "start_local_demo.ps1").read_text(
        encoding="utf-8"
    )

    assert '$env:MT5_AUTO_TRADING_ENABLED = "true"' in startup_script
    assert 'if ($TradingMode -eq "Demo")' in startup_script
    assert '$env:MT5_DEMO_ENABLED = "true"' in startup_script
    assert '$env:MT5_LEGACY_ORDER_PATH_ENABLED = "false"' in startup_script


def test_demo_profile_enables_bounded_guarded_automatic_trading() -> None:
    profile = build_demo_profile()

    assert profile == {
        "APP_ENV": "development",
        "MT5_ENABLED": "true",
        "MT5_DEMO_ENABLED": "true",
        "MT5_AUTO_TRADING_ENABLED": "true",
        "MT5_LEGACY_ORDER_PATH_ENABLED": "false",
        "MT5_DEMO_MAX_TRADE_VOLUME": "0.01",
        "MT5_DEMO_MAX_TRADES_PER_SESSION": "3",
        "MT5_DEMO_MAX_DAILY_LOSS": "10",
        "MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION": "true",
        "MT5_DEMO_AUTO_STOP_ON_ERROR": "true",
    }


def test_filter_modules_use_canonical_package_imports() -> None:
    filter_dir = PROJECT_ROOT / "src" / "python" / "filters"

    for path in sorted(filter_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        content = path.read_text(encoding="utf-8")
        assert "sys.path.insert" not in content
        assert "from indicators." not in content
        assert "import indicators" not in content
        assert "from src.python.indicators." in content


def test_filter_tests_use_canonical_package_imports() -> None:
    filter_tests_dir = PROJECT_ROOT / "tests" / "filters"
    legacy_modules = (
        "confirmation_filters",
        "fomo_filters",
        "fvg_filters",
        "intermarket_macro_filters",
        "smc_filters",
        "structure_break_filters",
        "structure_mtf_filters",
        "trend_filters",
        "volatility_time_filters",
    )

    for path in sorted(filter_tests_dir.glob("test_*.py")):
        content = path.read_text(encoding="utf-8")
        assert "sys.path.insert" not in content
        assert all(
            f"from {module} import" not in content
            and f"import {module}" not in content
            for module in legacy_modules
        )
        assert "from src.python.filters." in content


def test_pytest_file_logging_is_routed_to_temporary_directory() -> None:
    configured_log_dir = os.environ.get("APP_LOG_DIR")
    assert configured_log_dir
    log_dir = Path(configured_log_dir).resolve()
    assert log_dir.is_relative_to(Path(gettempdir()).resolve())

    null_device_paths = {
        os.path.normcase(os.devnull),
        os.path.normcase(r"\\.\nul"),
    }
    file_handlers = [
        handler
        for handler in logging.getLogger("ai_smart_tred").handlers
        if isinstance(handler, logging.FileHandler)
        and os.path.normcase(handler.baseFilename) not in null_device_paths
    ]
    assert file_handlers
    assert all(
        os.path.samefile(Path(handler.baseFilename).parent, log_dir)
        for handler in file_handlers
    ), [handler.baseFilename for handler in file_handlers]


def test_demo_audit_default_honors_configured_isolated_path(
    monkeypatch, tmp_path
) -> None:
    audit_path = tmp_path / "demo-audit.jsonl"
    monkeypatch.setenv("MT5_DEMO_AUDIT_LOG_PATH", str(audit_path))

    config = DemoActivationConfig(enabled=True)

    assert Path(config.audit_log_path) == audit_path


def test_workflow_without_demo_config_uses_configured_audit_path(
    monkeypatch, tmp_path
) -> None:
    audit_path = tmp_path / "workflow-audit.jsonl"
    monkeypatch.setenv("MT5_DEMO_AUDIT_LOG_PATH", str(audit_path))
    workflow = LiveOrderWorkflow(
        connector=object(),
        config=LiveOrderConfig(allowed_symbols=frozenset({"XAUUSD"})),
    )

    workflow._audit_logger.write("test_event")

    assert audit_path.exists()
