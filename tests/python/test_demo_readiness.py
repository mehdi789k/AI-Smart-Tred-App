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
