from __future__ import annotations

import asyncio

from sqlalchemy import text

from src.python.data.database import AsyncDatabase
from src.python.data.schema_verifier import audit_async_database


def test_schema_audit_accepts_initialized_sqlite_schema_without_alembic_table():
    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        try:
            report = await audit_async_database(database, expected_revision=None)
        finally:
            await database.dispose()
        assert report.ok
        assert report.duplicate_rows == 0
        assert report.missing_columns == ()

    asyncio.run(scenario())


def test_schema_audit_reports_missing_migration_contract_columns():
    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE ohlcv_data ("
                    "symbol VARCHAR(64), timeframe VARCHAR(3), timestamp DATETIME, "
                    "open FLOAT, high FLOAT, low FLOAT, close FLOAT)"
                )
            )
        try:
            report = await audit_async_database(database)
        finally:
            await database.dispose()
        assert not report.ok
        assert "source" in report.missing_columns
        assert "ingestion_metadata" in report.missing_columns
        assert not report.natural_key_enforced
        assert any("Alembic revision" in issue for issue in report.issues)

    asyncio.run(scenario())
