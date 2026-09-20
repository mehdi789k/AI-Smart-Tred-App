import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import pytest

from src.python.data.database import AsyncDatabase
from src.python.data.runtime import CollectorHealth, CollectorLock, health_is_stale


def test_health_writes_an_atomic_readable_document(tmp_path: Path):
    health = CollectorHealth(tmp_path / "health.json", Lock())

    health.update("running", published=3)

    payload = health.read()
    assert payload["status"] == "running"
    assert payload["published"] == 3
    assert payload["pid"] > 0


def test_health_rejects_missing_or_invalid_documents(tmp_path: Path):
    health = CollectorHealth(tmp_path / "health.json", Lock())
    with pytest.raises(RuntimeError):
        health.read()
    health.path.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        health.read()


def test_health_staleness_uses_utc_timestamp():
    now = datetime.now(timezone.utc)
    payload = {"updated_at": (now - timedelta(seconds=10)).isoformat()}

    assert health_is_stale(payload, 5) is True
    assert health_is_stale({"updated_at": now.isoformat()}, 5) is False


def test_health_staleness_validates_timeout_and_timestamp():
    with pytest.raises(ValueError):
        health_is_stale({"updated_at": "invalid"}, 5)
    with pytest.raises(ValueError):
        health_is_stale({"updated_at": datetime.now(timezone.utc).isoformat()}, 0)


def test_windows_lock_can_be_acquired_and_released(tmp_path: Path):
    lock = CollectorLock(tmp_path / "collector.lock")
    lock.acquire()
    lock.release()
    lock.release()


def test_health_status_is_not_stale_when_timestamp_is_recent():
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    assert health_is_stale(payload, 15) is False


@pytest.mark.asyncio
async def test_production_schema_verification_rejects_unmigrated_database(
    tmp_path: Path,
):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.db'}")
    try:
        with pytest.raises(RuntimeError, match="schema verification"):
            await database.verify_schema()
    finally:
        await database.dispose()
