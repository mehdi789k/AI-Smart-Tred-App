"""Integration tests for the versioned local migration chain."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql, sqlite

from src.python.data.database import AsyncDatabase
from src.python.data.models import ExecutionControlState, SystemVersion
from src.python.data.versioning import SCHEMA_VERSION


def _alembic_config(database_path: Path) -> Config:
    """Build an Alembic config that points at an isolated SQLite database."""

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_migration_chain_upgrades_from_empty_database(
    tmp_path: Path, monkeypatch
) -> None:
    """All revisions apply in order and expose the current schema version."""

    database_path = tmp_path / "migration.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        components = {
            row[0]
            for row in connection.execute(
                "SELECT component FROM system_versions"
            ).fetchall()
        }

    assert version == ("0010",)
    assert components == set()
    assert SCHEMA_VERSION == "0010"


def test_migration_chain_upgrades_previous_revision(
    tmp_path: Path, monkeypatch
) -> None:
    """A database at the previous revision can be upgraded safely to head."""

    database_path = tmp_path / "repeat.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0001")
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]

    assert table_count >= 14


@pytest.mark.asyncio
async def test_database_initialization_records_schema_and_model_versions(
    tmp_path: Path, monkeypatch
) -> None:
    """Application initialization persists both deployment version records."""

    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}")
    monkeypatch.setenv("MODEL_VERSION", "model-test-1")
    try:
        await database.initialize()
        async with database.session_factory() as session:
            rows = (await session.execute(select(SystemVersion))).scalars().all()
    finally:
        await database.dispose()

    assert {row.component: row.version for row in rows} == {
        "schema": SCHEMA_VERSION,
        "model": "model-test-1",
    }


def test_missing_execution_control_table_is_created_and_reversible(
    tmp_path: Path, monkeypatch
) -> None:
    """Revision 0009 creates a missing table and can recreate it after downgrade."""

    database_path = tmp_path / "missing-control.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = connection.execute(
            "PRAGMA table_info(execution_control_state)"
        ).fetchall()
        checks = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'execution_control_state'"
        ).fetchone()[0]
    assert [column[1] for column in columns] == [
        "scope",
        "emergency_stop",
        "emergency_stop_reason",
        "demo_active",
        "session_expires_at",
        "demo_trade_count",
        "daily_loss",
        "version",
        "actor",
        "updated_at",
        "demo_owner_approval",
        "demo_second_approval",
        "demo_selected_symbols",
        "demo_limits",
        "demo_configuration_hash",
    ]
    assert columns[0][3] == 1
    assert columns[1][4] == "FALSE"
    assert columns[6][4] == "0"
    assert columns[7][4] == "1"
    assert "version >= 1" in checks

    command.downgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='execution_control_state'"
            ).fetchone()
            is None
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='execution_control_state'"
        ).fetchone() == ("execution_control_state",)


def test_migration_rejects_mismatched_preexisting_execution_control_schema(
    tmp_path: Path, monkeypatch
) -> None:
    """A same-named but incompatible table fails loudly instead of being trusted."""

    database_path = tmp_path / "mismatch-control.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE IF EXISTS execution_control_state")
        connection.execute(
            "CREATE TABLE execution_control_state ("
            "scope VARCHAR(255) PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0)"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="execution_control_state schema mismatch"):
        command.upgrade(config, "head")


def test_downgrade_preserves_valid_preexisting_execution_control_table(
    tmp_path: Path, monkeypatch
) -> None:
    """Downgrade never deletes a valid table that predates revision 0009."""

    database_path = tmp_path / "preexisting-control.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        ExecutionControlState.__table__.create(engine)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    command.downgrade(config, "0008")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='execution_control_state'"
        ).fetchone() == ("execution_control_state",)


def test_execution_control_datetime_validator_is_dialect_aware():
    """PostgreSQL reflection requires timezone-aware DateTime; SQLite stays portable."""

    migration = importlib.import_module(
        "migrations.versions.0009_execution_control_state"
    )

    assert migration._column_type_matches(
        postgresql.TIMESTAMP(timezone=True), "datetime", dialect_name="postgresql"
    )
    assert not migration._column_type_matches(
        postgresql.TIMESTAMP(timezone=False), "datetime", dialect_name="postgresql"
    )
    assert migration._column_type_matches(
        sqlite.DATETIME(), "datetime", dialect_name="sqlite"
    )


def test_fresh_migration_lifecycle_tracks_0009_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    """Fresh head downgrade removes only tables owned by revision 0009."""

    database_path = tmp_path / "fresh-lifecycle.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        tables_at_head = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "execution_control_state" in tables_at_head
    assert "_migration_0009_execution_control_state" in tables_at_head
    assert "order_transitions" in tables_at_head

    command.downgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        tables_after_downgrade = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "execution_control_state" not in tables_after_downgrade
    assert "_migration_0009_execution_control_state" not in tables_after_downgrade
    assert "order_transitions" in tables_after_downgrade


def test_preexisting_marker_is_validated_and_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid marker table that predates 0009 is never dropped on downgrade."""

    database_path = tmp_path / "preexisting-marker.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE _migration_0009_execution_control_state ("
            "revision VARCHAR(32) PRIMARY KEY, "
            "owns_table BOOLEAN NOT NULL, "
            "owns_marker BOOLEAN NOT NULL)"
        )
        connection.commit()

    command.upgrade(config, "head")
    command.downgrade(config, "0008")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='_migration_0009_execution_control_state'"
        ).fetchone() == ("_migration_0009_execution_control_state",)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='execution_control_state'"
            ).fetchone()
            is None
        )


def test_preexisting_marker_ownership_ambiguity_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """A preexisting current-revision marker row cannot authorize destructive cleanup."""

    database_path = tmp_path / "ambiguous-marker.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE _migration_0009_execution_control_state ("
            "revision VARCHAR(32) PRIMARY KEY, "
            "owns_table BOOLEAN NOT NULL, "
            "owns_marker BOOLEAN NOT NULL)"
        )
        connection.execute(
            "INSERT INTO _migration_0009_execution_control_state "
            "(revision, owns_table, owns_marker) VALUES ('0009', 1, 0)"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="marker ownership cannot be proven"):
        command.upgrade(config, "head")


def test_preexisting_marker_schema_mismatch_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """A preexisting marker with an unexpected schema is rejected before use."""

    database_path = tmp_path / "mismatched-marker.db"
    config = _alembic_config(database_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    command.upgrade(config, "0008")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE _migration_0009_execution_control_state "
            "(revision VARCHAR(32) PRIMARY KEY)"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="marker schema mismatch"):
        command.upgrade(config, "head")
