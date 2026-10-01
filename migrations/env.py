"""Alembic environment supporting PostgreSQL and SQLite migrations."""

from __future__ import annotations

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.python.data.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure Alembic for an online migration transaction."""

    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and execute the synchronous Alembic runner."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against the configured async database URL."""

    import asyncio

    url = config.get_main_option("sqlalchemy.url")
    if url.startswith("postgresql+asyncpg://") or url.startswith("sqlite+aiosqlite://"):
        asyncio.run(run_async_migrations())
        return
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            do_run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
