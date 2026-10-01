from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.python.api.app import create_app
from src.python.api.auth import SIGNAL_EXECUTION, AuthenticationService
from src.python.execution.live_order_workflow import (
    AmbiguousOrderOutcome,
    LiveOrderConfig,
    LiveOrderWorkflow,
)


class _Database:
    url = "sqlite"

    async def initialize(self, **_: object) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _DurableRepository:
    def __init__(self) -> None:
        self.responses: dict[str, dict[str, object]] = {}
        self.claims: dict[str, str] = {}
        self.orders: dict[str, dict[str, object]] = {}
        self.transition_correlations: list[str | None] = []

    async def claim_idempotency(self, key: str, request_hash: str):
        from src.python.data.database import IdempotencyInProgress, IdempotencyKeyReuse

        if key in self.claims:
            if self.claims[key] != request_hash:
                raise IdempotencyKeyReuse("key reuse")
            if key not in self.responses:
                raise IdempotencyInProgress("in progress")
            return self.responses[key]
        self.claims[key] = request_hash
        return None

    async def complete_idempotency(
        self, key: str, response: dict[str, object], *, status: str = "completed"
    ):
        self.responses[key] = response

    async def release_idempotency(self, key: str):
        self.claims.pop(key, None)

    async def create_order_intent(self, order_id, idempotency_key, order):
        self.orders[order_id] = {
            **order,
            "order_id": order_id,
            "idempotency_key": idempotency_key,
            "status": "pending",
        }

    async def transition_order(
        self,
        order_id,
        status,
        *,
        payload=None,
        correlation_id=None,
        broker_order_id=None,
        broker_deal_id=None,
    ):
        self.orders[order_id]["status"] = status
        self.transition_correlations.append(correlation_id)
        if broker_order_id is not None:
            self.orders[order_id]["broker_order_id"] = broker_order_id
        if broker_deal_id is not None:
            self.orders[order_id]["broker_deal_id"] = broker_deal_id
        if payload:
            self.orders[order_id]["payload"] = payload


class _DurableDatabase(_Database):
    def __init__(self) -> None:
        self.repository = _DurableRepository()


class _AtomicDurableRepository(_DurableRepository):
    async def claim_order_intent(
        self, key: str, request_hash: str, order_id: str, order: dict[str, object]
    ):
        response = await self.claim_idempotency(key, request_hash)
        if response is not None:
            return response
        await self.create_order_intent(order_id, key, order)
        return None


class _AtomicDurableDatabase(_Database):
    def __init__(self) -> None:
        self.repository = _AtomicDurableRepository()


class _Workflow:
    class config:
        magic = 77

    def __init__(self) -> None:
        self.calls = 0

    def execute_market_order(self, *args: object, **kwargs: object) -> dict[str, int]:
        self.calls += 1
        return {"ticket": 1}


class _AcceptedWorkflow(_Workflow):
    def execute_market_order(self, *args: object, **kwargs: object):
        self.calls += 1
        return type(
            "BrokerResult",
            (),
            {"retcode": 10009, "order": 123, "deal": 456, "comment": "done"},
        )()


class _IncompleteAcceptedWorkflow(_Workflow):
    def execute_market_order(self, *args: object, **kwargs: object):
        self.calls += 1
        return type(
            "BrokerResult",
            (),
            {"retcode": 10009, "order": 0, "deal": None, "comment": "done"},
        )()


class _FailingWorkflow(_Workflow):
    def execute_market_order(self, *args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("broker outcome unavailable")


class _AmbiguousWorkflow(_Workflow):
    def execute_market_order(self, *args: object, **kwargs: object) -> dict[str, int]:
        self.calls += 1
        raise AmbiguousOrderOutcome(
            "execution_result_unknown", "broker response was lost"
        )


class _RejectingWorkflow(_Workflow):
    def execute_market_order(self, *args: object, **kwargs: object) -> dict[str, int]:
        self.calls += 1
        from src.python.execution.live_order_workflow import LiveOrderRejected

        raise LiveOrderRejected(
            "order_rejected", "broker rejected order", retcode=10030
        )


class _NonNumericIdentifier:
    def __int__(self):
        raise AssertionError("non-numeric broker identifier was coerced")


def test_non_numeric_broker_identifier_remains_unknown_without_coercion():
    adapter = LiveOrderWorkflow(
        SimpleNamespace(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
        ),
    )
    mt5 = SimpleNamespace(
        order_check=lambda _: SimpleNamespace(retcode=0),
        order_send=lambda _: SimpleNamespace(
            retcode=10009,
            order=_NonNumericIdentifier(),
            deal=456,
        ),
    )

    with pytest.raises(AmbiguousOrderOutcome) as error:
        adapter._send_checked(mt5, {}, require_identifiers=True)

    assert error.value.code == "execution_result_unknown"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "signal_id": "signal-1",
        "symbol": "XAUUSD",
        "action": "buy",
        "volume": 0.1,
        "stop_loss": 2490,
        "take_profit": 2520,
        "dry_run": False,
        "confirmation_token": "confirmation",
        "idempotency_key": "request-1",
    }
    payload.update(overrides)
    return payload


