"""Shared test isolation for application infrastructure."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_application_database(tmp_path, monkeypatch):
    """Give each test an isolated SQLite database instead of production PostgreSQL."""

    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}",
    )
