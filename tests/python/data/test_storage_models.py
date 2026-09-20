"""SQLite contract tests for all persistence categories."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.database import AsyncDatabase
from src.python.data.models import Base, Timeframe


def test_metadata_contains_every_contract_category():
    tables = set(Base.metadata.tables)
    assert {
        "ohlcv_data",
        "market_ticks",
        "account_snapshots",
        "indicator_calculations",
        "filter_evaluations",
        "orders",
        "positions",
        "trade_executions",
        "project_logs",
        "ai_predictions",
        "trading_decisions",
    } <= tables


def test_ohlcv_contract_declares_natural_key_and_provenance_columns():
    table = Base.metadata.tables["ohlcv_data"]
    assert {"source", "ingestion_metadata"} <= set(table.columns.keys())
    assert any(
        constraint.name == "uq_ohlcv_symbol_timeframe_timestamp"
        for constraint in table.constraints
    )


def test_repository_round_trips_derived_and_execution_records():
    async def scenario() -> None:
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        repository = database.repository
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)

        assert await repository.bulk_insert_indicator_calculations(
            [
                {
                    "symbol": "EURUSD",
                    "timeframe": Timeframe.M1,
                    "timestamp": timestamp,
                    "indicator_name": "rsi",
                    "category": "momentum",
                    "value": 55,
                    "values": {"period": 14},
                }
            ]
        ) == 1
        assert await repository.bulk_insert_filter_evaluations(
            [
                {
                    "symbol": "EURUSD",
                    "timeframe": "M1",
                    "timestamp": timestamp,
                    "filter_name": "trend",
                    "category": "trend",
                    "passed": True,
                    "signal": "buy",
                }
            ]
        ) == 1
        assert await repository.insert_order(
            {
                "order_id": "order-1",
                "symbol": "EURUSD",
                "side": "buy",
                "quantity": 1,
            }
        ) == 1
        assert await repository.bulk_insert_trade_executions(
            [
                {
                    "execution_id": "fill-1",
                    "order_id": "order-1",
                    "symbol": "EURUSD",
                    "timestamp": timestamp,
                    "side": "buy",
                    "quantity": 1,
                    "price": 1.1,
                }
            ]
        ) == 1
        assert len(await repository.get_indicator_calculations("EURUSD", category="momentum")) == 1
        assert len(await repository.get_filter_evaluations("EURUSD", category="trend")) == 1
        assert len(await repository.get_orders(symbol="EURUSD")) == 1
        assert len(await repository.get_trade_executions("EURUSD")) == 1
        await database.dispose()

    asyncio.run(scenario())


def test_repository_records_order_and_fill_atomically_and_idempotently():
    async def scenario() -> None:
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        repository = database.repository
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        order = {
            "order_id": "order-atomic-1",
            "symbol": "EURUSD",
            "side": "buy",
            "quantity": 1,
            "status": "filled",
        }
        execution = {
            "execution_id": "fill-atomic-1",
            "order_id": "order-atomic-1",
            "symbol": "EURUSD",
            "timestamp": timestamp,
            "side": "buy",
            "quantity": 1,
            "price": 1.1,
        }

        await repository.record_order_execution(order, execution)
        await repository.record_order_execution(order, execution)

        orders = await repository.get_orders(symbol="EURUSD")
        executions = await repository.get_trade_executions("EURUSD")
        assert len([item for item in orders if item.order_id == "order-atomic-1"]) == 1
        assert len([item for item in executions if item.execution_id == "fill-atomic-1"]) == 1
        await database.dispose()

    asyncio.run(scenario())
