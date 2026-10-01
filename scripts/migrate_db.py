"""Run Alembic migrations and record the deployed model version."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from src.python.data.database import AsyncDatabase  # noqa: E402
from src.python.data.versioning import record_system_versions  # noqa: E402


def migration_config() -> Config:
    """Build Alembic configuration from the repository root."""

    config = Config(str(ROOT / "alembic.ini"))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to run migrations")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def record_versions() -> None:
    """Record schema/model metadata after Alembic reaches head."""

    database = AsyncDatabase(os.environ["DATABASE_URL"])
    try:
        await record_system_versions(database.repository)
    finally:
        await database.dispose()


async def verify_schema() -> None:
    """Verify the migration head and execution-control schema."""

    database = AsyncDatabase(os.environ["DATABASE_URL"])
    try:
        await database.verify_schema()
    finally:
        await database.dispose()


def main() -> None:
    """Upgrade the configured database and persist version metadata."""

    command.upgrade(migration_config(), "head")
    asyncio.run(verify_schema())
    asyncio.run(record_versions())


if __name__ == "__main__":
    main()
