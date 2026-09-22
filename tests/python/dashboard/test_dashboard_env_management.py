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


def test_sensitive_environment_widget_state_is_cleared_after_submit(monkeypatch):
    """Raw replacement values must not remain in Streamlit session state."""
    dashboard_app.st.session_state["dashboard_env_sensitive_API_TOKEN"] = "secret"
    dashboard_app.st.session_state["dashboard_env_admin_token"] = "admin-secret"

    dashboard_app._clear_sensitive_environment_inputs(
        ["API_TOKEN", "DATABASE_URL"]
    )

    assert "dashboard_env_sensitive_API_TOKEN" not in dashboard_app.st.session_state
    assert "dashboard_env_admin_token" not in dashboard_app.st.session_state
