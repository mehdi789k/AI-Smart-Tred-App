from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.python.api.app import create_app


class FakeGateway:
    def __init__(self):
        self.calls = []

    def request(self, message_type, payload):
        self.calls.append((message_type, payload))
        return {"accepted": True, "code": "filled"}


def test_health_and_dry_run_are_safe_by_default():
    gateway = FakeGateway()
    client = TestClient(
        create_app(gateway=gateway, allowed_symbols=frozenset({"XAUUSD"}))
    )

    assert client.get("/health").json()["data"]["status"] == "ok"
    assert client.get("/health").json()["data"]["schema_version"] == "0010"
    response = client.post(
        "/api/v1/signals/s-1/execute",
        json={
            "signal_id": "s-1",
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.01,
            "stop_loss": 2300,
            "take_profit": 2400,
            "magic": 7,
        },
    )
    assert response.status_code == 202
    assert response.json()["data"]["mode"] == "dry_run"
    assert gateway.calls == []


def test_readiness_and_metrics_are_available(monkeypatch):
    monkeypatch.delenv("OBS_METRICS_TOKEN", raising=False)
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))

    readiness = client.get("/ready")
    metrics = client.get("/metrics")

    assert readiness.status_code == 200
    assert readiness.json()["data"]["dependencies"]["database"] == "ready"
    assert readiness.json()["data"]["dependencies"]["mt5"] == "unavailable"
    assert metrics.status_code == 200
    assert "http_requests_total" in metrics.text


def test_production_startup_verifies_schema_without_creating_it():
    class ProductionDatabase:
        url = "postgresql+asyncpg://database.example/mt5"
        repository = None

        def __init__(self):
            self.calls = []

        async def verify_schema(self):
            self.calls.append("verify_schema")

        async def initialize_for_tests(self):
            raise AssertionError("production startup must not initialize schema")

        async def dispose(self):
            self.calls.append("dispose")

    database = ProductionDatabase()
    with TestClient(
        create_app(database=database, allowed_symbols=frozenset({"XAUUSD"}))
    ):
        pass

    assert database.calls == ["verify_schema", "dispose"]


def test_readiness_reports_mt5_and_circuit_breaker_without_sending_orders():
    class FakeMT5:
        def is_connected(self):
            return True

    class FakeCircuitBreaker:
        is_tripped = True

    client = TestClient(
        create_app(
            allowed_symbols=frozenset({"XAUUSD"}),
            mt5_connection=FakeMT5(),
            circuit_breaker=FakeCircuitBreaker(),
        )
    )

    response = client.get("/ready")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dependencies"]["mt5"] == "ready"
    assert data["dependencies"]["circuit_breaker"] == "tripped"
    assert data["trading"]["allowed"] is False


def test_readiness_reports_mt5_failure_as_trading_unavailable():
    class DisconnectedMT5:
        def is_connected(self):
            raise RuntimeError("terminal unavailable")

    client = TestClient(
        create_app(
            allowed_symbols=frozenset({"XAUUSD"}),
            mt5_connection=DisconnectedMT5(),
        )
    )

    response = client.get("/ready")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dependencies"]["mt5"] == "unavailable"
    assert data["trading"]["allowed"] is False


def test_metrics_requires_internal_token_when_configured(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_TOKEN", "metrics-secret")
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))

    assert client.get("/metrics").status_code == 401
    assert (
        client.get("/metrics", headers={"X-API-Key": "metrics-secret"}).status_code
        == 200
    )


def test_execution_rejects_non_whitelisted_symbol_before_auth_and_requires_workflow(
    monkeypatch,
):
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    gateway = FakeGateway()
    client = TestClient(
        create_app(gateway=gateway, allowed_symbols=frozenset({"XAUUSD"}))
    )
    payload = {
        "signal_id": "s-2",
        "symbol": "EURUSD",
        "action": "sell",
        "volume": 0.01,
        "stop_loss": 2400,
        "take_profit": 2300,
        "magic": 7,
        "dry_run": False,
    }
    assert client.post("/api/v1/signals/s-2/execute", json=payload).status_code == 409

    payload["symbol"] = "XAUUSD"
    response = client.post("/api/v1/signals/s-2/execute", json=payload)
    # Live execution must not fall back to the injected gateway: production
    # orders are only valid through LiveOrderWorkflow/risk gates.
    assert response.status_code == 401
    assert gateway.calls == []


