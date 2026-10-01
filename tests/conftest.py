"""Shared test isolation for application infrastructure."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

_TEST_RUNTIME_DIR: Path | None = None
_TEST_LOG_DIR: Path | None = None

_SAFE_TEST_ENVIRONMENT = {
    "APP_ENV": "",
    "MT5_ENABLED": "false",
    "MT5_AUTO_TRADING_ENABLED": "false",
    "MT5_DEMO_ENABLED": "false",
    "MT5_DASHBOARD_DIRECT": "false",
}


def pytest_configure(config: pytest.Config) -> None:
    """Isolate test persistence and disable developer live-mode settings."""
    global _TEST_RUNTIME_DIR, _TEST_LOG_DIR

    os.environ.update(_SAFE_TEST_ENVIRONMENT)
    _TEST_RUNTIME_DIR = (
        Path(tempfile.gettempdir()) / f"smart-mt5-test-runtime-{uuid.uuid4().hex}"
    )
    _TEST_LOG_DIR = _TEST_RUNTIME_DIR / "logs"
    _TEST_LOG_DIR.mkdir(parents=True)
    os.environ["DASHBOARD_SETTINGS_FILE"] = str(
        _TEST_RUNTIME_DIR / "dashboard_settings.json"
    )
    os.environ["APP_LOG_DIR"] = str(_TEST_LOG_DIR)
    os.environ["LOG_FILE"] = str(_TEST_LOG_DIR / "data_collector.log")
    os.environ["MT5_DEMO_AUDIT_LOG_PATH"] = str(
        _TEST_LOG_DIR / "demo_activation_audit.jsonl"
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    """Close test log handles and remove this run's temporary runtime files."""
    if _TEST_RUNTIME_DIR is None or _TEST_LOG_DIR is None:
        return

    application_logger = logging.getLogger("ai_smart_tred")
    for handler in tuple(application_logger.handlers):
        if isinstance(handler, logging.FileHandler) and Path(
            handler.baseFilename
        ).resolve().is_relative_to(_TEST_LOG_DIR.resolve()):
            application_logger.removeHandler(handler)
            handler.close()

    if _TEST_RUNTIME_DIR.exists():
        shutil.rmtree(_TEST_RUNTIME_DIR)


@pytest.fixture(autouse=True)
def isolate_application_database(tmp_path, monkeypatch):
    """Give each test isolated local services without inheriting live-mode settings."""

    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}",
    )
    monkeypatch.setenv("APP_ENV", "")
    monkeypatch.setenv("MT5_ENABLED", "false")
    monkeypatch.setenv("MT5_AUTO_TRADING_ENABLED", "false")
    monkeypatch.setenv("MT5_DEMO_ENABLED", "false")
    monkeypatch.setenv("MT5_DASHBOARD_DIRECT", "false")
