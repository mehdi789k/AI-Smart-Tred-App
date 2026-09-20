"""Regression tests for dashboard execution scheduling."""

from src.python.dashboard.app import _live_trading_fragment_interval


def test_live_trading_fragment_is_not_scheduled_when_trading_is_inactive():
    """Inactive dashboards must not keep Streamlit in a perpetual running state."""
    assert _live_trading_fragment_interval(False) is None


def test_live_trading_fragment_keeps_the_configured_interval_when_active():
    """Authorized live trading keeps its bounded five-second polling interval."""
    assert _live_trading_fragment_interval(True) == 5.0
