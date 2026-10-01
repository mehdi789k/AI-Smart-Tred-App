from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.python.dashboard import app as dashboard_app
from src.python.dashboard.execution_control_store import (
    DashboardExecutionControlStore,
    create_dashboard_execution_control_store,
)
from src.python.data.database import AsyncDatabase
from src.python.execution.live_order_workflow import (
    DemoActivationConfig,
    LiveOrderConfig,
    LiveOrderRejected,
    LiveOrderWorkflow,
)


class _Connector:
    def is_connected(self) -> bool:
        return True


class _SessionState(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


def _workflow(store: DashboardExecutionControlStore) -> LiveOrderWorkflow:
    return LiveOrderWorkflow(
        _Connector(),
        LiveOrderConfig(
            allowed_symbols=frozenset({"XAUUSD"}),
            demo_activation=DemoActivationConfig(enabled=True),
        ),
        control_store=store,
    )


def test_dashboard_workflows_share_emergency_stop_across_store_instances(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-controls.db'}"
    setup_database = AsyncDatabase(database_url)
    asyncio.run(setup_database.initialize_for_tests())
    asyncio.run(setup_database.dispose())

    first_store = DashboardExecutionControlStore(AsyncDatabase(database_url))
    second_store = DashboardExecutionControlStore(AsyncDatabase(database_url))
    try:
        _workflow(first_store).emergency_stop(reason="operator_stop")

        restarted_workflow = _workflow(second_store)

        assert restarted_workflow.emergency_stop_active is True
        with pytest.raises(LiveOrderRejected, match="emergency stop is active"):
            restarted_workflow._prepare_control_sync()
    finally:
        first_store.close()
        second_store.close()


def test_dashboard_control_store_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_dashboard_execution_control_store()


def test_dashboard_workflow_receives_the_durable_store(monkeypatch):
    store = object()
    config = LiveOrderConfig(allowed_symbols=frozenset({"XAUUSD"}))
    monkeypatch.setattr(dashboard_app, "mt5_connector", _Connector())
    monkeypatch.setattr(
        dashboard_app, "st", SimpleNamespace(session_state=_SessionState())
    )
    monkeypatch.setattr(
        dashboard_app,
        "_get_dashboard_execution_control_store",
        lambda _database_url: store,
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test")
    monkeypatch.setattr(
        dashboard_app.LiveOrderConfig,
        "from_environment",
        staticmethod(lambda: config),
    )

    workflow = dashboard_app.get_live_order_workflow()

    assert workflow is not None
    assert workflow._control_store is store


def test_dashboard_workflow_is_unavailable_without_database_url(monkeypatch):
    monkeypatch.setattr(dashboard_app, "mt5_connector", _Connector())
    monkeypatch.setattr(
        dashboard_app, "st", SimpleNamespace(session_state=_SessionState())
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert dashboard_app.get_live_order_workflow() is None


def test_unavailable_database_fails_closed_without_ephemeral_fallback(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'uninitialized.db'}"
    store = DashboardExecutionControlStore(AsyncDatabase(database_url))
    try:
        workflow = _workflow(store)

        with pytest.raises(LiveOrderRejected, match="durable"):
            workflow._prepare_control_sync()
    finally:
        store.close()
