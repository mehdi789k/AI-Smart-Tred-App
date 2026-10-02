from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from src.python.api.app import create_app
from src.python.data.database import AsyncDatabase
from src.python.data.models import OrderStatus, TradeSide, TradingOrder


def _unknown_order() -> TradingOrder:
    return TradingOrder(
        order_id="unknown-order",
        symbol="EURUSD",
        side=TradeSide.BUY,
        quantity=1,
        status=OrderStatus.UNKNOWN,
        created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )


def _seed_database(database: AsyncDatabase, *orders: TradingOrder) -> None:
    async def scenario() -> None:
        await database.initialize()
        async with database.session_factory.begin() as session:
            session.add_all(orders)
        await database.dispose()

    asyncio.run(scenario())


def _make_database(tmp_path) -> AsyncDatabase:
    database_path = (tmp_path / "metrics.db").as_posix()
    return AsyncDatabase(f"sqlite+aiosqlite:///{database_path}")


def test_metrics_scrape_refreshes_database_backed_unknown_order_count(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OBS_CANDLE_MAX_AGE_SECONDS", "")
    monkeypatch.setenv("OBS_METRICS_TOKEN", "")
    database = _make_database(tmp_path)
    try:
        _seed_database(database, _unknown_order())
        with TestClient(create_app(database=database)) as client:
            response = client.get("/metrics")

        assert response.status_code == 200
        assert "unknown_orders_count 1" in response.text
        assert 'observability_source_available{source="order_state"} 1' in response.text
    finally:
        asyncio.run(database.dispose())


def test_metrics_scrape_reports_order_query_failure_without_success_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OBS_CANDLE_MAX_AGE_SECONDS", "")
    monkeypatch.setenv("OBS_METRICS_TOKEN", "")
    database = _make_database(tmp_path)
    _seed_database(database, _unknown_order())

    def fail_order_query(_conn, _cursor, statement, _parameters, _context, _many):
        lowered = statement.lower()
        if "select count(*)" in lowered and "from orders" in lowered:
            raise SQLAlchemyError("injected order query failure")

    event.listen(database.engine.sync_engine, "before_cursor_execute", fail_order_query)
    try:
        with TestClient(create_app(database=database)) as client:
            response = client.get("/metrics")

        assert response.status_code == 200
        assert 'observability_source_available{source="order_state"} 0' in response.text
        assert "unknown_orders_count" not in response.text
        assert "account_exposure" not in response.text
        assert "daily_pnl" not in response.text
    finally:
        event.remove(
            database.engine.sync_engine, "before_cursor_execute", fail_order_query
        )
        asyncio.run(database.dispose())


def test_app_rejects_invalid_candle_age_configuration(monkeypatch):
    monkeypatch.setenv("OBS_CANDLE_MAX_AGE_SECONDS", '{"EURUSD:M5": 0}')
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(ValueError, match="Candle age"):
            create_app(database=database)
    finally:
        import asyncio

        asyncio.run(database.dispose())
