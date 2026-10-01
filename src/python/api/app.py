"""Production HTTP boundary for read-only data and explicitly gated execution."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..data.config import Timeframe
from ..data.database import (
    AsyncDatabase,
    IdempotencyInProgress,
    IdempotencyKeyReuse,
    UnknownOrderResolutionError,
)
from ..data.versioning import version_payload
from ..execution.broker_reconciliation import reconcile_unknown_orders
from ..execution.live_order_workflow import AmbiguousOrderOutcome, LiveOrderRejected
from ..execution.policy import DEFAULT_EXECUTION_POLICY
from ..logging_config import (
    bind_request_context,
    get_logger,
    log_event,
    new_correlation_id,
)
from ..observability import AlertManager, MetricsRegistry
from ..risk.circuit_breaker import CircuitBreaker
from .auth import (
    EMERGENCY_STOP,
    ORDER_REVIEW,
    READ_ONLY,
    SIGNAL_EXECUTION,
    AuthenticationService,
    Principal,
    require_role,
)
from .zmq_gateway import MQL5ExecutionGateway


class ExecuteRequest(BaseModel):
    """Request for a previously approved signal."""

    signal_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=64)
    action: str = Field(pattern="^(buy|sell)$")
    volume: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    magic: int | None = Field(default=None, gt=0)
    dry_run: bool = True
    confirmation_token: str = Field(default="", max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime | None = None
    expires_at: datetime | None = None


class ReconcileOrderRequest(BaseModel):
    """Operator-confirmed outcome for an order whose broker result was unknown."""

    resolution: str = Field(pattern="^(accepted|rejected)$")
    evidence: dict[str, Any] = Field(default_factory=dict)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "")


def _broker_result_payload(result: Any) -> dict[str, Any]:
    """Extract stable broker evidence without persisting connector objects."""
    payload: dict[str, Any] = {}
    for field in ("retcode", "order", "deal", "comment", "request_id"):
        value = getattr(result, field, None)
        if value is not None:
            payload[field] = value.value if hasattr(value, "value") else value
    return payload


def _has_positive_broker_identifiers(payload: dict[str, Any]) -> bool:
    """Require both MT5 identifiers before an accepted result is durable."""
    try:
        return int(payload.get("order", 0)) > 0 and int(payload.get("deal", 0)) > 0
    except (TypeError, ValueError):
        return False


def _is_accepted_broker_retcode(value: Any) -> bool:
    """Normalize MT5 retcodes before applying accepted-result invariants."""
    try:
        return int(value) in {10008, 10009, 10010}
    except (TypeError, ValueError):
        return False


def create_app(
    *,
    database: AsyncDatabase | None = None,
    gateway: MQL5ExecutionGateway | None = None,
    allowed_symbols: frozenset[str] | None = None,
    live_workflow: Any | None = None,
    mt5_connection: Any | None = None,
    circuit_breaker: Any | None = None,
    allow_anonymous: bool = False,
    production_mode: bool | None = None,
) -> FastAPI:
    """Create the API with injected infrastructure for deterministic tests."""
    runtime_database = database or AsyncDatabase(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/smart_trader.db")
    )
    runtime_gateway = gateway or MQL5ExecutionGateway(
        os.getenv("ZMQ_EXECUTION_ENDPOINT", "tcp://127.0.0.1:5555"),
        int(os.getenv("ZMQ_EXECUTION_TIMEOUT_MS", "1000")),
    )
    symbols = allowed_symbols or frozenset(
        item.strip().upper()
        for item in os.getenv(
            "MT5_LIVE_SYMBOLS", DEFAULT_EXECUTION_POLICY.default_symbol
        ).split(",")
        if item.strip()
    )
    runtime_workflow = live_workflow
    runtime_mt5 = mt5_connection
    runtime_circuit_breaker = circuit_breaker
    live_mode_enabled = os.getenv("MT5_AUTO_TRADING_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    is_production = (
        production_mode
        if production_mode is not None
        else (
            os.getenv("APP_ENV", "").strip().lower() in {"production", "prod"}
            or (
                os.getenv("APP_ENV", "").strip().lower()
                not in {"development", "dev", "test", "local"}
                and not runtime_database.url.startswith("sqlite")
            )
        )
    )
    if is_production and database is None and not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required in production mode")
    if is_production and runtime_database.url.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed in production mode")
    if runtime_workflow is None:
        # Construct the production workflow lazily.  Importing the optional
        # MT5 package must not make read-only or dry-run API startup fail.
        try:
            from ..execution.live_order_workflow import (
                LiveOrderConfig,
                LiveOrderWorkflow,
            )
            from ..execution.mt5_connector import MT5Connection

            workflow_config = LiveOrderConfig.from_environment()
            if not workflow_config.allowed_symbols:
                workflow_config = replace(workflow_config, allowed_symbols=symbols)
            runtime_workflow = LiveOrderWorkflow(
                MT5Connection(),
                workflow_config,
                control_store=getattr(runtime_database, "repository", None),
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            runtime_workflow = None
    if runtime_mt5 is None and runtime_workflow is not None:
        runtime_mt5 = getattr(runtime_workflow, "connector", None)
    metrics = MetricsRegistry()
    alerts = AlertManager(
        metrics,
        thresholds={
            "http_5xx_total": float(os.getenv("OBS_ALERT_HTTP_5XX_THRESHOLD", "5")),
            "order_rejections_total": float(
                os.getenv("OBS_ALERT_ORDER_REJECTIONS_THRESHOLD", "1")
            ),
            "mt5_gateway_timeouts_total": float(
                os.getenv("OBS_ALERT_GATEWAY_TIMEOUTS_THRESHOLD", "1")
            ),
            "signal_stale_total": float(
                os.getenv("OBS_ALERT_STALE_SIGNAL_THRESHOLD", "1")
            ),
            "idempotency_duplicates_total": float(
                os.getenv("OBS_ALERT_DUPLICATE_IDEMPOTENCY_THRESHOLD", "1")
            ),
            "data_gaps_total": float(os.getenv("OBS_ALERT_DATA_GAPS_THRESHOLD", "1")),
        },
    )
    logger = get_logger("api")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal runtime_circuit_breaker
        try:
            if is_production:
                verify_schema = getattr(runtime_database, "verify_schema", None)
                if verify_schema is None:
                    raise RuntimeError(
                        "production database must provide migration schema verification"
                    )
                await verify_schema()
            else:
                initialize_for_tests = getattr(
                    runtime_database, "initialize_for_tests", None
                )
                if initialize_for_tests is None:
                    # Keep older local/test doubles working while real
                    # databases use the explicit helper above.
                    initialize_for_tests = runtime_database.initialize
                await initialize_for_tests(
                    hypertables=runtime_database.url.startswith("postgresql")
                )
            repository = getattr(runtime_database, "repository", None)
            recover_pending = getattr(repository, "recover_pending_order_intents", None)
            if recover_pending is not None:
                await recover_pending()
            mt5_connected_by_lifespan = False
            if (
                runtime_mt5 is not None
                and os.getenv("MT5_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"}
                and not bool(runtime_mt5.is_connected())
            ):
                connect = getattr(runtime_mt5, "connect", None)
                if connect is not None:
                    try:
                        login_value = os.getenv("MT5_LOGIN")
                        mt5_connected_by_lifespan = bool(
                            connect(
                                path=os.getenv("MT5_TERMINAL_PATH") or None,
                                login=int(login_value) if login_value else None,
                                password=os.getenv("MT5_PASSWORD") or None,
                                server=os.getenv("MT5_SERVER") or None,
                            )
                        )
                    except (OSError, RuntimeError, TypeError, ValueError):
                        logger.exception("mt5_runtime_connect_failed")
            if live_mode_enabled and runtime_circuit_breaker is None:
                try:
                    if runtime_mt5 is None or not bool(runtime_mt5.is_connected()):
                        raise RuntimeError("MT5 is not connected for live risk setup")
                    account = runtime_mt5.get_account_summary()
                    capital = float(account.get("equity") or account.get("balance") or 0)
                    if capital <= 0:
                        raise ValueError("MT5 account equity must be positive")
                    history = runtime_mt5.get_history(days=1)
                    if hasattr(history, "to_dict"):
                        history = history.to_dict(orient="records")
                    trades = []
                    for trade in history or []:
                        row = dict(trade)
                        if "pnl" not in row and "profit" in row:
                            row["pnl"] = row["profit"]
                        trades.append(row)
                    runtime_circuit_breaker = CircuitBreaker(
                        capital,
                        max_daily_loss_percent=float(
                            os.getenv("MT5_MAX_DAILY_LOSS_PERCENT", "2")
                        ),
                    )
                    runtime_circuit_breaker.evaluate(trades)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    logger.exception("live_circuit_breaker_initialization_failed")
                    runtime_circuit_breaker = None
            yield
        finally:
            if mt5_connected_by_lifespan:
                disconnect = getattr(runtime_mt5, "disconnect", None)
                if disconnect is not None:
                    disconnect()
            runtime_gateway.close()
            await runtime_database.dispose()

    app = FastAPI(title="Smart MT5 Trading API", version="1.0.0", lifespan=lifespan)
    auth = AuthenticationService()
    metrics_token = os.getenv("OBS_METRICS_TOKEN", "").strip()

    async def authenticate(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Principal:
        return require_role(
            auth,
            api_key=x_api_key,
            authorization=authorization,
            role=READ_ONLY,
            require_configured=not allow_anonymous,
        )

    async def authenticate_order_review(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Principal:
        return require_role(
            auth,
            api_key=x_api_key,
            authorization=authorization,
            role=ORDER_REVIEW,
            require_configured=True,
        )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next: Any) -> Any:
        requested_id = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = (
            requested_id if requested_id and len(requested_id) <= 128 else str(uuid4())
        )
        request.state.correlation_id = new_correlation_id()
        bind_request_context(request.state.request_id, request.state.correlation_id)
        started = datetime.now(timezone.utc)
        metrics.inc(
            "http_requests_total",
            labels={"method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            metrics.inc("http_5xx_total")
            alerts.evaluate()
            raise
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        metrics.observe(
            "http_request_duration_seconds", elapsed, labels={"method": request.method}
        )
        if response.status_code >= 500:
            metrics.inc("http_5xx_total")
        alerts.evaluate()
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        return {
            "data": {"status": "ok", **version_payload()},
            "request_id": _request_id(request),
            "correlation_id": _correlation_id(request),
        }

    @app.get("/ready")
    async def readiness(request: Request) -> dict[str, Any]:
        """Report whether the database dependency is accepting queries."""
        dependency_status: dict[str, str] = {
            "database": "ready",
            "mt5": "not_configured",
            "circuit_breaker": "not_configured",
        }
        try:
            async with runtime_database.engine.begin() as connection:
                await connection.exec_driver_sql("SELECT 1")
        except Exception as error:
            metrics.inc("readiness_failures_total")
            log_event(logger, 40, "readiness_failed", error_type=type(error).__name__)
            raise HTTPException(
                status_code=503, detail="database_unavailable"
            ) from error
        if runtime_mt5 is not None:
            metrics.set_gauge("mt5_health_configured", 1)
            try:
                mt5_ready = bool(runtime_mt5.is_connected())
            except Exception as error:
                mt5_ready = False
                log_event(
                    logger,
                    40,
                    "mt5_healthcheck_failed",
                    error_type=type(error).__name__,
                )
            dependency_status["mt5"] = "ready" if mt5_ready else "unavailable"
            metrics.set_gauge("mt5_connected", int(mt5_ready))
        else:
            metrics.set_gauge("mt5_health_configured", 0)
            dependency_status["mt5"] = "unavailable"
        if runtime_circuit_breaker is not None:
            metrics.set_gauge("circuit_breaker_configured", 1)
            tripped = bool(getattr(runtime_circuit_breaker, "is_tripped", False))
            dependency_status["circuit_breaker"] = "tripped" if tripped else "armed"
            metrics.set_gauge("circuit_breaker_tripped", int(tripped))
        else:
            metrics.set_gauge("circuit_breaker_configured", 0)
        if live_mode_enabled and dependency_status["circuit_breaker"] == "not_configured":
            raise HTTPException(status_code=503, detail="circuit_breaker_unavailable")
        if live_mode_enabled and dependency_status["mt5"] == "unavailable":
            raise HTTPException(status_code=503, detail="mt5_unavailable")
        return {
            "data": {
                "status": "ready",
                "dependencies": dependency_status,
                "trading": {
                    "allowed": dependency_status["circuit_breaker"]
                    not in {"tripped", "unavailable"}
                    and dependency_status["mt5"] != "unavailable"
                },
            },
            "request_id": _request_id(request),
            "correlation_id": _correlation_id(request),
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> Any:
        """Expose operational metrics for a local Prometheus-compatible scraper."""
        from fastapi.responses import PlainTextResponse

        if metrics_token and x_api_key != metrics_token:
            raise HTTPException(status_code=401, detail="unauthorized")
        return PlainTextResponse(
            metrics.render_prometheus(), media_type="text/plain; version=0.0.4"
        )

    @app.get("/api/v1/data/ohlc")
    async def ohlc(
        request: Request,
        symbol: str = Query(min_length=1, max_length=64),
        timeframe: Timeframe = Query(...),
        limit: int = Query(1000, ge=1, le=10_000),
        start: datetime | None = None,
        end: datetime | None = None,
        _: Principal = Depends(authenticate),
    ) -> dict[str, Any]:
        if start and end and start >= end:
            raise HTTPException(status_code=422, detail="start must precede end")
        bars = await runtime_database.repository.candles(
            symbol.upper(), timeframe.value, start, end, limit=limit
        )
        return {
            "data": {
                "symbol": symbol.upper(),
                "timeframe": timeframe.value,
                "candles": [bar.to_payload() for bar in bars],
            },
            "request_id": _request_id(request),
        }

    @app.post("/api/v1/signals/{signal_id}/execute", status_code=202)
    async def execute(
        signal_id: str,
        body: ExecuteRequest,
        request: Request,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> dict[str, Any]:
        if signal_id != body.signal_id:
            raise HTTPException(status_code=422, detail="signal_id mismatch")
        symbol = body.symbol.upper()
        if symbol not in symbols:
            metrics.inc(
                "order_rejections_total", labels={"reason": "symbol_not_whitelisted"}
            )
            raise HTTPException(status_code=409, detail="symbol_not_whitelisted")
        if not body.dry_run:
            require_role(
                auth,
                api_key=x_api_key,
                authorization=authorization,
                role=SIGNAL_EXECUTION,
                require_configured=True,
            )
        now = datetime.now(timezone.utc)
        for timestamp in (body.created_at, body.expires_at):
            if timestamp is not None and timestamp.tzinfo is None:
                raise HTTPException(
                    status_code=422, detail="timestamps must be timezone-aware"
                )
        if body.created_at and body.created_at > now + timedelta(seconds=30):
            raise HTTPException(status_code=409, detail="signal_from_future")
        if body.created_at and now - body.created_at > timedelta(minutes=10):
            metrics.inc("signal_stale_total")
            raise HTTPException(status_code=409, detail="signal_stale")
        if body.created_at and body.expires_at and body.expires_at <= body.created_at:
            raise HTTPException(
                status_code=422, detail="expires_at must follow created_at"
            )
        if body.expires_at and body.expires_at <= now:
            metrics.inc("order_rejections_total", labels={"reason": "signal_expired"})
            raise HTTPException(status_code=409, detail="signal_expired")
        workflow_magic = getattr(
            getattr(runtime_workflow, "config", None), "magic", None
        )
        if (
            not body.dry_run
            and body.magic is not None
            and workflow_magic is not None
            and body.magic != workflow_magic
        ):
            metrics.inc("order_rejections_total", labels={"reason": "magic_mismatch"})
            raise HTTPException(status_code=409, detail="magic_mismatch")
        if not body.dry_run and not body.confirmation_token:
            metrics.inc(
                "order_rejections_total", labels={"reason": "confirmation_required"}
            )
            raise HTTPException(status_code=409, detail="confirmation_required")
        idem = idempotency_header or body.idempotency_key
        if (
            idempotency_header
            and body.idempotency_key
            and idempotency_header != body.idempotency_key
        ):
            raise HTTPException(status_code=422, detail="idempotency key mismatch")
        # Preserve the historical dry-run contract: idempotency is mandatory
        # for writes, while simulation requests remain convenient and safe.
        if not body.dry_run and not idem:
            raise HTTPException(status_code=422, detail="Idempotency-Key is required")
        request_hash = hashlib.sha256(
            json.dumps(
                {"signal_id": signal_id, **body.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        repository = getattr(runtime_database, "repository", None)
        claim_idempotency = getattr(repository, "claim_idempotency", None)
        claim_order_intent = getattr(repository, "claim_order_intent", None)
        order_id = f"signal:{signal_id}:idempotency:{idem}" if idem else ""
        order_payload = {
            "symbol": symbol,
            "side": body.action,
            "quantity": body.volume,
            "stop_loss": body.stop_loss,
            "take_profit": body.take_profit,
            "correlation_id": _correlation_id(request),
            "payload": {"signal_id": signal_id, "request_hash": request_hash},
        }
        if idem and claim_order_intent is not None and not body.dry_run:
            try:
                durable_response = await claim_order_intent(
                    idem, request_hash, order_id, order_payload
                )
            except IdempotencyKeyReuse as error:
                metrics.inc("idempotency_duplicates_total")
                raise HTTPException(
                    status_code=409, detail="idempotency_key_reuse"
                ) from error
            except IdempotencyInProgress as error:
                raise HTTPException(
                    status_code=409, detail="idempotency_in_progress"
                ) from error
            if durable_response is not None:
                metrics.inc("idempotency_duplicates_total")
                return {"data": durable_response, "request_id": _request_id(request)}
        elif idem and claim_idempotency is not None:
            try:
                durable_response = await claim_idempotency(idem, request_hash)
            except IdempotencyKeyReuse as error:
                metrics.inc("idempotency_duplicates_total")
                raise HTTPException(
                    status_code=409, detail="idempotency_key_reuse"
                ) from error
            except IdempotencyInProgress as error:
                raise HTTPException(
                    status_code=409, detail="idempotency_in_progress"
                ) from error
            if durable_response is not None:
                metrics.inc("idempotency_duplicates_total")
                return {"data": durable_response, "request_id": _request_id(request)}
        if body.dry_run:
            result = {"accepted": True, "mode": "dry_run", "signal_id": signal_id}
        else:
            if runtime_workflow is None:
                raise HTTPException(
                    status_code=503, detail="live workflow is unavailable"
                )
            create_order_intent = getattr(repository, "create_order_intent", None)
            transition_order = getattr(repository, "transition_order", None)
            if repository is None or transition_order is None:
                raise HTTPException(
                    status_code=503, detail="durable_order_state_unavailable"
                )
            if claim_order_intent is None:
                create_order_intent = getattr(repository, "create_order_intent", None)
                if create_order_intent is None:
                    raise HTTPException(
                        status_code=503, detail="durable_order_state_unavailable"
                    )
                await create_order_intent(order_id, idem, order_payload)
            try:
                # Route every live order through the workflow.  The workflow
                # owns risk gates and supplies the configured magic number.
                execute_market_order_async = getattr(
                    runtime_workflow, "execute_market_order_async", None
                )
                if execute_market_order_async is not None:
                    result = await execute_market_order_async(
                        symbol,
                        body.action,
                        body.volume,
                        stop_loss=body.stop_loss,
                        take_profit=body.take_profit,
                        confirmation_token=body.confirmation_token,
                        client_order_id=order_id,
                        correlation_id=_correlation_id(request),
                    )
                else:
                    result = runtime_workflow.execute_market_order(
                        symbol,
                        body.action,
                        body.volume,
                        stop_loss=body.stop_loss,
                        take_profit=body.take_profit,
                        confirmation_token=body.confirmation_token,
                        client_order_id=order_id,
                        correlation_id=_correlation_id(request),
                    )
                broker_payload = _broker_result_payload(result)
                if _is_accepted_broker_retcode(
                    broker_payload.get("retcode")
                ) and not _has_positive_broker_identifiers(broker_payload):
                    raise AmbiguousOrderOutcome(
                        "execution_result_unknown",
                        "MT5 accepted the order but returned invalid broker identifiers",
                    )
                await transition_order(
                    order_id,
                    "accepted",
                    payload={"execution": broker_payload},
                    correlation_id=_correlation_id(request),
                    broker_order_id=(
                        str(broker_payload["order"])
                        if broker_payload.get("order") is not None
                        else None
                    ),
                    broker_deal_id=(
                        str(broker_payload["deal"])
                        if broker_payload.get("deal") is not None
                        else None
                    ),
                )
                result = {
                    "accepted": True,
                    "mode": "live",
                    "order_id": order_id,
                    **broker_payload,
                }
            except AmbiguousOrderOutcome as error:
                await transition_order(
                    order_id,
                    "unknown",
                    payload={"reason": error.code},
                    correlation_id=_correlation_id(request),
                )
                unknown_result = {
                    "accepted": False,
                    "mode": "unknown",
                    "signal_id": signal_id,
                    "order_id": order_id,
                    "reason": error.code,
                }
                await repository.complete_idempotency(
                    idem, unknown_result, status="unknown"
                )
                raise HTTPException(status_code=503, detail=error.code) from error
            except LiveOrderRejected as error:
                metrics.inc("order_rejections_total", labels={"reason": error.code})
                if idem and repository is not None:
                    rejection_payload: dict[str, Any] = {"reason": error.code}
                    if error.retcode is not None:
                        rejection_payload["retcode"] = error.retcode
                    await transition_order(
                        order_id,
                        "rejected",
                        payload=rejection_payload,
                        correlation_id=_correlation_id(request),
                    )
                    rejected_result = {
                        "accepted": False,
                        "mode": "rejected",
                        "signal_id": signal_id,
                        "order_id": order_id,
                        "reason": error.code,
                    }
                    if error.retcode is not None:
                        rejected_result["retcode"] = error.retcode
                    await repository.complete_idempotency(
                        idem, rejected_result, status="rejected"
                    )
                raise HTTPException(status_code=409, detail=error.code) from error
            except Exception as error:
                if idem and repository is not None:
                    unknown_result = {
                        "accepted": False,
                        "mode": "unknown",
                        "signal_id": signal_id,
                        "order_id": order_id,
                        "reason": "execution_result_unknown",
                    }
                    await transition_order(
                        order_id,
                        "unknown",
                        payload={"reason": "execution_result_unknown"},
                        correlation_id=_correlation_id(request),
                    )
                    await repository.complete_idempotency(
                        idem, unknown_result, status="unknown"
                    )
                log_event(
                    logger,
                    40,
                    "execution_result_unknown",
                    signal_id=signal_id,
                    error_type=type(error).__name__,
                )
                raise HTTPException(
                    status_code=503, detail="execution_result_unknown"
                ) from error
        if idem:
            if repository is None or not hasattr(repository, "complete_idempotency"):
                raise HTTPException(
                    status_code=503, detail="durable_idempotency_unavailable"
                )
            await repository.complete_idempotency(idem, result)
        return {"data": result, "request_id": _request_id(request)}

    @app.get("/api/v1/orders/unknown")
    async def unknown_orders(
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        _: Principal = Depends(authenticate_order_review),
    ) -> dict[str, Any]:
        """List orders whose external execution outcome needs operator review."""

        repository = getattr(runtime_database, "repository", None)
        getter = getattr(repository, "get_unknown_orders", None)
        if getter is None:
            raise HTTPException(
                status_code=503, detail="durable_order_state_unavailable"
            )
        orders = await getter(limit=limit)
        return {
            "data": [
                {
                    "order_id": order.order_id,
                    "idempotency_key": order.idempotency_key,
                    "symbol": order.symbol,
                    "side": order.side.value
                    if hasattr(order.side, "value")
                    else str(order.side),
                    "quantity": float(order.quantity),
                    "status": order.status.value
                    if hasattr(order.status, "value")
                    else str(order.status),
                    "payload": order.payload or {},
                    "updated_at": order.updated_at.isoformat(),
                }
                for order in orders
            ],
            "request_id": _request_id(request),
        }

    @app.post("/api/v1/orders/{order_id}/reconcile")
    async def reconcile_order(
        order_id: str,
        body: ReconcileOrderRequest,
        request: Request,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> dict[str, Any]:
        """Resolve an unknown order; this endpoint never retries a broker request."""

        require_role(
            auth,
            api_key=x_api_key,
            authorization=authorization,
            role=EMERGENCY_STOP,
            require_configured=True,
        )
        repository = getattr(runtime_database, "repository", None)
        reconcile = getattr(repository, "reconcile_unknown_order", None)
        if reconcile is None:
            raise HTTPException(
                status_code=503, detail="durable_order_state_unavailable"
            )
        try:
            result = await reconcile(
                order_id,
                body.resolution,
                evidence=body.evidence,
            )
        except UnknownOrderResolutionError as error:
            raise HTTPException(
                status_code=409, detail="order_not_reconcilable"
            ) from error
        return {"data": result, "request_id": _request_id(request)}

    @app.post("/api/v1/orders/reconcile/automatic")
    async def automatic_order_reconciliation(
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> dict[str, Any]:
        """Reconcile unknown orders from MT5 history without sending orders."""

        require_role(
            auth,
            api_key=x_api_key,
            authorization=authorization,
            role=EMERGENCY_STOP,
            require_configured=True,
        )
        repository = getattr(runtime_database, "repository", None)
        if repository is None or runtime_mt5 is None:
            raise HTTPException(status_code=503, detail="reconciliation_unavailable")
        if not hasattr(repository, "get_unknown_orders") or not hasattr(
            runtime_mt5, "get_order_history"
        ):
            raise HTTPException(status_code=503, detail="reconciliation_unavailable")
        try:
            result = await reconcile_unknown_orders(
                repository,
                runtime_mt5,
                magic=getattr(getattr(runtime_workflow, "config", None), "magic", None),
                limit=limit,
            )
        except (RuntimeError, ValueError) as error:
            log_event(
                logger,
                40,
                "automatic_reconciliation_failed",
                error_type=type(error).__name__,
            )
            raise HTTPException(
                status_code=503, detail="reconciliation_failed"
            ) from error
        return {"data": result, "request_id": _request_id(request)}

    return app


app = create_app()
