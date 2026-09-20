from datetime import datetime, timezone

import pytest

from src.python.data.database import AsyncDatabase
from src.python.data.models import OrderTransition, Position, PositionTransition
from src.python.data.state_machine import InvalidTransition, validate_order_transition


def test_order_state_machine_rejects_terminal_state_reentry():
    with pytest.raises(InvalidTransition):
        validate_order_transition("filled", "cancelled")


@pytest.mark.asyncio
async def test_transitions_persist_audit_metadata(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.initialize()
    try:
        repository = database.repository
        await repository.create_order_intent(
            "order-1",
            "idem-1",
            {"symbol": "EURUSD", "side": "buy", "quantity": 1},
        )
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await repository.transition_order(
            "order-1",
            "accepted",
            actor="broker",
            correlation_id="corr-1",
            timestamp=at,
            payload={"ticket": 42},
        )
        async with database.session_factory() as session:
            events = list((await session.execute(
                __import__("sqlalchemy").select(OrderTransition)
            )).scalars())
        assert [event.to_status for event in events] == ["pending", "accepted"]
        assert events[-1].actor == "broker"
        assert events[-1].correlation_id == "corr-1"
        assert events[-1].timestamp == at
        assert events[-1].payload == {"ticket": 42}

        await repository.insert_position({
            "position_id": "position-1",
            "symbol": "EURUSD",
            "side": "buy",
            "quantity": 1,
            "average_price": 1.1,
        })
        await repository.transition_position(
            "position-1", "closed", actor="broker", correlation_id="corr-2"
        )
        async with database.session_factory() as session:
            position_events = list((await session.execute(
                __import__("sqlalchemy").select(PositionTransition)
            )).scalars())
        assert position_events[-1].to_status == "closed"
        assert position_events[-1].payload == {}
    finally:
        await database.dispose()
