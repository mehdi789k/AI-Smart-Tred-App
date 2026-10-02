from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlalchemy")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from src.python.data.candle_contract import COLLECTOR_VERSION
from src.python.data.database import (
    AsyncDatabase,
    IdempotencyInProgress,
    IdempotencyKeyReuse,
)
from src.python.indicators.candle_validator import validate_candle_versioned


def test_sqlite_repository_upserts_candles_and_ticks_without_duplicates():
    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        candle = {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0,
            "close": 1.5,
            "source": "historical_import",
            "schema_version": 1,
            "ingestion_metadata": {
                "ingestion_id": str(uuid4()),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "collector_version": COLLECTOR_VERSION,
            },
        }
        assert await database.repository.bulk_upsert_candles([candle, candle]) == 1
        candle["close"] = 1.75
        assert await database.repository.bulk_upsert_candles([candle]) == 1
        bars = await database.repository.candles("EURUSD", "M1")
        assert len(bars) == 1
        assert float(bars[0].close) == 1.75
        assert bars[0].timestamp.tzinfo == timezone.utc
        assert bars[0].source == "historical_import"
        persisted_payload = bars[0].to_payload()
        assert persisted_payload["schema_version"] == 1
        assert (
            persisted_payload["ingestion_metadata"]["collector_version"]
            == COLLECTOR_VERSION
        )
        validate_candle_versioned(persisted_payload)

        tick = {
            "symbol": "EURUSD",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "bid": 1.1,
            "ask": 1.2,
        }
        assert await database.repository.bulk_insert_ticks([tick, tick]) == 1
        assert len(await database.repository.ticks("EURUSD")) == 1
        await database.dispose()

    asyncio.run(scenario())


def test_database_rejects_candles_without_explicit_provenance():
    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        await database.initialize()
        candle = {
            "symbol": "EURUSD",
            "timeframe": "M1",
            "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0,
            "close": 1.5,
            "source": "mt5",
        }
        try:
            with pytest.raises(ValueError, match="schema_version"):
                await database.repository.bulk_upsert_candles([candle])
            candle["schema_version"] = 1
            candle["ingestion_metadata"] = {
                "ingestion_id": "row-1",
                "received_at": datetime(2024, 1, 1).isoformat(),
                "collector_version": COLLECTOR_VERSION,
            }
            with pytest.raises(ValueError, match="UTC"):
                await database.repository.bulk_upsert_candles([candle])
        finally:
            await database.dispose()

    asyncio.run(scenario())


def test_timescale_setup_fails_clearly_on_sqlite():
    async def scenario():
        database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
        with pytest.raises(RuntimeError, match="TimescaleDB.*PostgreSQL"):
            await database.repository.ensure_timescale_hypertables()
        await database.dispose()

    asyncio.run(scenario())


def test_idempotency_response_survives_database_restart(tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'idempotency.db'}"
        first_database = AsyncDatabase(database_url)
        await first_database.initialize()
        await first_database.repository.save_idempotency_response(
            "request-1",
            {"accepted": True, "signal_id": "signal-1"},
        )
        assert await first_database.repository.get_idempotency_response(
            "request-1"
        ) == {
            "accepted": True,
            "signal_id": "signal-1",
        }
        await first_database.dispose()

        restarted_database = AsyncDatabase(database_url)
        await restarted_database.initialize()
        assert await restarted_database.repository.get_idempotency_response(
            "request-1"
        ) == {
            "accepted": True,
            "signal_id": "signal-1",
        }
        assert (
            await restarted_database.repository.get_idempotency_response("unknown")
            is None
        )
        await restarted_database.dispose()

    asyncio.run(scenario())


def test_idempotency_claim_is_exclusive_and_rejects_key_reuse(tmp_path):
    async def scenario():
        database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
        await database.initialize()
        repository = database.repository

        assert await repository.claim_idempotency("request-1", "hash-a") is None
        with pytest.raises(IdempotencyInProgress):
            await repository.claim_idempotency("request-1", "hash-a")
        with pytest.raises(IdempotencyKeyReuse):
            await repository.claim_idempotency("request-1", "hash-b")

        await repository.complete_idempotency(
            "request-1", {"accepted": True}, status="completed"
        )
        assert await repository.claim_idempotency("request-1", "hash-a") == {
            "accepted": True
        }
        await database.dispose()

    asyncio.run(scenario())


