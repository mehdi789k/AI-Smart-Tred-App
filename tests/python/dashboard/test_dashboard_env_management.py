"""Focused tests for the dashboard's locked environment management gate."""

from src.python.dashboard import app as dashboard_app


def test_environment_manager_is_locked_without_admin_token(monkeypatch):
    """An absent token must fail closed."""
    monkeypatch.delenv("DASHBOARD_ADMIN_TOKEN", raising=False)

    assert dashboard_app._dashboard_env_access_allowed("") is False


def test_environment_manager_access_uses_constant_time_comparison(monkeypatch):
    """Only the configured token unlocks the management section."""
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "expected")

    assert dashboard_app._dashboard_env_access_allowed("expected") is True
    assert dashboard_app._dashboard_env_access_allowed("wrong") is False
