import pytest

from scripts.verify_demo_readiness import validate_demo_readiness


def test_demo_readiness_accepts_safe_connected_state():
    validate_demo_readiness(
        {"data": {"status": "ok"}},
        {
            "data": {
                "status": "ready",
                "trading": {"allowed": True},
                "dependencies": {"mt5": "ready", "circuit_breaker": "armed"},
            }
        },
    )


def test_demo_readiness_requires_explicit_demo_identity_for_trading():
    validate_demo_readiness(
        {"data": {"status": "ok"}},
        {
            "data": {
                "status": "ready",
                "account": {"trade_mode": "demo"},
                "trading": {"allowed": True},
                "dependencies": {"mt5": "ready", "circuit_breaker": "armed"},
            }
        },
        require_demo_trading=True,
    )


@pytest.mark.parametrize(
    "account",
    [{}, {"trade_mode": "real"}, {"trade_mode": "unknown"}],
)
def test_demo_readiness_rejects_unverified_demo_identity(account):
    with pytest.raises(RuntimeError, match="not verified Demo"):
        validate_demo_readiness(
            {"data": {"status": "ok"}},
            {
                "data": {
                    "status": "ready",
                    "account": account,
                    "trading": {"allowed": True},
                    "dependencies": {"mt5": "ready", "circuit_breaker": "armed"},
                }
            },
            require_demo_trading=True,
        )


def test_demo_readiness_accepts_unavailable_api_mt5_for_direct_dashboard():
    validate_demo_readiness(
        {"data": {"status": "ok"}},
        {
            "data": {
                "status": "ready",
                "trading": {"allowed": False},
                "dependencies": {
                    "mt5": "unavailable",
                    "circuit_breaker": "not_configured",
                },
            }
        },
        allow_direct_dashboard=True,
    )


def test_direct_dashboard_mode_cannot_authorize_demo_trading():
    with pytest.raises(RuntimeError, match="cannot authorize"):
        validate_demo_readiness(
            {"data": {"status": "ok"}},
            {"data": {"status": "ready"}},
            allow_direct_dashboard=True,
            require_demo_trading=True,
        )


def test_demo_readiness_rejects_unsafe_demo_limits():
    with pytest.raises(RuntimeError, match="single allowed symbol"):
        validate_demo_readiness(
            {"data": {"status": "ok"}},
            {
                "data": {
                    "status": "ready",
                    "trading": {"allowed": True},
                    "dependencies": {"mt5": "ready", "circuit_breaker": "armed"},
                },
            },
            demo_symbols=["EURUSD", "XAUUSD"],
            max_daily_loss=100,
        )


@pytest.mark.parametrize(
    "readiness",
    [
        {"data": {"status": "ready", "trading": {"allowed": False}}},
        {
            "data": {
                "status": "ready",
                "trading": {"allowed": True},
                "dependencies": {"mt5": "unavailable"},
            }
        },
        {
            "data": {
                "status": "ready",
                "trading": {"allowed": True},
                "dependencies": {"mt5": "ready", "circuit_breaker": "tripped"},
            }
        },
    ],
)
def test_demo_readiness_fails_closed(readiness):
    with pytest.raises(RuntimeError):
        validate_demo_readiness({"data": {"status": "ok"}}, readiness)
