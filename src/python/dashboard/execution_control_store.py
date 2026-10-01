"""Synchronous dashboard adapter for the async durable execution-control repository."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from concurrent.futures import Future
from typing import Any, Coroutine

from src.python.data.database import AsyncDatabase

logger = logging.getLogger(__name__)


class DashboardExecutionControlStore:
    """Run repository operations on one stable event loop for dashboard sync calls."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._serve_loop,
            name="dashboard-execution-controls",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("dashboard execution-control event loop did not start")
        atexit.register(self._close_at_exit)

    def _serve_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        self._loop.close()

    def _run(self, operation: Coroutine[Any, Any, Any]) -> Any:
        """Wait for an async repository operation without moving its pool across loops."""
        if self._closed:
            operation.close()
            raise RuntimeError("dashboard execution-control store is closed")
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            operation.close()
            raise RuntimeError(
                "synchronous dashboard store cannot run on its repository event loop"
            )
        future: Future[Any] = asyncio.run_coroutine_threadsafe(operation, self._loop)
        return future.result()

    def load_execution_control(self, scope: str) -> Any | None:
        """Load one durable control scope without creating an implicit default."""
        return self._run(self._database.repository.load_execution_control(scope))

    def save_execution_control(self, scope: str, **values: Any) -> Any:
        """Persist a version-checked update through the shared database repository."""
        return self._run(
            self._database.repository.save_execution_control(scope, **values)
        )

    def close(self) -> None:
        """Dispose the database pool and stop the dedicated repository event loop."""
        with self._close_lock:
            if self._closed:
                return
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._database.dispose(), self._loop
                )
                future.result()
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=10)
                self._closed = True
                atexit.unregister(self._close_at_exit)

    def _close_at_exit(self) -> None:
        try:
            self.close()
        except Exception:
            logger.exception("Failed to close dashboard execution-control store")


def create_dashboard_execution_control_store(
    database_url: str | None = None,
) -> DashboardExecutionControlStore:
    """Create a durable dashboard store; never substitute process-local state."""
    configured_url = os.getenv("DATABASE_URL") if database_url is None else database_url
    if not configured_url or not configured_url.strip():
        raise RuntimeError(
            "DATABASE_URL is required for durable dashboard execution controls"
        )
    return DashboardExecutionControlStore(AsyncDatabase(configured_url.strip()))