def test_execution_records_stale_signal_metric(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.delenv("OBS_METRICS_TOKEN", raising=False)
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))
    stale = datetime.now(timezone.utc) - timedelta(minutes=11)
    response = client.post(
        "/api/v1/signals/stale-1/execute",
        json={
            "signal_id": "stale-1",
            "symbol": "XAUUSD",
            "action": "buy",
            "volume": 0.01,
            "stop_loss": 2300,
            "take_profit": 2400,
            "created_at": stale.isoformat(),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "signal_stale"
    metrics = client.get("/metrics").text
    assert "signal_stale_total" in metrics


def test_ohlc_requires_explicit_credentials(monkeypatch):
    for variable in (
        "API_AUTH_TOKEN",
        "API_AUTH_READ_TOKEN",
        "API_AUTH_EXECUTION_TOKEN",
        "API_AUTH_ORDER_REVIEW_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))

    response = client.get("/api/v1/data/ohlc?symbol=XAUUSD&timeframe=M5")

    assert response.status_code == 401


def test_anonymous_unknown_orders_returns_401_without_reading_repository(monkeypatch):
    monkeypatch.delenv("API_AUTH_ORDER_REVIEW_TOKEN", raising=False)

    class TrackingRepository:
        def __init__(self):
            self.reads = 0

        async def get_unknown_orders(self, *, limit):
            self.reads += 1
            return []

    class Database:
        url = "sqlite"

        def __init__(self):
            self.repository = TrackingRepository()

        async def initialize(self, **_):
            return None

        async def dispose(self):
            return None

    database = Database()
    client = TestClient(
        create_app(database=database, allowed_symbols=frozenset({"XAUUSD"}))
    )

    response = client.get("/api/v1/orders/unknown")

    assert response.status_code == 401
    assert database.repository.reads == 0


def test_order_review_token_can_read_unknown_orders(monkeypatch):
    monkeypatch.setenv("API_AUTH_ORDER_REVIEW_TOKEN", "order-review-key")

    class Repository:
        async def get_unknown_orders(self, *, limit):
            assert limit == 100
            return [
                SimpleNamespace(
                    order_id="order-unknown",
                    idempotency_key="request-unknown",
                    symbol="XAUUSD",
                    side="buy",
                    quantity=0.1,
                    status="unknown",
                    payload={"reason": "execution_result_unknown"},
                    updated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
                )
            ]

    class Database:
        url = "sqlite"
        repository = Repository()

        async def initialize(self, **_):
            return None

        async def dispose(self):
            return None

    client = TestClient(
        create_app(
            database=Database(),
            allowed_symbols=frozenset({"XAUUSD"}),
        )
    )

    response = client.get(
        "/api/v1/orders/unknown",
        headers={"X-API-Key": "order-review-key"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "order_id": "order-unknown",
            "idempotency_key": "request-unknown",
            "symbol": "XAUUSD",
            "side": "buy",
            "quantity": 0.1,
            "status": "unknown",
            "payload": {"reason": "execution_result_unknown"},
            "updated_at": "2026-09-16T00:00:00+00:00",
        }
    ]


def test_unknown_orders_requires_order_review_role(monkeypatch):
    monkeypatch.setenv("API_AUTH_READ_TOKEN", "read-only-key")
    monkeypatch.delenv("API_AUTH_ORDER_REVIEW_TOKEN", raising=False)
    client = TestClient(create_app(allowed_symbols=frozenset({"XAUUSD"})))

    response = client.get(
        "/api/v1/orders/unknown",
        headers={"X-API-Key": "read-only-key"},
    )

    assert response.status_code == 403
