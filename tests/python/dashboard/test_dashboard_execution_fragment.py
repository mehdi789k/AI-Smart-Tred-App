"""Regression tests for dashboard execution scheduling."""

from types import SimpleNamespace

import src.python.dashboard.app as dashboard_app
from src.python.dashboard.app import _live_trading_fragment_interval


def test_live_trading_fragment_is_not_scheduled_when_trading_is_inactive():
    """Inactive dashboards must not keep Streamlit in a perpetual running state."""
    assert _live_trading_fragment_interval(False) is None


def test_live_trading_fragment_keeps_the_configured_interval_when_active():
    """Authorized live trading keeps its bounded five-second polling interval."""
    assert _live_trading_fragment_interval(True) == 5.0


def test_live_trading_fragment_clears_session_after_loop_stops(monkeypatch):
    """A fail-closed loop stop must not be re-created on the next Streamlit rerun."""
    persisted_states = []
    session_state = SimpleNamespace(trading_active=True)
    session_state.get = lambda key, default=None: getattr(session_state, key, default)
    monkeypatch.setattr(
        dashboard_app.st,
        "session_state",
        session_state,
    )
    monkeypatch.setattr(
        dashboard_app,
        "_persist_auto_trading_state",
        lambda enabled, *_: persisted_states.append(enabled),
    )

    dashboard_app._sync_live_trading_state(SimpleNamespace(active=False))

    assert dashboard_app.st.session_state.trading_active is False
    assert persisted_states == [False]


def test_persisted_auto_trading_requires_connected_demo_connector(monkeypatch):
    """Auto-trading restore must fail closed without an explicit Demo account."""
    class Connector:
        def __init__(self, connected, demo):
            self.connected = connected
            self.demo = demo

        def is_connected(self):
            return self.connected

        def is_demo_account(self):
            return self.demo

    monkeypatch.setattr(dashboard_app, "mt5_connector", Connector(True, False))
    assert dashboard_app._active_demo_connector() is False
    monkeypatch.setattr(dashboard_app, "mt5_connector", Connector(True, True))
    assert dashboard_app._active_demo_connector() is True
