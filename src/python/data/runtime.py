"""Runtime safety primitives for the long-running market-data collector."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class CollectorAlreadyRunningError(RuntimeError):
    """Raised when another collector owns the single-instance lock."""


class CollectorLock:
    """Cross-process lock implemented with an exclusively locked Windows file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: Any | None = None

    def acquire(self) -> None:
        """Acquire the lock or fail closed if another process owns it."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="ascii")
        try:
            import msvcrt

            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid():020d}\n")
            handle.flush()
        except (ImportError, OSError) as error:
            handle.close()
            raise CollectorAlreadyRunningError(
                f"collector lock is unavailable: {self.path}"
            ) from error
        self._handle = handle

    def release(self) -> None:
        """Release the lock and close its file handle."""

        if self._handle is None:
            return
        try:
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            logger.exception("Unable to unlock collector lock file %s", self.path)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "CollectorLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(slots=True)
class CollectorHealth:
    """Atomic, watchdog-readable collector state."""

    path: Path
    _mutex: Lock

    def update(self, status: str, **details: Any) -> None:
        """Persist a heartbeat without exposing partially written JSON."""

        payload = {
            "status": status,
            "pid": os.getpid(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        }
        with self._mutex:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

    def read(self) -> dict[str, Any]:
        """Read and validate the current health document."""

        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"collector health is unavailable: {self.path}") from error
        if not isinstance(value, dict) or "updated_at" not in value:
            raise RuntimeError("collector health document is invalid")
        return value


def health_is_stale(payload: dict[str, Any], timeout_seconds: float) -> bool:
    """Return whether a heartbeat is older than the configured watchdog limit."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("collector health has an invalid updated_at") from error
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()
    return age > timeout_seconds


__all__ = [
    "CollectorAlreadyRunningError",
    "CollectorHealth",
    "CollectorLock",
    "health_is_stale",
]
