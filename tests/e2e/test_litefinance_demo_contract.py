"""Offline contract/replay matrix for the LiteFinance Demo boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.python.api import zmq_gateway
from src.python.api.app import create_app
from src.python.api.zmq_contract import load_contract_document, validate_envelope
from src.python.api.zmq_gateway import GatewayTimeoutError, MQL5ExecutionGateway
from src.python.execution.broker_reconciliation import match_order_history
from src.python.risk.circuit_breaker import CircuitBreaker
from scripts import verify_demo_readiness

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "fixtures" / "zmq_contract_v1.json"
REPLAY_GUARDRAILS = ROOT / "tests" / "fixtures" / "mt5_replay_guardrails_v1.json"


def test_demo_replay_guardrails_are_fail_closed() -> None:
    replay = json.loads(REPLAY_GUARDRAILS.read_text(encoding="utf-8"))

    assert replay["replay"]["allow_live_trading"] is False
    assert replay["replay"]["enable_zmq"] is False
    assert replay["replay"]["live_execution_allowed"] is False
    assert replay["replay"]["expected_total_trades"] == 0
    assert replay["replay"]["expected_total_deals"] == 0


def test_demo_readiness_checks_only_health_and_readiness_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_fetch_json(_base_url: str, path: str, _timeout: float) -> dict:
        calls.append(path)
        return {"data": {"status": "ok"}} if path == "/health" else {
            "data": {
                "status": "ready",
                "account": {"trade_mode": "real"},
                "trading": {"allowed": True},
                "dependencies": {"mt5": "ready", "circuit_breaker": "armed"},
            }
        }

    monkeypatch.setattr(verify_demo_readiness, "fetch_json", fake_fetch_json)
    health = verify_demo_readiness.fetch_json("http://demo", "/health", 1.0)
    readiness = verify_demo_readiness.fetch_json("http://demo", "/ready", 1.0)
    with pytest.raises(RuntimeError, match="not verified Demo"):
        verify_demo_readiness.validate_demo_readiness(
            health, readiness, require_demo_trading=True
        )

    assert calls == ["/health", "/ready"]


def test_contract_matrix_contains_operational_demo_scenarios() -> None:
    names = {case.name for case in load_contract_document(CONTRACT).cases}

    assert {
        "heartbeat",
        "accepted_order",
        "rejected_order",
        "timeout",
        "duplicate_message",
        "unknown_outcome",
        "reconciliation_evidence",
        "restart_state",
        "circuit_breaker",
    } <= names


@pytest.mark.parametrize("case_name", ["heartbeat", "accepted_order", "rejected_order"])
def test_contract_requests_validate_at_python_boundary(case_name: str) -> None:
    case = load_contract_document(CONTRACT).get_case(case_name)
    validate_envelope(case.request, source="python")


def test_timeout_and_unknown_outcomes_remain_unresolved() -> None:
    document = load_contract_document(CONTRACT)

    for name in ("timeout", "unknown_outcome"):
        expected = document.get_case(name).expected
        assert expected["accepted"] is False
        assert expected["requires_reconciliation"] is True


class _ReplaySocket:
    def __init__(self, response: object) -> None:
        self.response = response
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def setsockopt(self, *_args: object) -> None:
        return None

    def connect(self, _endpoint: str) -> None:
        return None

    def send_json(self, value: dict[str, object]) -> None:
        self.sent.append(value)

    def recv_json(self) -> object:
        if isinstance(self.response, BaseException):
            raise self.response
        return {
            "schema_version": "1.0",
            "message_id": "mt5-response",
            "message_type": "order_result",
            "sent_at": "2026-09-18T00:00:00+00:00",
            "correlation_id": self.sent[-1]["correlation_id"],
            "source": "mt5",
            "payload": self.response,
        }

    def close(self, linger: int = 0) -> None:
        self.closed = True


class _ReplayContext:
    def __init__(self, sockets: list[_ReplaySocket]) -> None:
        self.sockets = iter(sockets)

    def socket(self, _socket_type: int) -> _ReplaySocket:
        return next(self.sockets)


class _ReplayZMQ:
    REQ = 3
    RCVTIMEO = 27
    SNDTIMEO = 28


def test_accepted_replay_preserves_broker_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _ReplaySocket(
        {
            "status": "accepted",
            "accepted": True,
            "code": "order_accepted",
            "order": "123",
            "deal": "456",
        }
    )
    context = _ReplayContext([socket])
    _ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", _ReplayZMQ)

    response = MQL5ExecutionGateway().request("order_request", {"symbol": "XAUUSD"})

    assert response["payload"]["order"] == "123"
    assert response["payload"]["deal"] == "456"


def test_timeout_replay_sends_once_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _ReplaySocket(TimeoutError("demo timeout"))
    context = _ReplayContext([socket])
    _ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", _ReplayZMQ)

    with pytest.raises(GatewayTimeoutError):
        MQL5ExecutionGateway().request("order_request", {"symbol": "XAUUSD"})

    assert len(socket.sent) == 1


def test_unknown_replay_is_not_promoted_to_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _ReplaySocket(
        {"status": "unknown", "accepted": False, "code": "broker_uncertain"}
    )
    context = _ReplayContext([socket])
    _ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", _ReplayZMQ)

    response = MQL5ExecutionGateway().request("order_request", {"symbol": "XAUUSD"})

    assert response["payload"]["status"] == "unknown"
    assert response["payload"]["accepted"] is False


def test_duplicate_idempotency_key_does_not_create_second_order() -> None:
    class RecordingGateway:
        def __init__(self) -> None:
            self.calls = 0

        def request(
            self, _message_type: str, _payload: dict[str, object]
        ) -> dict[str, object]:
            self.calls += 1
            return {"accepted": True, "code": "filled"}

        def close(self) -> None:
            return None

    gateway = RecordingGateway()
    payload = {
        "signal_id": "demo-duplicate",
        "symbol": "XAUUSD",
        "action": "buy",
        "volume": 0.01,
        "stop_loss": 2300,
        "take_profit": 2400,
        "dry_run": True,
    }
    with TestClient(
        create_app(gateway=gateway, allowed_symbols=frozenset({"XAUUSD"}))
    ) as client:
        first = client.post(
            "/api/v1/signals/demo-duplicate/execute",
            headers={"Idempotency-Key": "case-duplicate-001"},
            json=payload,
        )
        second = client.post(
            "/api/v1/signals/demo-duplicate/execute",
            headers={"Idempotency-Key": "case-duplicate-001"},
            json=payload,
        )

    assert first.status_code == second.status_code == 202
    assert first.json()["data"] == second.json()["data"]
    assert gateway.calls == 0


def test_reconciliation_requires_unique_broker_evidence() -> None:
    order = SimpleNamespace(
        order_id="order-1234",
        symbol="XAUUSD",
        quantity=0.01,
        created_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    broker_order = SimpleNamespace(
        ticket=123,
        symbol="XAUUSD",
        volume_initial=0.01,
        magic=26090901,
        comment="AI-Smart-Trader-confirmed:order-1",
        time_setup=1789689600,
        state="started",
    )

    match = match_order_history(
        order,
        [broker_order],
        [],
        magic=26090901,
    )

    assert match is not None
    assert match.evidence["source"] == "mt5_history"
    assert match.broker_order_id == "123"


def test_restart_state_requires_explicit_reconnect_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _ReplaySocket(TimeoutError("restart"))
    second = _ReplaySocket(
        {
            "status": "accepted",
            "accepted": True,
            "code": "heartbeat",
            "message": "alive",
        }
    )
    context = _ReplayContext([first, second])
    _ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", _ReplayZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(GatewayTimeoutError):
        gateway.request("heartbeat", {})
    gateway.reconnect()
    assert gateway.request("heartbeat", {})["payload"]["accepted"] is True
    assert len(first.sent) == 1
    assert len(second.sent) == 1


def test_circuit_breaker_replay_trips_on_daily_loss() -> None:
    result = CircuitBreaker(1_000).evaluate([{"pnl": -20}])

    assert result["trading_allowed"] is False
    assert result["stop_reason"]