def test_claim_order_intent_is_atomic_and_idempotent(tmp_path):
    async def scenario():
        database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'atomic-intent.db'}")
        await database.initialize()
        repository = database.repository

        assert (
            await repository.claim_order_intent(
                "request-atomic",
                "hash-atomic",
                "order-atomic",
                {"symbol": "EURUSD", "side": "buy", "quantity": 0.1},
            )
            is None
        )
        with pytest.raises(IdempotencyInProgress):
            await repository.claim_order_intent(
                "request-atomic",
                "hash-atomic",
                "order-atomic",
                {"symbol": "EURUSD", "side": "buy", "quantity": 0.1},
            )

        orders = await repository.get_orders(symbol="EURUSD")
        assert [order.order_id for order in orders] == ["order-atomic"]
        assert await repository.get_idempotency_response("request-atomic") is None
        await database.dispose()

    asyncio.run(scenario())


def test_concurrent_order_intent_claim_has_single_owner(tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent-intent.db'}"
        first_database = AsyncDatabase(database_url)
        second_database = AsyncDatabase(database_url)
        await first_database.initialize()
        await second_database.initialize()

        async def claim(database, order_id):
            try:
                result = await database.repository.claim_order_intent(
                    "request-concurrent",
                    "hash-concurrent",
                    order_id,
                    {"symbol": "EURUSD", "side": "buy", "quantity": 0.1},
                )
                return ("owner", result)
            except IdempotencyInProgress:
                return ("in_progress", None)

        outcomes = await asyncio.gather(
            claim(first_database, "order-concurrent-1"),
            claim(second_database, "order-concurrent-2"),
        )
        assert sorted(outcome[0] for outcome in outcomes) == ["in_progress", "owner"]

        orders = await first_database.repository.get_orders(symbol="EURUSD")
        assert (
            len(
                [
                    order
                    for order in orders
                    if order.idempotency_key == "request-concurrent"
                ]
            )
            == 1
        )
        await first_database.dispose()
        await second_database.dispose()

    asyncio.run(scenario())


def test_unknown_order_reconciliation_is_durable_and_does_not_retry(tmp_path):
    async def scenario():
        database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'orders.db'}")
        await database.initialize()
        repository = database.repository
        await repository.claim_idempotency("request-unknown", "hash-unknown")
        await repository.create_order_intent(
            "order-unknown",
            "request-unknown",
            {
                "symbol": "EURUSD",
                "side": "buy",
                "quantity": 0.1,
                "payload": {"signal_id": "signal-unknown"},
            },
        )
        await repository.transition_order(
            "order-unknown",
            "unknown",
            payload={"reason": "execution_result_unknown"},
        )
        unknown = await repository.get_unknown_orders()
        assert [item.order_id for item in unknown] == ["order-unknown"]

        result = await repository.reconcile_unknown_order(
            "order-unknown",
            "accepted",
            evidence={"broker_ticket": 42},
        )
        assert result["status"] == "accepted"
        assert await repository.get_unknown_orders() == []
        assert await repository.get_idempotency_response("request-unknown") == {
            "accepted": True,
            "mode": "reconciled",
            "order_id": "order-unknown",
            "resolution": "accepted",
        }
        await database.dispose()

    asyncio.run(scenario())


def test_recover_pending_order_intents_marks_crashed_execution_unknown(tmp_path):
    async def scenario():
        database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
        await database.initialize()
        repository = database.repository
        await repository.claim_order_intent(
            "request-crashed",
            "hash-crashed",
            "order-crashed",
            {
                "symbol": "EURUSD",
                "side": "buy",
                "quantity": 0.1,
                "payload": {"signal_id": "signal-crashed"},
            },
        )

        recovered = await repository.recover_pending_order_intents()

        assert recovered == ["order-crashed"]
        assert [item.order_id for item in await repository.get_unknown_orders()] == [
            "order-crashed"
        ]
        assert await repository.get_idempotency_response("request-crashed") == {
            "accepted": False,
            "mode": "unknown",
            "order_id": "order-crashed",
            "reason": "execution_interrupted",
        }
        await database.dispose()

    asyncio.run(scenario())
