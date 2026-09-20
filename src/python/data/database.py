"""Async SQLAlchemy repository for PostgreSQL/TimescaleDB market data."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, overload

try:
    from sqlalchemy import and_, select, text
    from sqlalchemy import insert as sa_insert
    from sqlalchemy import update as sa_update
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.sql import Insert

    from .models import (
        SQLALCHEMY_AVAILABLE,
        AccountSnapshot,
        AIPrediction,
        Base,
        ExecutionControlState,
        FilterEvaluation,
        IdempotencyRecord,
        IndicatorCalculation,
        MarketTick,
        OHLCVBar,
        OrderTransition,
        Position,
        PositionTransition,
        ProjectLog,
        Symbol,
        TradeExecution,
        TradingDecision,
        TradingOrder,
    )
except ImportError as error:  # pragma: no cover - minimal installation
    SQLALCHEMY_AVAILABLE = False
    _SQLALCHEMY_IMPORT_ERROR = error

from .models import utc_now
from .state_machine import validate_order_transition, validate_position_transition

logger = logging.getLogger(__name__)
_UNSET = object()
TIMESCALE_HYPERTABLES = ("ohlcv_data", "market_ticks", "account_snapshots")


def normalize_database_url(url: str) -> str:
    """Normalize a sync-style URL to an async SQLAlchemy dialect URL."""

    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("postgresql+psycopg2://") or url.startswith(
        "postgresql+psycopg://"
    ):
        return "postgresql+asyncpg://" + url.split("://", 1)[1]
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return "sqlite+aiosqlite://" + url.removeprefix("sqlite://")
    return url


def ensure_aware_utc(value: datetime | int | float | str) -> datetime:
    """Normalize epoch values and naive datetimes to timezone-aware UTC."""

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _execution_scope(scope: str) -> str:
    """Validate and normalize the stable execution-control scope key."""

    if not isinstance(scope, str):
        raise ValueError("execution control scope must be a string")
    normalized = scope.strip()
    if not normalized:
        raise ValueError("execution control scope must not be blank")
    if len(normalized) > 255:
        raise ValueError("execution control scope must be at most 255 characters")
    return normalized


def _execution_actor(actor: str) -> str:
    """Validate and normalize an execution-control audit actor."""

    if not isinstance(actor, str):
        raise ValueError("execution control actor must be a string")
    normalized = actor.strip()
    if not normalized:
        raise ValueError("execution control actor must not be blank")
    if len(normalized) > 128:
        raise ValueError("execution control actor must be at most 128 characters")
    return normalized


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    return float(value)


def _row_timestamp(row: Mapping[str, Any]) -> datetime:
    for key in ("timestamp", "time", "time_msc"):
        if key in row and row[key] is not None:
            value = row[key]
            if (
                key == "time_msc"
                and isinstance(value, (int, float))
                and value > 10_000_000_000
            ):
                value = value / 1000
            return ensure_aware_utc(value)
    raise ValueError("market-data row is missing timestamp/time")


if SQLALCHEMY_AVAILABLE:

    class ConcurrencyConflict(RuntimeError):
        """Raised when a version-checked execution-control write is stale."""

    class IdempotencyInProgress(RuntimeError):
        """Raised when another request currently owns an idempotency key."""

    class IdempotencyKeyReuse(RuntimeError):
        """Raised when a key is reused with a different request payload."""

    class UnknownOrderResolutionError(RuntimeError):
        """Raised when an unknown order cannot be safely reconciled."""

    class DatabaseRepository:
        """Repository with one explicit transaction per public write operation."""

        def __init__(
            self,
            session_factory: async_sessionmaker[AsyncSession],
            engine: AsyncEngine | None = None,
        ) -> None:
            self.session_factory = session_factory
            self.engine = engine

        async def create_schema(self) -> None:
            """Create tables and indexes; migrations remain deployment-owned."""

            if self.engine is None:
                raise RuntimeError("an async engine is required to create the schema")
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await _ensure_derived_data_columns(connection)

        async def load_execution_control(
            self, scope: str
        ) -> ExecutionControlState | None:
            """Load an execution-control row without provisioning a missing scope."""

            scope = _execution_scope(scope)
            async with self.session_factory() as session:
                return await session.get(ExecutionControlState, scope)

        async def provision_execution_control(
            self, scope: str
        ) -> ExecutionControlState:
            """Provision fail-closed defaults for a scope when explicitly requested."""

            scope = _execution_scope(scope)
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        state = await session.get(ExecutionControlState, scope)
                        if state is None:
                            state = ExecutionControlState(scope=scope)
                            session.add(state)
                            await session.flush()
                        return state
            except IntegrityError:
                state = await self.load_execution_control(scope)
                if state is None:
                    raise
                return state

        @overload
        async def get_execution_control(
            self, scope: str, *, provision: Literal[False] = False
        ) -> ExecutionControlState | None: ...

        @overload
        async def get_execution_control(
            self, scope: str, *, provision: Literal[True]
        ) -> ExecutionControlState: ...

        async def get_execution_control(
            self, scope: str, *, provision: bool = False
        ) -> ExecutionControlState | None:
            """Load durable controls without implicitly provisioning missing state.

            Provisioning is intentionally explicit through
            :meth:`provision_execution_control`; the optional ``provision=True``
            compatibility path is never the default.
            """

            if provision:
                return await self.provision_execution_control(scope)
            return await self.load_execution_control(scope)

        async def save_execution_control(
            self,
            scope: str,
            *,
            expected_version: int | None = None,
            emergency_stop: bool | None = None,
            emergency_stop_reason: str | None | object = _UNSET,
            demo_active: bool | None = None,
            session_expires_at: datetime | None | object = _UNSET,
            demo_owner_approval: str | None | object = _UNSET,
            demo_second_approval: str | None | object = _UNSET,
            demo_selected_symbols: list[str] | None | object = _UNSET,
            demo_limits: dict[str, Any] | None | object = _UNSET,
            demo_configuration_hash: str | None | object = _UNSET,
            demo_trade_count: int | None = None,
            daily_loss: float | None = None,
            actor: str | None = None,
        ) -> ExecutionControlState:
            """Create or atomically update execution controls by expected version."""

            scope = _execution_scope(scope)
            if expected_version is not None and expected_version < 0:
                raise ValueError("expected_version must be non-negative")
            if demo_trade_count is not None and demo_trade_count < 0:
                raise ValueError("demo_trade_count must be non-negative")
            if daily_loss is not None and (
                daily_loss < 0 or not math.isfinite(float(daily_loss))
            ):
                raise ValueError("daily_loss must be finite and non-negative")
            normalized_actor = _execution_actor(actor) if actor is not None else None

            values: dict[str, Any] = {}
            if emergency_stop is not None:
                values["emergency_stop"] = emergency_stop
            if emergency_stop_reason is not _UNSET:
                values["emergency_stop_reason"] = emergency_stop_reason
            if demo_active is not None:
                values["demo_active"] = demo_active
            if session_expires_at is not _UNSET:
                values["session_expires_at"] = (
                    None
                    if session_expires_at is None
                    else ensure_aware_utc(
                        session_expires_at
                        if isinstance(
                            session_expires_at, (datetime, int, float, str)
                        )
                        else str(session_expires_at)
                    )
                )
            if demo_owner_approval is not _UNSET:
                values["demo_owner_approval"] = demo_owner_approval
            if demo_second_approval is not _UNSET:
                values["demo_second_approval"] = demo_second_approval
            if demo_selected_symbols is not _UNSET:
                values["demo_selected_symbols"] = demo_selected_symbols
            if demo_limits is not _UNSET:
                values["demo_limits"] = demo_limits
            if demo_configuration_hash is not _UNSET:
                values["demo_configuration_hash"] = demo_configuration_hash
            if demo_trade_count is not None:
                values["demo_trade_count"] = demo_trade_count
            if daily_loss is not None:
                values["daily_loss"] = daily_loss
            if normalized_actor is not None:
                values["actor"] = normalized_actor

            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        state = await session.get(ExecutionControlState, scope)
                        if state is None:
                            if expected_version not in (None, 0):
                                raise ConcurrencyConflict(
                                    f"execution control {scope!r} does not exist"
                                )
                            state = ExecutionControlState(scope=scope, **values)
                            session.add(state)
                            await session.flush()
                            return state

                        if expected_version is None:
                            raise ValueError(
                                "expected_version is required when updating execution control"
                            )
                        if state.version != expected_version:
                            raise ConcurrencyConflict(
                                f"execution control {scope!r} has version "
                                f"{state.version}, expected {expected_version}"
                            )

                        update_values = {
                            **values,
                            "version": expected_version + 1,
                            "updated_at": utc_now(),
                        }
                        result = await session.execute(
                            sa_update(ExecutionControlState)
                            .where(
                                ExecutionControlState.scope == scope,
                                ExecutionControlState.version == expected_version,
                            )
                            .values(**update_values)
                        )
                        if result.rowcount != 1:
                            raise ConcurrencyConflict(
                                f"execution control {scope!r} changed during update"
                            )
                        await session.flush()
                        return await session.get(ExecutionControlState, scope)
            except IntegrityError as error:
                if expected_version == 0:
                    raise ConcurrencyConflict(
                        f"execution control {scope!r} was created concurrently"
                    ) from error
                raise

        async def get_idempotency_response(self, key: str) -> dict[str, Any] | None:
            """Return a completed idempotent response, if one exists."""

            async with self.session_factory() as session:
                record = await session.get(IdempotencyRecord, key)
                if record is None or record.status == "in_progress":
                    return None
                return record.response

        async def claim_idempotency(
            self, key: str, request_hash: str
        ) -> dict[str, Any] | None:
            """Atomically claim a key or return its completed response.

            The primary-key insert is the concurrency boundary.  A request that
            loses the race is never allowed to call an external execution
            adapter while the first request is still in progress.
            """

            if not key or not request_hash:
                raise ValueError("idempotency key and request hash are required")
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        session.add(
                            IdempotencyRecord(
                                key=key,
                                request_hash=request_hash,
                                status="in_progress",
                            )
                        )
                return None
            except IntegrityError:
                async with self.session_factory() as session:
                    record = await session.get(IdempotencyRecord, key)
                if record is None:
                    raise RuntimeError("idempotency claim disappeared") from None
                if record.request_hash and record.request_hash != request_hash:
                    raise IdempotencyKeyReuse(
                        "idempotency key was already used for a different request"
                    )
                if record.status == "in_progress":
                    raise IdempotencyInProgress(
                        "a request with this idempotency key is still in progress"
                    )
                return record.response

        async def claim_order_intent(
            self,
            key: str,
            request_hash: str,
            order_id: str,
            order: Mapping[str, Any],
        ) -> dict[str, Any] | None:
            """Atomically claim idempotency and persist its pending order intent."""

            if not key or not request_hash or not order_id:
                raise ValueError(
                    "idempotency key, request hash and order_id are required"
                )
            normalized = _normalize_domain_row(
                TradingOrder,
                {
                    **dict(order),
                    "order_id": order_id,
                    "idempotency_key": key,
                    "status": "pending",
                },
            )
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        session.add(
                            IdempotencyRecord(
                                key=key,
                                request_hash=request_hash,
                                status="in_progress",
                            )
                        )
                        session.add(TradingOrder(**normalized))
                        session.add(
                            OrderTransition(
                                order_id=order_id,
                                from_status=None,
                                to_status="pending",
                                actor="order_intent",
                                correlation_id=str(
                                    normalized.get("correlation_id") or key
                                ),
                                payload=dict(normalized.get("payload") or {}),
                            )
                        )
                return None
            except IntegrityError:
                record = None
                existing_order = None
                for attempt in range(20):
                    async with self.session_factory() as session:
                        record = await session.get(IdempotencyRecord, key)
                        existing_order = await session.scalar(
                            select(TradingOrder).where(
                                TradingOrder.idempotency_key == key
                            )
                        )
                    if record is not None and existing_order is not None:
                        break
                    if attempt < 19:
                        await asyncio.sleep(0.005)
                if record is None:
                    raise RuntimeError("idempotency claim disappeared") from None
                if record.request_hash and record.request_hash != request_hash:
                    raise IdempotencyKeyReuse(
                        "idempotency key was already used for a different request"
                    )
                if existing_order is None or existing_order.idempotency_key != key:
                    raise RuntimeError("idempotency claim has no matching order intent")
                if record.status == "in_progress":
                    raise IdempotencyInProgress(
                        "a request with this idempotency key is still in progress"
                    )
                return record.response

        async def complete_idempotency(
            self,
            key: str,
            response: dict[str, Any],
            *,
            status: str = "completed",
        ) -> None:
            """Persist the terminal response for a previously claimed key."""

            if status not in {"completed", "rejected", "unknown"}:
                raise ValueError("invalid idempotency terminal status")
            async with self.session_factory() as session:
                async with session.begin():
                    record = await session.get(IdempotencyRecord, key)
                    if record is None:
                        raise RuntimeError("idempotency key was not claimed")
                    record.response = response
                    record.status = status
                    record.completed_at = utc_now()

        async def release_idempotency(self, key: str) -> None:
            """Release a claim when validation rejected before external I/O."""

            async with self.session_factory() as session:
                async with session.begin():
                    record = await session.get(IdempotencyRecord, key)
                    if record is not None and record.status == "in_progress":
                        await session.delete(record)

        async def create_order_intent(
            self,
            order_id: str,
            idempotency_key: str,
            order: Mapping[str, Any],
        ) -> None:
            """Persist a pending order intent linked to its idempotency claim."""

            if not order_id or not idempotency_key:
                raise ValueError("order_id and idempotency_key are required")
            normalized = _normalize_domain_row(
                TradingOrder,
                {
                    **dict(order),
                    "order_id": order_id,
                    "idempotency_key": idempotency_key,
                    "status": "pending",
                },
            )
            async with self.session_factory() as session:
                async with session.begin():
                    existing = await session.get(TradingOrder, order_id)
                    if existing is None:
                        session.add(TradingOrder(**normalized))
                        session.add(
                            OrderTransition(
                                order_id=order_id,
                                from_status=None,
                                to_status="pending",
                                actor="order_intent",
                                correlation_id=str(
                                    normalized.get("correlation_id") or idempotency_key
                                ),
                                payload=dict(normalized.get("payload") or {}),
                            )
                        )
                    elif existing.idempotency_key != idempotency_key:
                        raise IdempotencyKeyReuse(
                            "order id is already linked to another idempotency key"
                        )

        async def transition_order(
            self,
            order_id: str,
            status: str,
            *,
            payload: Mapping[str, Any] | None = None,
            actor: str = "system",
            correlation_id: str | None = None,
            timestamp: datetime | None = None,
            broker_order_id: str | None = None,
            broker_deal_id: str | None = None,
        ) -> None:
            """Move an order through its durable lifecycle."""

            if not actor.strip():
                raise ValueError("actor must not be blank")
            async with self.session_factory() as session:
                async with session.begin():
                    order = await session.get(TradingOrder, order_id)
                    if order is None:
                        raise UnknownOrderResolutionError("order intent does not exist")
                    current, target = validate_order_transition(order.status, status)
                    event_time = ensure_aware_utc(timestamp or utc_now())
                    order.status = target
                    order.updated_at = event_time
                    if broker_order_id is not None:
                        order.broker_order_id = broker_order_id
                    if broker_deal_id is not None:
                        order.broker_deal_id = broker_deal_id
                    if payload:
                        order.payload = {**(order.payload or {}), **dict(payload)}
                    session.add(
                        OrderTransition(
                            order_id=order_id,
                            from_status=current,
                            to_status=target,
                            actor=actor,
                            timestamp=event_time,
                            correlation_id=correlation_id or order_id,
                            payload=dict(payload or {}),
                        )
                    )

        async def transition_position(
            self,
            position_id: str,
            status: str,
            *,
            payload: Mapping[str, Any] | None = None,
            actor: str = "system",
            correlation_id: str | None = None,
            timestamp: datetime | None = None,
        ) -> None:
            """Move a position through its durable lifecycle and audit it."""

            if not actor.strip():
                raise ValueError("actor must not be blank")
            async with self.session_factory() as session:
                async with session.begin():
                    position = await session.get(Position, position_id)
                    if position is None:
                        raise ValueError("position does not exist")
                    current, target = validate_position_transition(
                        position.status, status
                    )
                    event_time = ensure_aware_utc(timestamp or utc_now())
                    position.status = target
                    position.updated_at = event_time
                    if target == "closed":
                        position.closed_at = event_time
                    if payload:
                        position.payload = {**(position.payload or {}), **dict(payload)}
                    session.add(
                        PositionTransition(
                            position_id=position_id,
                            from_status=current,
                            to_status=target,
                            actor=actor,
                            timestamp=event_time,
                            correlation_id=correlation_id or position_id,
                            payload=dict(payload or {}),
                        )
                    )

        async def get_unknown_orders(self, *, limit: int = 100) -> list[Any]:
            """Return unresolved orders requiring operator reconciliation."""

            if limit < 1 or limit > 1_000:
                raise ValueError("limit must be between 1 and 1000")
            return await self._query(
                TradingOrder,
                {"status": "unknown"},
                TradingOrder.updated_at,
                limit,
            )

        async def recover_pending_order_intents(
            self, *, limit: int = 1000
        ) -> list[str]:
            """Fail closed for intents left pending by an interrupted execution."""

            if limit < 1 or limit > 10_000:
                raise ValueError("limit must be between 1 and 10000")
            recovered: list[str] = []
            async with self.session_factory() as session:
                async with session.begin():
                    orders = list(
                        (
                            await session.scalars(
                                select(TradingOrder)
                                .where(TradingOrder.status == "pending")
                                .order_by(TradingOrder.updated_at)
                                .limit(limit)
                            )
                        ).all()
                    )
                    event_time = utc_now()
                    for order in orders:
                        current, target = validate_order_transition(
                            order.status, "unknown"
                        )
                        order.status = target
                        order.updated_at = event_time
                        order.payload = {
                            **(order.payload or {}),
                            "reason": "execution_interrupted",
                        }
                        session.add(
                            OrderTransition(
                                order_id=order.order_id,
                                from_status=current,
                                to_status=target,
                                actor="startup_recovery",
                                timestamp=event_time,
                                correlation_id=order.idempotency_key or order.order_id,
                                payload={"reason": "execution_interrupted"},
                            )
                        )
                        if order.idempotency_key:
                            record = await session.get(
                                IdempotencyRecord, order.idempotency_key
                            )
                            if record is not None and record.status == "in_progress":
                                record.status = "unknown"
                                record.response = {
                                    "accepted": False,
                                    "mode": "unknown",
                                    "order_id": order.order_id,
                                    "reason": "execution_interrupted",
                                }
                                record.completed_at = event_time
                        recovered.append(order.order_id)
            return recovered

        async def reconcile_unknown_order(
            self,
            order_id: str,
            resolution: str,
            *,
            evidence: Mapping[str, Any] | None = None,
            broker_order_id: str | None = None,
            broker_deal_id: str | None = None,
        ) -> dict[str, Any]:
            """Resolve an unknown order without issuing a new broker request."""

            if resolution not in {"accepted", "rejected"}:
                raise ValueError("resolution must be accepted or rejected")
            async with self.session_factory() as session:
                async with session.begin():
                    order = await session.get(TradingOrder, order_id)
                    if order is None or str(order.status) not in {
                        "unknown",
                        "OrderStatus.UNKNOWN",
                    }:
                        raise UnknownOrderResolutionError(
                            "only an existing unknown order can be reconciled"
                        )
                    current, target = validate_order_transition(
                        order.status, resolution
                    )
                    event_time = utc_now()
                    order.status = target
                    order.updated_at = event_time
                    if broker_order_id:
                        order.broker_order_id = broker_order_id
                    if broker_deal_id:
                        order.broker_deal_id = broker_deal_id
                    order.payload = {
                        **(order.payload or {}),
                        "reconciliation": {
                            "resolution": resolution,
                            "evidence": dict(evidence or {}),
                            "reconciled_at": event_time.isoformat(),
                        },
                    }
                    session.add(
                        OrderTransition(
                            order_id=order_id,
                            from_status=current,
                            to_status=target,
                            actor="broker_reconciliation",
                            timestamp=event_time,
                            correlation_id=order_id,
                            payload={
                                "evidence": dict(evidence or {}),
                                "broker_order_id": broker_order_id,
                                "broker_deal_id": broker_deal_id,
                            },
                        )
                    )
                    if order.idempotency_key:
                        idem = await session.get(
                            IdempotencyRecord, order.idempotency_key
                        )
                        if idem is not None and idem.status in {
                            "in_progress",
                            "unknown",
                        }:
                            idem.status = "completed"
                            idem.response = {
                                "accepted": resolution == "accepted",
                                "mode": "reconciled",
                                "order_id": order_id,
                                "resolution": resolution,
                            }
                            idem.completed_at = utc_now()
                    return {
                        "order_id": order_id,
                        "status": resolution,
                        "idempotency_key": order.idempotency_key,
                    }

        async def upsert_system_version(self, component: str, version: str) -> None:
            """Record a deployment component version transactionally."""

            from .models import SystemVersion

            if not component.strip() or not version.strip():
                raise ValueError("component and version must not be blank")
            async with self.session_factory() as session:
                async with session.begin():
                    record = await session.get(SystemVersion, component)
                    if record is None:
                        session.add(SystemVersion(component=component, version=version))
                    else:
                        record.version = version
                        record.updated_at = utc_now()

        async def save_idempotency_response(
            self, key: str, response: dict[str, Any]
        ) -> None:
            """Persist a response, retaining compatibility with older callers."""

            async with self.session_factory() as session:
                async with session.begin():
                    record = await session.get(IdempotencyRecord, key)
                    if record is None:
                        record = IdempotencyRecord(
                            key=key,
                            status="completed",
                            response=response,
                            completed_at=utc_now(),
                        )
                        session.add(record)
                    else:
                        record.response = response
                        record.status = "completed"
                        record.completed_at = utc_now()

        async def ensure_timescale_hypertables(self) -> None:
            """Create Timescale hypertables idempotently on PostgreSQL only."""

            if self.engine is None:
                raise RuntimeError("an async engine is required for TimescaleDB setup")
            await ensure_timescale_hypertables(self.engine)

        async def upsert_symbols(self, names: Iterable[str]) -> None:
            """Insert symbols that are not present, without deleting metadata."""

            values = [
                {"name": str(name), "is_active": True}
                for name in set(names)
                if str(name)
            ]
            if not values:
                return
            async with self.session_factory() as session:
                async with session.begin():
                    statement = self._upsert_statement(
                        Symbol, values, ["name"], ["name"]
                    )
                    await session.execute(statement)

        async def bulk_upsert_candles(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert OHLCV records and return the number accepted."""

            normalized = [_normalize_candle(row) for row in rows]
            if not normalized:
                return 0
            normalized = _deduplicate(normalized, ("symbol", "timeframe", "timestamp"))
            total = 0
            async with self.session_factory() as session:
                async with session.begin():
                    # Keep symbol registration and candle writes in the same
                    # transaction.  A failed page must not leave an orphan
                    # symbol committed ahead of its market data.
                    symbols = [
                        {"name": symbol, "is_active": True}
                        for symbol in {row["symbol"] for row in normalized}
                    ]
                    await session.execute(
                        self._upsert_statement(Symbol, symbols, ["name"], ["name"])
                    )
                    for batch in _batches(normalized, batch_size):
                        statement = self._upsert_statement(
                            OHLCVBar,
                            batch,
                            ["symbol", "timeframe", "timestamp"],
                            [
                                "open",
                                "high",
                                "low",
                                "close",
                                "tick_volume",
                                "volume",
                                "spread",
                                "status",
                                "payload",
                                "source",
                                "ingestion_metadata",
                            ],
                        )
                        await session.execute(statement)
                        total += len(batch)
            return total

        async def bulk_insert_ticks(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk insert ticks idempotently using the natural observation key."""

            normalized = [_normalize_tick(row) for row in rows]
            if not normalized:
                return 0
            normalized = _deduplicate(normalized, ("symbol", "timestamp", "bid", "ask"))
            total = 0
            async with self.session_factory() as session:
                async with session.begin():
                    symbols = [
                        {"name": symbol, "is_active": True}
                        for symbol in {row["symbol"] for row in normalized}
                    ]
                    await session.execute(
                        self._upsert_statement(Symbol, symbols, ["name"], ["name"])
                    )
                    for batch in _batches(normalized, batch_size):
                        statement = self._upsert_statement(
                            MarketTick,
                            batch,
                            ["symbol", "timestamp", "bid", "ask"],
                            ["last", "volume", "status", "payload"],
                        )
                        await session.execute(statement)
                        total += len(batch)
            return total

        async def bulk_insert_ohlcv(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Compatibility name for callers that prefer insert terminology."""

            return await self.bulk_upsert_candles(rows, batch_size=batch_size)

        async def candles(
            self,
            symbol: str,
            timeframe: str,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[OHLCVBar]:
            """Read bars in chronological order using indexed predicates."""

            async with self.session_factory() as session:
                conditions = [
                    OHLCVBar.symbol == symbol,
                    OHLCVBar.timeframe == timeframe,
                ]
                if start is not None:
                    conditions.append(OHLCVBar.timestamp >= ensure_aware_utc(start))
                if end is not None:
                    conditions.append(OHLCVBar.timestamp < ensure_aware_utc(end))
                statement = (
                    select(OHLCVBar)
                    .where(and_(*conditions))
                    .order_by(OHLCVBar.timestamp)
                )
                if limit is not None:
                    statement = statement.limit(limit)
                result = await session.execute(statement)
                return list(result.scalars())

        async def get_candles(
            self,
            symbol: str,
            timeframe: str,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[OHLCVBar]:
            """Compatibility name for the indexed candle query."""

            return await self.candles(symbol, timeframe, start, end, limit=limit)

        async def ticks(
            self,
            symbol: str,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[MarketTick]:
            """Read ticks in chronological order."""

            async with self.session_factory() as session:
                conditions = [MarketTick.symbol == symbol]
                if start is not None:
                    conditions.append(MarketTick.timestamp >= ensure_aware_utc(start))
                if end is not None:
                    conditions.append(MarketTick.timestamp < ensure_aware_utc(end))
                statement = (
                    select(MarketTick)
                    .where(and_(*conditions))
                    .order_by(MarketTick.timestamp)
                )
                if limit is not None:
                    statement = statement.limit(limit)
                result = await session.execute(statement)
                return list(result.scalars())

        async def bulk_upsert(
            self,
            model: type[Any],
            rows: Iterable[Mapping[str, Any]],
            *,
            conflict_columns: Sequence[str] | None = None,
            update_columns: Sequence[str] | None = None,
            batch_size: int = 1_000,
        ) -> int:
            """Persist arbitrary contract rows in one explicit transaction.

            ``conflict_columns`` enables PostgreSQL/SQLite upsert semantics.
            Without it this method performs a regular bulk insert, allowing
            server/Python defaults (for example generated IDs) to apply.
            """

            normalized = [_normalize_domain_row(model, row) for row in rows]
            if not normalized:
                return 0
            if conflict_columns and update_columns is None:
                columns = set(model.__table__.columns.keys())
                update_columns = [
                    key
                    for key in normalized[0]
                    if key in columns and key not in conflict_columns
                ]
            total = 0
            async with self.session_factory() as session:
                async with session.begin():
                    for batch in _batches(normalized, batch_size):
                        if conflict_columns:
                            statement = self._upsert_statement(
                                model,
                                batch,
                                list(conflict_columns),
                                list(update_columns or ()),
                            )
                        else:
                            statement = sa_insert(model).values(batch)
                        await session.execute(statement)
                        total += len(batch)
            return total

        async def insert_account_snapshot(self, row: Mapping[str, Any]) -> int:
            """Insert or update one account snapshot."""

            return await self.bulk_upsert(
                AccountSnapshot,
                [row],
                conflict_columns=("account_id", "timestamp"),
            )

        async def bulk_insert_account_snapshots(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert account snapshots keyed by account and timestamp."""

            return await self.bulk_upsert(
                AccountSnapshot,
                rows,
                conflict_columns=("account_id", "timestamp"),
                batch_size=batch_size,
            )

        async def get_account_snapshots(
            self,
            account_id: str,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query account snapshots in ascending UTC time."""

            return await self._query_time_series(
                AccountSnapshot, {"account_id": account_id}, start, end, limit
            )

        async def bulk_insert_indicator_calculations(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert indicator calculations by natural observation key."""

            return await self.bulk_upsert(
                IndicatorCalculation,
                rows,
                conflict_columns=("symbol", "timeframe", "timestamp", "indicator_name"),
                batch_size=batch_size,
            )

        async def get_indicator_calculations(
            self,
            symbol: str,
            timeframe: str | None = None,
            category: str | None = None,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query indicator values for a symbol/time window."""

            filters = {"symbol": symbol}
            if timeframe is not None:
                filters["timeframe"] = timeframe
            if category is not None:
                filters["category"] = category
            return await self._query_time_series(
                IndicatorCalculation, filters, start, end, limit
            )

        async def bulk_insert_filter_evaluations(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert filter evaluations by natural observation key."""

            return await self.bulk_upsert(
                FilterEvaluation,
                rows,
                conflict_columns=("symbol", "timeframe", "timestamp", "filter_name"),
                batch_size=batch_size,
            )

        async def get_filter_evaluations(
            self,
            symbol: str,
            timeframe: str | None = None,
            category: str | None = None,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query filter evaluations for a symbol/time window."""

            filters = {"symbol": symbol}
            if timeframe is not None:
                filters["timeframe"] = timeframe
            if category is not None:
                filters["category"] = category
            return await self._query_time_series(
                FilterEvaluation, filters, start, end, limit
            )

        async def bulk_upsert_orders(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert orders by venue/order identifier."""

            return await self.bulk_upsert(
                TradingOrder,
                rows,
                conflict_columns=("order_id",),
                batch_size=batch_size,
            )

        async def insert_order(self, row: Mapping[str, Any]) -> int:
            """Insert or update one order."""

            return await self.bulk_upsert_orders([row])

        async def record_order_execution(
            self,
            order: Mapping[str, Any],
            execution: Mapping[str, Any],
        ) -> None:
            """Atomically persist an order intent and its execution/fill.

            Keeping both writes in one transaction prevents an execution from
            being visible without its parent order after a restart or failure.
            The execution identifier remains the deduplication key for broker
            retries and reconnects.
            """

            normalized_order = _normalize_domain_row(TradingOrder, order)
            normalized_execution = _normalize_domain_row(TradeExecution, execution)
            order_id = normalized_execution.get("order_id")
            if not order_id or order_id != normalized_order.get("order_id"):
                raise ValueError("execution order_id must match the persisted order")
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(
                        self._upsert_statement(
                            TradingOrder,
                            [normalized_order],
                            ["order_id"],
                            [
                                key
                                for key in normalized_order
                                if key in TradingOrder.__table__.columns
                                and key != "order_id"
                            ],
                        )
                    )
                    await session.execute(
                        self._upsert_statement(
                            TradeExecution,
                            [normalized_execution],
                            ["execution_id"],
                            [
                                key
                                for key in normalized_execution
                                if key in TradeExecution.__table__.columns
                                and key != "execution_id"
                            ],
                        )
                    )

        async def get_orders(
            self,
            *,
            account_id: str | None = None,
            symbol: str | None = None,
            status: str | None = None,
            limit: int | None = None,
        ) -> list[Any]:
            """Query orders using indexed account/symbol/status predicates."""

            return await self._query(
                TradingOrder,
                {
                    key: value
                    for key, value in (
                        ("account_id", account_id),
                        ("symbol", symbol),
                        ("status", status),
                    )
                    if value is not None
                },
                TradingOrder.created_at,
                limit,
            )

        async def bulk_upsert_positions(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk upsert positions by position identifier."""

            return await self.bulk_upsert(
                Position, rows, conflict_columns=("position_id",), batch_size=batch_size
            )

        async def insert_position(self, row: Mapping[str, Any]) -> int:
            """Insert or update one position."""

            return await self.bulk_upsert_positions([row])

        async def get_positions(
            self,
            *,
            account_id: str | None = None,
            symbol: str | None = None,
            status: str | None = None,
            limit: int | None = None,
        ) -> list[Any]:
            """Query positions using account/symbol/status filters."""

            return await self._query(
                Position,
                {
                    key: value
                    for key, value in (
                        ("account_id", account_id),
                        ("symbol", symbol),
                        ("status", status),
                    )
                    if value is not None
                },
                Position.updated_at,
                limit,
            )

        async def bulk_insert_trade_executions(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk insert execution/fill events idempotently by execution ID."""

            return await self.bulk_upsert(
                TradeExecution,
                rows,
                conflict_columns=("execution_id",),
                batch_size=batch_size,
            )

        async def get_trade_executions(
            self,
            symbol: str | None = None,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query fills in timestamp order."""

            filters = {"symbol": symbol} if symbol is not None else {}
            return await self._query_time_series(
                TradeExecution, filters, start, end, limit
            )

        async def bulk_insert_project_logs(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk insert structured project logs."""

            return await self.bulk_upsert(ProjectLog, rows, batch_size=batch_size)

        async def get_project_logs(
            self,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            level: str | None = None,
            limit: int | None = None,
        ) -> list[Any]:
            """Query audit logs in chronological order."""

            filters = {"level": level} if level is not None else {}
            return await self._query_time_series(ProjectLog, filters, start, end, limit)

        async def bulk_insert_ai_predictions(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk insert AI predictions, updating repeated IDs."""

            return await self.bulk_upsert(
                AIPrediction, rows, conflict_columns=("id",), batch_size=batch_size
            )

        async def insert_prediction(self, row: Mapping[str, Any]) -> int:
            """Insert or update one AI prediction."""

            return await self.bulk_insert_ai_predictions([row])

        async def get_ai_predictions(
            self,
            symbol: str,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query AI predictions for a symbol."""

            return await self._query_time_series(
                AIPrediction, {"symbol": symbol}, start, end, limit
            )

        async def bulk_insert_decisions(
            self, rows: Iterable[Mapping[str, Any]], *, batch_size: int = 1_000
        ) -> int:
            """Bulk insert auditable trading decisions."""

            return await self.bulk_upsert(
                TradingDecision, rows, conflict_columns=("id",), batch_size=batch_size
            )

        async def insert_decision(self, row: Mapping[str, Any]) -> int:
            """Insert or update one trading decision."""

            return await self.bulk_insert_decisions([row])

        async def get_decisions(
            self,
            symbol: str | None = None,
            start: datetime | None = None,
            end: datetime | None = None,
            *,
            limit: int | None = None,
        ) -> list[Any]:
            """Query decisions in timestamp order."""

            filters = {"symbol": symbol} if symbol is not None else {}
            return await self._query_time_series(
                TradingDecision, filters, start, end, limit
            )

        async def _query_time_series(
            self,
            model: type[Any],
            filters: Mapping[str, Any],
            start: datetime | None,
            end: datetime | None,
            limit: int | None,
        ) -> list[Any]:
            conditions = dict(filters)
            if start is not None:
                conditions["__start"] = ensure_aware_utc(start)
            if end is not None:
                conditions["__end"] = ensure_aware_utc(end)
            return await self._query(model, conditions, model.timestamp, limit)

        async def _query(
            self,
            model: type[Any],
            filters: Mapping[str, Any],
            order_column: Any,
            limit: int | None,
        ) -> list[Any]:
            """Run a read-only query; writes never happen outside begin()."""

            async with self.session_factory() as session:
                conditions = []
                for key, value in filters.items():
                    if key == "__start":
                        conditions.append(order_column >= value)
                    elif key == "__end":
                        conditions.append(order_column < value)
                    else:
                        conditions.append(getattr(model, key) == value)
                statement = (
                    select(model).where(and_(*conditions)).order_by(order_column)
                )
                if limit is not None:
                    statement = statement.limit(limit)
                result = await session.execute(statement)
                return list(result.scalars())

        def _upsert_statement(
            self,
            model: type[Any],
            values: list[dict[str, Any]],
            conflict_columns: list[str],
            update_columns: list[str],
        ) -> Insert:
            dialect = (
                self.engine.sync_engine.dialect.name
                if self.engine is not None
                else "postgresql"
            )
            if dialect == "postgresql":
                statement = pg_insert(model).values(values)
            else:
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                statement = sqlite_insert(model).values(values)
            excluded = statement.excluded
            return statement.on_conflict_do_update(
                index_elements=conflict_columns,
                # Mapping access also handles SQL columns named ``values``,
                # ``format`` and other names that collide with attributes.
                set_={column: excluded[column] for column in update_columns},
            )

    class AsyncDatabase:
        """Owns the async engine and repository lifecycle."""

        def __init__(
            self,
            url: str,
            *,
            echo: bool = False,
            pool_size: int = 5,
            max_overflow: int = 10,
        ) -> None:
            self.url = normalize_database_url(url)
            if self.url.startswith("sqlite+aiosqlite") and _missing_aiosqlite():
                raise RuntimeError(
                    "SQLite async tests require the optional 'aiosqlite' package"
                )
            kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
            if self.url.startswith("postgresql"):
                kwargs.update(pool_size=pool_size, max_overflow=max_overflow)
            try:
                self.engine = create_async_engine(self.url, **kwargs)
            except ImportError as error:
                if self.url.startswith("postgresql"):
                    raise RuntimeError(
                        "PostgreSQL async support requires the optional 'asyncpg' package"
                    ) from error
                raise
            self.session_factory = async_sessionmaker(
                self.engine, expire_on_commit=False, autoflush=False
            )
            self.repository = DatabaseRepository(self.session_factory, self.engine)

        async def initialize_for_tests(self, *, hypertables: bool = False) -> None:
            """Provision a local/test schema, optionally followed by Timescale setup.

            Production deployments must run Alembic before startup and use
            :meth:`verify_schema`; this helper is intentionally explicit for
            SQLite fixtures and local development.
            """

            await self.repository.create_schema()
            from .versioning import record_system_versions

            await record_system_versions(self.repository)
            if hypertables:
                await self.repository.ensure_timescale_hypertables()

        async def initialize(self, *, hypertables: bool = False) -> None:
            """Backward-compatible alias for explicit local/test initialization."""

            await self.initialize_for_tests(hypertables=hypertables)

        async def verify_schema(self) -> Any:
            """Verify Alembic head and required execution-control schema."""

            from .schema_verifier import audit_runtime_database

            report = await audit_runtime_database(self)
            if not report.ok:
                details = "; ".join(report.issues)
                raise RuntimeError(f"database schema verification failed: {details}")
            return report

        async def dispose(self) -> None:
            """Close all pooled connections."""

            await self.engine.dispose()

    Database: Any = AsyncDatabase
    AsyncDatabaseRepository: Any = DatabaseRepository
    DatabaseService: Any = DatabaseRepository

    def _missing_aiosqlite() -> bool:
        try:
            import aiosqlite  # noqa: F401
        except ImportError:
            return True
        return False

    def create_database(url: str, **kwargs: Any) -> AsyncDatabase:
        """Construct an async database facade."""

        return AsyncDatabase(url, **kwargs)


else:

    class DatabaseRepository:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(
                "SQLAlchemy is required for the data storage layer"
            ) from _SQLALCHEMY_IMPORT_ERROR

    class AsyncDatabase(DatabaseRepository):  # type: ignore[no-redef]
        pass

    Database = AsyncDatabase
    AsyncDatabaseRepository = DatabaseRepository
    DatabaseService = DatabaseRepository

    def create_database(url: str, **kwargs: Any) -> AsyncDatabase:
        raise RuntimeError("SQLAlchemy is required for the data storage layer")


async def ensure_timescale_hypertables(engine: Any) -> None:
    """Idempotently enable TimescaleDB and convert collector time-series tables.

    This intentionally refuses SQLite and other dialects instead of silently
    doing nothing, which prevents a local test database from being mistaken for
    production storage.
    """

    if not SQLALCHEMY_AVAILABLE:
        raise RuntimeError("SQLAlchemy is required for TimescaleDB setup")
    dialect = engine.sync_engine.dialect.name
    if dialect != "postgresql":
        raise RuntimeError(
            "TimescaleDB hypertables require a PostgreSQL engine; "
            f"received {dialect!r} (SQLite is supported for tests but has no hypertables)"
        )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        for table in TIMESCALE_HYPERTABLES:
            await connection.execute(
                text(
                    "SELECT create_hypertable("
                    f"'{table}', 'timestamp', if_not_exists => TRUE)"
                )
            )


async def _ensure_derived_data_columns(connection: Any) -> None:
    """Add additive categorisation fields to existing derived-data tables."""

    dialect = connection.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            f"unsupported database dialect for schema migration: {dialect!r}"
        )
    if dialect == "postgresql":
        statements = (
            "ALTER TABLE indicator_calculations "
            "ADD COLUMN IF NOT EXISTS category VARCHAR(64) NOT NULL DEFAULT 'general'",
            "ALTER TABLE filter_evaluations "
            "ADD COLUMN IF NOT EXISTS category VARCHAR(64) NOT NULL DEFAULT 'general'",
        )
    else:
        statements = ()
        for table in ("indicator_calculations", "filter_evaluations"):
            result = await connection.execute(text(f"PRAGMA table_info({table})"))
            columns = {row[1] for row in result}
            if "category" not in columns:
                await connection.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN "
                        "category VARCHAR(64) NOT NULL DEFAULT 'general'"
                    )
                )
    statements += (
        "CREATE INDEX IF NOT EXISTS ix_indicator_category_time "
        "ON indicator_calculations (symbol, timeframe, category, timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_filter_category_time "
        "ON filter_evaluations (symbol, timeframe, category, timestamp)",
    )
    for statement in statements:
        await connection.execute(text(statement))


def _normalize_candle(row: Mapping[str, Any]) -> dict[str, Any]:
    required = ("symbol", "timeframe", "open", "high", "low", "close")
    missing = [key for key in required if row.get(key) is None]
    if missing:
        raise ValueError(f"candle is missing required fields: {', '.join(missing)}")
    payload = dict(row.get("payload") or {})
    payload.update(
        {
            str(key): value
            for key, value in row.items()
            if key
            not in {
                "symbol",
                "timeframe",
                "timestamp",
                "time",
                "time_msc",
                "open",
                "high",
                "low",
                "close",
                "tick_volume",
                "volume",
                "spread",
                "status",
                "payload",
                "source",
                "ingestion_metadata",
            }
        }
    )
    return {
        "symbol": str(row["symbol"]),
        "timeframe": str(row["timeframe"]),
        "timestamp": _row_timestamp(row),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "tick_volume": _as_float(row.get("tick_volume"), 0.0),
        "volume": _as_float(row.get("volume"), 0.0),
        "spread": int(row["spread"]) if row.get("spread") is not None else None,
        "status": row.get("status", "persisted"),
        "payload": payload,
        "source": str(row.get("source") or "mt5"),
        "ingestion_metadata": dict(row.get("ingestion_metadata") or {}),
    }


def _normalize_tick(row: Mapping[str, Any]) -> dict[str, Any]:
    if not row.get("symbol"):
        raise ValueError("tick is missing required field: symbol")
    payload = dict(row.get("payload") or {})
    payload.update(
        {
            str(key): value
            for key, value in row.items()
            if key
            not in {
                "symbol",
                "timestamp",
                "time",
                "time_msc",
                "bid",
                "ask",
                "last",
                "volume",
                "status",
                "payload",
            }
        }
    )
    return {
        "symbol": str(row["symbol"]),
        "timestamp": _row_timestamp(row),
        # A complete observation key is required by the Timescale-compatible
        # composite primary key.  MT5 normally supplies both fields; zero is
        # an explicit sentinel for feeds that only expose ``last``.
        "bid": _as_float(row.get("bid"), 0.0),
        "ask": _as_float(row.get("ask"), 0.0),
        "last": _as_float(row.get("last")),
        "volume": _as_float(row.get("volume")),
        "status": row.get("status", "persisted"),
        "payload": payload,
    }


def _normalize_domain_row(model: type[Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only mapped columns and normalise all known UTC datetime fields."""

    if not isinstance(row, Mapping):
        raise TypeError("repository rows must be mappings")
    columns = set(model.__table__.columns.keys())
    values = {key: value for key, value in row.items() if key in columns}
    if "timestamp" in columns and "timestamp" not in values:
        for alias in ("time", "time_msc"):
            if alias in row:
                raw = row[alias]
                if (
                    alias == "time_msc"
                    and isinstance(raw, (int, float))
                    and raw > 10_000_000_000
                ):
                    raw = raw / 1000
                values["timestamp"] = raw
                break
    for key in ("timestamp", "created_at", "updated_at", "opened_at", "closed_at"):
        if key in values and values[key] is not None:
            values[key] = ensure_aware_utc(values[key])
    if "payload" in columns and "payload" not in values:
        # Preserve flexible provider fields without leaking unknown SQL columns.
        payload = {
            str(key): value
            for key, value in row.items()
            if key not in columns and key not in {"time", "time_msc"}
        }
        if payload:
            values["payload"] = payload
    return values


def _deduplicate(
    rows: Sequence[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    return list({tuple(row.get(key) for key in keys): row for row in rows}.values())


def _batches(
    rows: Sequence[dict[str, Any]], size: int
) -> Iterable[list[dict[str, Any]]]:
    if size <= 0:
        raise ValueError("batch_size must be greater than zero")
    for index in range(0, len(rows), size):
        yield list(rows[index : index + size])


__all__ = [
    "AsyncDatabase",
    "AsyncDatabaseRepository",
    "Database",
    "DatabaseRepository",
    "DatabaseService",
    "create_database",
    "ensure_aware_utc",
    "normalize_database_url",
    "TIMESCALE_HYPERTABLES",
    "ensure_timescale_hypertables",
]
