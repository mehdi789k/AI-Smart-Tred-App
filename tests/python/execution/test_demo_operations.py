from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.python.api import zmq_gateway
from src.python.api.app import create_app
from src.python.api.zmq_gateway import GatewayUnavailable, MQL5ExecutionGateway
from src.python.execution.live_order_workflow import (
    DemoActivationConfig,
    LiveOrderConfig,
    LiveOrderRejected,
    LiveOrderWorkflow,
)
from src.python.execution.shadow import ShadowOrderLedger
from src.python.execution.trading import Order, OrderType, TradingExecution


class ReplaySocket:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def setsockopt(self, *_args: object) -> None:
        return None

    def connect(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def send_json(self, value: dict[str, object]) -> None:
        self.sent.append(value)

    def recv_json(self) -> object:
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, dict) and response.get("schema_version") == "1.0":
            response = {
                "schema_version": "1.0",
                "message_id": "mt5-response",
                "message_type": "order_result",
                "sent_at": "2026-09-16T00:00:00+00:00",
                "correlation_id": self.sent[-1]["correlation_id"],
                "source": "mt5",
                "payload": {
                    key: value
                    for key, value in response.items()
                    if key != "schema_version"
                },
            }
        return response

    def close(self, linger: int = 0) -> None:
        self.closed = True


class ReplayContext:
    def __init__(self, sockets: list[ReplaySocket]) -> None:
        self.sockets = iter(sockets)
        self.created: list[ReplaySocket] = []

    def socket(self, _socket_type: int) -> ReplaySocket:
        socket = next(self.sockets)
        self.created.append(socket)
        return socket