def test_live_execution_requires_authentication(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    app = create_app(
        database=_Database(),
        live_workflow=_Workflow(),
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/signals/signal-1/execute", json=_payload())
    assert response.status_code == 401


def test_health_exposes_request_and_correlation_ids():
    app = create_app(database=_Database())
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "request-test-1"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-test-1"
    assert response.headers["X-Correlation-ID"]
    assert response.json()["request_id"] == "request-test-1"
    assert response.json()["correlation_id"] == response.headers["X-Correlation-ID"]


def test_live_execution_uses_workflow_and_deduplicates(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    workflow = _Workflow()
    app = create_app(
        database=_DurableDatabase(),
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        headers = {"X-API-Key": "secret"}
        first = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
        duplicate = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
    assert first.status_code == duplicate.status_code == 202
    assert workflow.calls == 1


def test_live_execution_propagates_request_correlation_to_order_intent(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _DurableDatabase()
    app = create_app(
        database=database,
        live_workflow=_Workflow(),
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers={"X-API-Key": "secret", "X-Request-ID": "request-correlation-1"},
        )

    assert response.status_code == 202
    assert (
        database.repository.orders["signal:signal-1:idempotency:request-1"][
            "correlation_id"
        ]
        == response.headers["X-Correlation-ID"]
    )
    assert database.repository.transition_correlations == [
        response.headers["X-Correlation-ID"]
    ]


def test_accepted_execution_persists_broker_identifiers(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    app = create_app(
        database=database,
        live_workflow=_AcceptedWorkflow(),
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers={"X-API-Key": "secret"},
        )

    assert response.status_code == 202
    order = database.repository.orders["signal:signal-1:idempotency:request-1"]
    assert order["broker_order_id"] == "123"
    assert order["broker_deal_id"] == "456"


def test_incomplete_accepted_execution_is_unknown_and_not_retried(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    workflow = _IncompleteAcceptedWorkflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    headers = {"X-API-Key": "secret"}
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
        duplicate = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )

    assert first.status_code == 503
    assert first.json()["detail"] == "execution_result_unknown"
    assert duplicate.status_code == 202
    assert duplicate.json()["data"]["mode"] == "unknown"
    assert workflow.calls == 1
    assert (
        database.repository.orders["signal:signal-1:idempotency:request-1"]["status"]
        == "unknown"
    )


def test_invalid_magic_does_not_claim_idempotency(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    workflow = _AcceptedWorkflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    headers = {"X-API-Key": "secret"}
    with TestClient(app) as client:
        invalid = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=999),
            headers=headers,
        )
        corrected = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )

    assert invalid.status_code == 409
    assert corrected.status_code == 202
    assert workflow.calls == 1


def test_missing_confirmation_does_not_claim_idempotency(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    workflow = _AcceptedWorkflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    headers = {"X-API-Key": "secret"}
    with TestClient(app) as client:
        invalid = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77, confirmation_token=""),
            headers=headers,
        )
        corrected = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )

    assert invalid.status_code == 409
    assert corrected.status_code == 202
    assert workflow.calls == 1


def test_unknown_execution_transition_preserves_request_correlation(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _DurableDatabase()
    app = create_app(
        database=database,
        live_workflow=_FailingWorkflow(),
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers={"X-API-Key": "secret", "X-Request-ID": "request-unknown-1"},
        )

    assert response.status_code == 503
    assert database.repository.transition_correlations == [
        response.headers["X-Correlation-ID"]
    ]


def test_ambiguous_execution_is_durable_and_not_retried(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    workflow = _AmbiguousWorkflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    headers = {"X-API-Key": "secret"}
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
        duplicate = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )

    assert first.status_code == 503
    assert first.json()["detail"] == "execution_result_unknown"
    assert duplicate.status_code == 202
    assert duplicate.json()["data"]["mode"] == "unknown"
    assert workflow.calls == 1
    assert (
        database.repository.orders["signal:signal-1:idempotency:request-1"]["status"]
        == "unknown"
    )


def test_rejected_execution_is_durable_and_not_retried(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _AtomicDurableDatabase()
    workflow = _RejectingWorkflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    headers = {"X-API-Key": "secret"}
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
        duplicate = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )

    assert first.status_code == 409
    assert duplicate.status_code == 202
    assert duplicate.json()["data"]["accepted"] is False
    assert duplicate.json()["data"]["retcode"] == 10030
    assert workflow.calls == 1
    assert (
        database.repository.orders["signal:signal-1:idempotency:request-1"]["status"]
        == "rejected"
    )


def test_live_execution_rejects_client_magic_override(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    app = create_app(
        database=_Database(),
        live_workflow=_Workflow(),
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=999),
            headers={"X-API-Key": "secret"},
        )
    assert response.status_code == 409


def test_live_execution_reuses_durable_response_after_process_cache_clear(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "secret")
    database = _DurableDatabase()
    workflow = _Workflow()
    app = create_app(
        database=database,
        live_workflow=workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(app) as client:
        headers = {"X-API-Key": "secret"}
        first = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
    restarted_workflow = _Workflow()
    restarted_app = create_app(
        database=database,
        live_workflow=restarted_workflow,
        allowed_symbols=frozenset({"XAUUSD"}),
    )
    with TestClient(restarted_app) as client:
        duplicate = client.post(
            "/api/v1/signals/signal-1/execute",
            json=_payload(magic=77),
            headers=headers,
        )
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["data"] == duplicate.json()["data"]
    assert workflow.calls == 1
    assert restarted_workflow.calls == 0


def test_live_execution_accepts_short_lived_bearer_token(monkeypatch):
    monkeypatch.setenv("API_AUTH_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_AUTH_TOKEN_TTL_SECONDS", "300")
    token_service = AuthenticationService()
    issued_at = int(time.time())
    token = token_service.issue_token(
        subject="execution-service",
        roles={SIGNAL_EXECUTION},
        token_id="test-token",
        now=issued_at,
    )
    client = TestClient(
        create_app(
            database=_Database(),
            allowed_symbols=frozenset({"XAUUSD"}),
        )
    )
    response = client.post(
        "/api/v1/signals/s-3/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "signal_id": "s-3",
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.01,
            "stop_loss": 2300,
            "take_profit": 2400,
            "dry_run": False,
            "idempotency_key": "bearer-request-1",
        },
    )
    assert response.status_code == 409


def test_read_only_token_cannot_execute_live_order(monkeypatch):
    monkeypatch.setenv("API_AUTH_READ_TOKEN", "read-secret")
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))
    response = client.post(
        "/api/v1/signals/s-4/execute",
        headers={"X-API-Key": "read-secret"},
        json={
            "signal_id": "s-4",
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.01,
            "stop_loss": 2300,
            "take_profit": 2400,
            "dry_run": False,
        },
    )
    assert response.status_code == 403


def test_order_review_token_cannot_execute_or_reconcile(monkeypatch):
    monkeypatch.setenv("API_AUTH_ORDER_REVIEW_TOKEN", "order-review-secret")
    client = TestClient(
        create_app(database=_Database(), allowed_symbols=frozenset({"XAUUSD"}))
    )
    headers = {"X-API-Key": "order-review-secret"}

    execute_response = client.post(
        "/api/v1/signals/s-5/execute",
        headers=headers,
        json={
            "signal_id": "s-5",
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.01,
            "stop_loss": 2300,
            "take_profit": 2400,
            "dry_run": False,
            "idempotency_key": "order-review-execute",
        },
    )
    reconcile_response = client.post(
        "/api/v1/orders/order-1/reconcile",
        headers=headers,
        json={"resolution": "accepted"},
    )

    assert execute_response.status_code == 403
    assert reconcile_response.status_code == 403
