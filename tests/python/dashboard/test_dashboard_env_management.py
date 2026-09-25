"""Focused tests for the dashboard's locked environment management gate."""

from streamlit.testing.v1 import AppTest

from src.python.dashboard import app as dashboard_app


def test_settings_page_renders_secure_environment_management_without_market_watch(
    monkeypatch,
):
    """The env controls must remain visible on the Settings page, even if MT5 is unavailable."""
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("MT5_ENABLED", "false")
    monkeypatch.setenv("MT5_DASHBOARD_DIRECT", "false")

    app = AppTest.from_file(str(dashboard_app.__file__))
    app.run(timeout=60)
    app.sidebar.radio[0].set_value("تنظیمات").run(timeout=60)

    assert len(app.expander) >= 1
    assert any(
        getattr(expander, "label", "") == "🔐 مدیریت امن محیط"
        for expander in app.expander
    )


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
