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


def test_broker_disabled_loop_keeps_dashboard_activity_persisted(monkeypatch):
    """Broker execution failures must not turn off the dashboard runtime."""
    persisted_states = []
    session_state = SimpleNamespace(trading_active=True)
    session_state.get = lambda key, default=None: getattr(session_state, key, default)
    monkeypatch.setattr(dashboard_app.st, "session_state", session_state)
    monkeypatch.setattr(
        dashboard_app,
        "_persist_auto_trading_state",
        lambda enabled, *_: persisted_states.append(enabled),
    )

    dashboard_app._sync_live_trading_state(
        SimpleNamespace(active=True, last_status="broker_trading_disabled")
    )

    assert dashboard_app.st.session_state.trading_active is True
    assert persisted_states == []


def test_persisted_auto_trading_requires_connected_trade_enabled_account(monkeypatch):
    """Auto-trading restore must fail closed without explicit MT5 permission."""
    class Connector:
        def __init__(self, connected, mode, allowed=True):
            self.connected = connected
            self.mode = mode
            self.allowed = allowed

        def is_connected(self):
            return self.connected

        def get_account_summary(self):
            return {
                "trade_mode": self.mode,
                "account_trade_allowed": self.allowed,
                "terminal_trade_allowed": True,
                "terminal_tradeapi_disabled": False,
            }

    monkeypatch.setattr(dashboard_app, "mt5_connector", Connector(True, "contest"))
    assert dashboard_app._active_trading_connector() is False
    monkeypatch.setattr(dashboard_app, "mt5_connector", Connector(True, "real"))
    assert dashboard_app._active_trading_connector() is True
    monkeypatch.setattr(
        dashboard_app, "mt5_connector", Connector(True, "demo", allowed=False)
    )
    assert dashboard_app._active_trading_connector() is False


def test_dashboard_refresh_auto_connects_disconnected_mt5(monkeypatch):
    """A new dashboard session should connect to MT5 without a button click."""
    class Connector:
        def __init__(self):
            self.connect_calls = 0
            self.connected = False

        def is_connected(self):
            return self.connected

        def connect(self, **_kwargs):
            self.connect_calls += 1
            self.connected = True
            return True

    connector = Connector()
    session_state = SimpleNamespace()
    session_state.get = lambda key, default=None: getattr(session_state, key, default)
    monkeypatch.setattr(dashboard_app, "mt5_connector", connector)
    monkeypatch.setattr(dashboard_app.st, "session_state", session_state)
    monkeypatch.setattr(dashboard_app, "mt5_enabled", True)

    assert dashboard_app._auto_connect_mt5() is True
    assert connector.connect_calls == 1


def test_dashboard_refresh_reports_auto_connect_failure_without_raising(monkeypatch):
    """A failed automatic connection must leave the dashboard available."""
    class Connector:
        def is_connected(self):
            return False

        def connect(self, **_kwargs):
            raise RuntimeError("terminal unavailable")

    session_state = SimpleNamespace()
    session_state.get = lambda key, default=None: getattr(session_state, key, default)
    monkeypatch.setattr(dashboard_app, "mt5_connector", Connector())
    monkeypatch.setattr(dashboard_app.st, "session_state", session_state)
    monkeypatch.setattr(dashboard_app, "mt5_enabled", True)

    assert dashboard_app._auto_connect_mt5() is False
