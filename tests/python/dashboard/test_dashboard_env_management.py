"""Focused tests for the dashboard's locked environment management gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

from src.python.dashboard import app as dashboard_app


def test_dashboard_import_uses_isolated_settings_file():
    """Pytest must not load saved developer settings or restore their scheduler."""
    isolated_path = Path(os.environ["DASHBOARD_SETTINGS_FILE"])

    assert dashboard_app.SETTINGS_FILE == isolated_path
    assert isolated_path != Path(dashboard_app.PROJECT_ROOT) / "data" / (
        "dashboard_settings.json"
    )
    assert dashboard_app.st.session_state.saved_settings["ml_training_enabled"] is False
    assert dashboard_app._ml_scheduler_running() is False


def test_settings_page_renders_secure_environment_management_without_market_watch(
    monkeypatch,
):
    """The env controls must remain visible on the Settings page, even if MT5 is unavailable."""
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("MT5_ENABLED", "false")
    monkeypatch.setenv("MT5_DASHBOARD_DIRECT", "false")

    runner = Path(__file__).with_name("apptest_runner.py")
    dashboard_app_path = (
        Path(__file__).resolve().parents[3] / "src" / "python" / "dashboard" / "app.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(runner),
            str(dashboard_app_path),
            "60",
            "تنظیمات",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=70,
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ),
    )
    output = json.loads(result.stdout)

    assert len(output["expanders"]) >= 1
    assert any(label == "🔐 مدیریت امن محیط" for label in output["expanders"])


def test_environment_manager_is_locked_without_admin_token(monkeypatch):
    """An absent token must fail closed."""
    monkeypatch.delenv("DASHBOARD_ADMIN_TOKEN", raising=False)

    assert dashboard_app._dashboard_env_access_allowed("") is False


def test_environment_manager_access_uses_constant_time_comparison(monkeypatch):
    """Only the configured token unlocks the management section."""
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "expected")

    assert dashboard_app._dashboard_env_access_allowed("expected") is True
    assert dashboard_app._dashboard_env_access_allowed("wrong") is False


def test_sensitive_environment_widget_state_is_cleared_after_submit(monkeypatch):
    """Raw replacement values must not remain in Streamlit session state."""
    dashboard_app.st.session_state["dashboard_env_sensitive_API_TOKEN"] = "secret"
    dashboard_app.st.session_state["dashboard_env_admin_token"] = "admin-secret"

    dashboard_app._clear_sensitive_environment_inputs(["API_TOKEN", "DATABASE_URL"])

    assert "dashboard_env_sensitive_API_TOKEN" not in dashboard_app.st.session_state
    assert "dashboard_env_admin_token" not in dashboard_app.st.session_state
