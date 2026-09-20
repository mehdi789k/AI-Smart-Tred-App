"""Regression tests for durable execution safety state."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from src.python.data.database import AsyncDatabase, ConcurrencyConflict
from src.python.data.models import ExecutionControlState


@pytest.mark.asyncio
async def test_execution_control_round_trips_utc_and_state(tmp_path):
    """A missing scope is initialized with safe defaults and UTC timestamps."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        state = await database.repository.provision_execution_control("demo:XAUUSD")

        assert state.scope == "demo:XAUUSD"
        assert state.emergency_stop is False
        assert state.demo_active is False
        assert state.demo_trade_count == 0
        assert state.daily_loss == 0
        assert state.version == 1
        assert state.updated_at.tzinfo == timezone.utc
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_execution_control_update_requires_current_version(tmp_path):
    """A stale version cannot overwrite newer execution safety state."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        state = await database.repository.provision_execution_control("demo:XAUUSD")
        updated = await database.repository.save_execution_control(
            "demo:XAUUSD",
            expected_version=state.version,
            emergency_stop=True,
            emergency_stop_reason="operator stop",
            actor="operator",
        )

        assert updated.emergency_stop is True
        assert updated.emergency_stop_reason == "operator stop"
        assert updated.actor == "operator"
        assert updated.version == 2

        with pytest.raises(ConcurrencyConflict):
            await database.repository.save_execution_control(
                "demo:XAUUSD", expected_version=state.version, demo_active=True
            )

        current = await database.repository.load_execution_control("demo:XAUUSD")
        assert current.demo_active is False
        assert current.version == 2
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_execution_control_can_be_created_with_zero_expected_version(tmp_path):
    """Creation accepts only the sentinel version zero and starts at one."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        state = await database.repository.save_execution_control(
            "live:XAUUSD",
            expected_version=0,
            session_expires_at=datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc),
            demo_trade_count=3,
            daily_loss=12.5,
            actor="bootstrap",
        )

        assert state.version == 1
        assert state.session_expires_at == datetime(
            2026, 9, 16, 4, 0, tzinfo=timezone.utc
        )
        assert state.demo_trade_count == 3
        assert state.daily_loss == 12.5
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_execution_control_rejects_negative_counters(tmp_path):
    """Database constraints prevent invalid safety counters from persisting."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        with pytest.raises((IntegrityError, ValueError)):
            await database.repository.save_execution_control(
                "demo:XAUUSD", expected_version=0, demo_trade_count=-1
            )
        with pytest.raises((IntegrityError, ValueError)):
            await database.repository.save_execution_control(
                "live:XAUUSD", expected_version=0, daily_loss=-0.01
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_execution_control_persists_across_database_restart(tmp_path):
    """Safety state survives disposal and reconstruction of the database facade."""

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'control.db'}"
    first = AsyncDatabase(database_url)
    await first.initialize()
    state = await first.repository.provision_execution_control("demo:XAUUSD")
    await first.repository.save_execution_control(
        "demo:XAUUSD",
        expected_version=state.version,
        demo_active=True,
        demo_trade_count=2,
        daily_loss=4.25,
        actor="session",
    )
    await first.dispose()

    second = AsyncDatabase(database_url)
    await second.initialize()
    try:
        restored = await second.repository.load_execution_control("demo:XAUUSD")
        assert restored.demo_active is True
        assert restored.demo_trade_count == 2
        assert restored.daily_loss == 4.25
        assert restored.actor == "session"
    finally:
        await second.dispose()


@pytest.mark.asyncio
async def test_load_execution_control_is_read_only(tmp_path):
    """The live-safe loader distinguishes an absent scope from provisioned state."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        assert await database.repository.load_execution_control("live:XAUUSD") is None
        assert await database.repository.get_execution_control("live:XAUUSD") is None
        provisioned = await database.repository.provision_execution_control(
            "live:XAUUSD"
        )
        assert provisioned.version == 1
        loaded = await database.repository.load_execution_control("live:XAUUSD")
        assert loaded is not None
        assert loaded.scope == "live:XAUUSD"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_first_creation_returns_concurrency_conflict(tmp_path):
    """Only one independent repository can create version-zero state."""

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'control.db'}"
    first = AsyncDatabase(database_url)
    second = AsyncDatabase(database_url)
    await first.initialize()
    await second.initialize()
    try:
        outcomes = await asyncio.gather(
            first.repository.save_execution_control(
                "live:XAUUSD", expected_version=0, actor="first"
            ),
            second.repository.save_execution_control(
                "live:XAUUSD", expected_version=0, actor="second"
            ),
            return_exceptions=True,
        )
        assert (
            sum(isinstance(outcome, ConcurrencyConflict) for outcome in outcomes) == 1
        )
        assert (
            sum(isinstance(outcome, ExecutionControlState) for outcome in outcomes) == 1
        )
    finally:
        await first.dispose()
        await second.dispose()


@pytest.mark.asyncio
async def test_concurrent_execution_control_updates_have_one_winner(tmp_path):
    """Two workers updating one version cannot both pass the safety gate."""

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent-update.db'}"
    first = AsyncDatabase(database_url)
    second = AsyncDatabase(database_url)
    await first.initialize()
    await second.initialize()
    try:
        state = await first.repository.provision_execution_control("live:XAUUSD")
        outcomes = await asyncio.gather(
            first.repository.save_execution_control(
                "live:XAUUSD",
                expected_version=state.version,
                emergency_stop=True,
                actor="first",
            ),
            second.repository.save_execution_control(
                "live:XAUUSD",
                expected_version=state.version,
                emergency_stop=True,
                actor="second",
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(outcome, ExecutionControlState) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, ConcurrencyConflict) for outcome in outcomes) == 1
        current = await first.repository.load_execution_control("live:XAUUSD")
        assert current is not None
        assert current.version == state.version + 1
        assert current.emergency_stop is True
    finally:
        await first.dispose()
        await second.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "actor"),
    [
        ("", None),
        ("   ", None),
        ("x" * 256, None),
        ("live:XAUUSD", ""),
        ("live:XAUUSD", "   "),
        ("live:XAUUSD", "x" * 129),
    ],
)
async def test_execution_control_rejects_invalid_scope_and_actor(
    tmp_path, scope, actor
):
    """Scope and actor bounds are enforced before backend-specific SQL."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        with pytest.raises(ValueError):
            await database.repository.save_execution_control(
                scope, expected_version=0, actor=actor
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_execution_control_model_rejects_version_below_one(tmp_path):
    """The model constraint rejects an impossible persisted version."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    await database.initialize()
    try:
        with pytest.raises(IntegrityError):
            async with database.session_factory() as session:
                async with session.begin():
                    session.add(ExecutionControlState(scope="invalid", version=0))
                    await session.flush()
    finally:
        await database.dispose()


def test_execution_control_bounds_compile_for_sqlite_and_postgresql():
    """Model bounds remain identical across supported SQL dialects."""

    sqlite_ddl = str(
        CreateTable(ExecutionControlState.__table__).compile(dialect=sqlite.dialect())
    )
    postgres_ddl = str(
        CreateTable(ExecutionControlState.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    for expression in (
        "length(trim(scope)) > 0",
        "length(scope) <= 255",
        "length(trim(actor)) > 0",
        "length(actor) <= 128",
        "version >= 1",
    ):
        assert expression in sqlite_ddl
        assert expression in postgres_ddl