class ReplayZMQ:
    REQ = 3
    RCVTIMEO = 27
    SNDTIMEO = 28


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(
        self, message_type: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((message_type, payload))
        return {"accepted": True, "code": "filled"}

    def close(self) -> None:
        return None


def test_zero_mq_replay_preserves_envelope_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = ReplaySocket(
        [
            {
                "schema_version": "1.0",
                "status": "accepted",
                "accepted": True,
                "code": "heartbeat",
                "message": "alive",
            }
        ]
    )
    context = ReplayContext([socket])
    ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", ReplayZMQ)

    response = MQL5ExecutionGateway().request("heartbeat", {"symbol": "XAUUSD"})

    assert response["payload"]["accepted"] is True
    assert socket.sent[0]["schema_version"] == "1.0"
    assert socket.sent[0]["source"] == "python"
    assert socket.sent[0]["message_type"] == "heartbeat"


def test_real_zero_mq_transport_round_trip_is_fail_closed(monkeypatch):
    """Exercise the real REQ/REP seam without contacting MT5 or a broker."""

    real_zmq = pytest.importorskip("zmq")
    context = real_zmq.Context()
    server = context.socket(real_zmq.REP)
    endpoint = f"tcp://127.0.0.1:{server.bind_to_random_port('tcp://127.0.0.1')}"
    received: list[dict[str, object]] = []

    def serve_once() -> None:
        try:
            request = server.recv_json()
            received.append(request)
            server.send_json(
                {
                    "schema_version": "1.0",
                    "message_id": "stub-response",
                    "message_type": "order_result",
                    "sent_at": "2026-09-18T00:00:00+00:00",
                    "correlation_id": request["correlation_id"],
                    "source": "mt5",
                    "payload": {
                        "status": "rejected",
                        "accepted": False,
                        "code": "dry_run",
                    },
                }
            )
        finally:
            server.close(linger=0)

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    monkeypatch.setattr(zmq_gateway, "zmq", real_zmq)
    gateway = MQL5ExecutionGateway(endpoint=endpoint, timeout_ms=1_000)
    try:
        response = gateway.request(
            "order_request",
            {"symbol": "XAUUSD", "volume": 0.01, "dry_run": True},
        )
    finally:
        gateway.close()
        thread.join(timeout=2)
        context.term()

    assert response["payload"]["status"] == "rejected"
    assert response["payload"]["accepted"] is False
    assert received[0]["source"] == "python"
    assert received[0]["message_type"] == "order_request"
    assert received[0]["correlation_id"] == response["correlation_id"]


def test_zero_mq_timeout_closes_socket_and_explicit_reconnect_uses_fresh_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timed_out = ReplaySocket([TimeoutError("simulated timeout")])
    recovered = ReplaySocket(
        [
            {
                "schema_version": "1.0",
                "status": "accepted",
                "accepted": True,
                "code": "heartbeat",
                "message": "alive",
            }
        ]
    )
    context = ReplayContext([timed_out, recovered])
    ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", ReplayZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(GatewayUnavailable, match="did not acknowledge"):
        gateway.request("heartbeat", {})

    assert timed_out.closed is False
    gateway.reconnect()
    assert gateway.request("heartbeat", {})["payload"]["accepted"] is True
    assert context.created == [timed_out, recovered]


def test_zero_mq_duplicate_response_is_exposed_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = ReplaySocket(
        [
            {
                "schema_version": "1.0",
                "status": "rejected",
                "accepted": False,
                "code": "duplicate_message",
            }
        ]
    )
    context = ReplayContext([socket])
    ReplayZMQ.Context = SimpleNamespace(instance=lambda: context)
    monkeypatch.setattr(zmq_gateway, "zmq", ReplayZMQ)

    response = MQL5ExecutionGateway().request(
        "order_request", {"message_id": "replayed"}
    )

    assert response["payload"] == {
        "status": "rejected",
        "accepted": False,
        "code": "duplicate_message",
    }
    assert response["message_type"] == "order_result"
    assert len(socket.sent) == 1


def test_api_idempotency_returns_cached_result_without_second_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    gateway = FakeGateway()
    payload = {
        "signal_id": "demo-idempotent",
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
            "/api/v1/signals/demo-idempotent/execute",
            headers={"Idempotency-Key": "demo-request-1"},
            json=payload,
        )
        second = client.post(
            "/api/v1/signals/demo-idempotent/execute",
            headers={"Idempotency-Key": "demo-request-1"},
            json=payload,
        )

    assert first.status_code == second.status_code == 202
    assert first.json()["data"] == second.json()["data"]
    assert gateway.calls == []


def test_shadow_mode_never_calls_gateway_and_evaluates_exit(tmp_path: Path) -> None:
    ledger = ShadowOrderLedger(tmp_path / "shadow.jsonl")
    execution = TradingExecution(mode="shadow", shadow_ledger=ledger)
    order = Order("XAUUSD", "BUY", OrderType.MARKET, 0.1)

    opened = execution.execute_order(order, current_price=2000.0)
    closed = execution.execute_order(
        Order(
            "XAUUSD",
            "SELL",
            OrderType.MARKET,
            0.1,
            metadata={
                "action": "close_position",
                "shadow_order_id": order.order_id,
                "reason": "replay_tp",
            },
        ),
        current_price=2005.0,
    )

    assert opened.success and closed.success
    assert ledger.get(order.order_id).status == "evaluated"
    assert ledger.get(order.order_id).exit_reason == "replay_tp"


def test_shadow_summary_reports_evaluated_and_open_orders(tmp_path: Path) -> None:
    ledger = ShadowOrderLedger(tmp_path / "shadow.jsonl")
    execution = TradingExecution(mode="shadow", shadow_ledger=ledger)
    order = Order("EURUSD", "BUY", OrderType.MARKET, 1.0)

    execution.execute_order(order, current_price=100.0)
    execution.execute_order(
        Order(
            "EURUSD",
            "SELL",
            OrderType.MARKET,
            1.0,
            metadata={
                "action": "close_position",
                "shadow_order_id": order.order_id,
                "reason": "TP",
            },
        ),
        current_price=103.0,
    )

    summary = ledger.summary()

    assert summary["total_orders"] == 1
    assert summary["evaluated_orders"] == 1
    assert summary["open_orders"] == 0
    assert summary["winning_orders"] == 1
    assert summary["total_pnl"] == pytest.approx(2.96)


def test_shadow_rejects_order_outside_allowed_symbols(tmp_path: Path) -> None:
    execution = TradingExecution(
        mode="shadow",
        shadow_ledger=ShadowOrderLedger(tmp_path / "shadow.jsonl"),
        allowed_symbols=frozenset({"XAUUSD"}),
    )

    report = execution.execute_order(
        Order("EURUSD", "BUY", OrderType.MARKET, 0.1),
        current_price=100.0,
    )

    assert not report.success
    assert report.message == "Symbol EURUSD is not allowed"


def test_shadow_readiness_rejects_open_orders_and_accepts_complete_audit(
    tmp_path: Path,
) -> None:
    ledger = ShadowOrderLedger(tmp_path / "shadow.jsonl")
    order = Order("XAUUSD", "BUY", OrderType.MARKET, 0.01)
    TradingExecution(mode="shadow", shadow_ledger=ledger).execute_order(
        order, current_price=2000.0
    )
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        '{"event":"demo_activated","status":"approved"}\n',
        encoding="utf-8",
    )

    report = ledger.validate_for_demo(audit_path=audit_path)

    assert report["ready"] is False
    assert "open_shadow_orders" in report["reasons"]

    ledger.record_close(order.order_id, 2001.0, 0.99, "TP")
    report = ledger.validate_for_demo(audit_path=audit_path)

    assert report["ready"] is True
    assert report["reasons"] == []


def test_demo_confirmation_is_one_time_and_expires() -> None:
    class Connector:
        _mt5 = SimpleNamespace()

        def is_connected(self) -> bool:
            return True

        def get_history(self, days: int = 1) -> pd.DataFrame:
            return pd.DataFrame()

    workflow = LiveOrderWorkflow(
        Connector(),
        LiveOrderConfig(
            frozenset({"XAUUSD"}),
            magic=7,
            max_position_volume=0.1,
            max_daily_loss=100,
            confirmation_ttl_seconds=1,
            demo_activation=DemoActivationConfig(
                enabled=True,
                max_trade_volume=0.1,
                max_trades_per_session=1,
                max_daily_loss=100,
            ),
        ),
    )
    token = workflow.request_confirmation("open:XAUUSD:BUY:0.1:0:0")
    workflow._pending_confirmation = (workflow._pending_confirmation[0], 0.0)

    with pytest.raises(LiveOrderRejected) as error:
        workflow.execute_market_order(
            "XAUUSD",
            "BUY",
            0.1,
            magic=7,
            confirmation_token=token,
        )

    assert error.value.code == "confirmation_invalid"
