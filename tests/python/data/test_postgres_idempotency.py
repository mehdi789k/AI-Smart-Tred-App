"""Integration coverage for atomic idempotency claims on PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from src.python.data.database import (
    AsyncDatabase,
    ConcurrencyConflict,
    IdempotencyInProgress,
)
from src.python.data.models import ExecutionControlState

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set TEST_POSTGRES_URL to run the PostgreSQL integration test",
)


def test_two_independent_postgres_connections_have_one_idempotency_owner():
    async def scenario():
        first = AsyncDatabase(POSTGRES_URL)
        second = AsyncDatabase(POSTGRES_URL)
        await first.initialize()
        await second.initialize()
        key = f"postgres-concurrent-claim-{uuid4()}"
        first_result, second_result = await asyncio.gather(
            first.repository.claim_idempotency(key, "same-request-hash"),
            second.repository.claim_idempotency(key, "same-request-hash"),
            return_exceptions=True,
        )
        outcomes = (first_result, second_result)
        assert sum(value is None for value in outcomes) == 1
        assert sum(isinstance(value, IdempotencyInProgress) for value in outcomes) == 1
        await first.repository.complete_idempotency(
            key, {"accepted": True}, status="completed"
        )
        assert await second.repository.claim_idempotency(key, "same-request-hash") == {
            "accepted": True
        }
        await first.dispose()
        await second.dispose()

    asyncio.run(scenario())


def test_two_postgres_workers_cannot_both_update_execution_control():
    async def scenario():
        first = AsyncDatabase(POSTGRES_URL)
        second = AsyncDatabase(POSTGRES_URL)
        await first.initialize()
        await second.initialize()
        scope = f"postgres-concurrent-control-{uuid4()}"
        try:
            state = await first.repository.provision_execution_control(scope)
            outcomes = await asyncio.gather(
                first.repository.save_execution_control(
                    scope,
                    expected_version=state.version,
                    emergency_stop=True,
                    actor="first",
                ),
                second.repository.save_execution_control(
                    scope,
                    expected_version=state.version,
                    emergency_stop=True,
                    actor="second",
                ),
                return_exceptions=True,
            )
            assert sum(
                isinstance(value, ExecutionControlState) for value in outcomes
            ) == 1
            assert sum(isinstance(value, ConcurrencyConflict) for value in outcomes) == 1
            current = await first.repository.load_execution_control(scope)
            assert current is not None
            assert current.version == state.version + 1
            assert current.emergency_stop is True
        finally:
            await first.dispose()
            await second.dispose()

    asyncio.run(scenario())
