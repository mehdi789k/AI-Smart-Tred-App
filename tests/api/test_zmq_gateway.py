from pathlib import Path

import pytest

from src.python.api import zmq_gateway
from src.python.api.zmq_contract import load_contract_document
from src.python.api.zmq_gateway import (
    GatewayProtocolError,
    GatewayTimeoutError,
    GatewayTransportError,
    GatewayUnavailable,
    MQL5ExecutionGateway,
)
from src.python.logging_config import bind_request_context
from src.python.observability import MetricsRegistry


class FakeSocket:
    def __init__(self):
        self.sent = None
        self.send_count = 0
        self.closed = False

    def setsockopt(self, *_args):
        return None

    def connect(self, endpoint):
        self.endpoint = endpoint

    def send_json(self, value):
        self.sent = value
        self.send_count += 1

    def recv_json(self):
        return {
            "schema_version": "1.0",
            "message_id": "mt5-response",
            "message_type": "order_result",
            "sent_at": "2026-09-16T00:00:00+00:00",
            "correlation_id": self.sent["correlation_id"],
            "source": "mt5",
            "payload": {
                "status": "accepted",
                "accepted": True,
                "code": "heartbeat",
                "message": "alive",
            },
        }

    def close(self, linger=0):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.socket_instance = FakeSocket()

    def socket(self, _socket_type):
        return self.socket_instance


class FakeZMQ:
    REQ = 3
    RCVTIMEO = 27
    SNDTIMEO = 28

    class Context:
        instance = staticmethod(lambda: FakeContext())

    class error:
        class Again(Exception):
            pass

        class ZMQError(Exception):
            pass


def test_gateway_builds_versioned_envelope(monkeypatch):
    monkeypatch.setattr(zmq_gateway, "zmq", FakeZMQ)
    gateway = MQL5ExecutionGateway()
    bind_request_context("request-1", "correlation-1")
    response = gateway.request("heartbeat", {"symbol": "XAUUSD"})

    assert response["payload"]["accepted"] is True
    assert gateway._socket.sent["schema_version"] == "1.0"
    assert gateway._socket.sent["source"] == "python"
    assert gateway._socket.sent["message_type"] == "heartbeat"
    assert gateway._socket.sent["payload"] == {"symbol": "XAUUSD"}
    assert gateway._socket.sent["correlation_id"] == "correlation-1"


def test_gateway_rejects_heartbeat_without_heartbeat_code(monkeypatch):
    class HeartbeatCodeMismatchSocket(FakeSocket):
        def recv_json(self):
            response = super().recv_json()
            response["payload"] = {
                "status": "accepted",
                "accepted": True,
                "code": "order_accepted",
                "message": "ok",
            }
            return response

    class HeartbeatCodeMismatchContext(FakeContext):
        def __init__(self):
            self.socket_instance = HeartbeatCodeMismatchSocket()

    class HeartbeatCodeMismatchZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: HeartbeatCodeMismatchContext())

    monkeypatch.setattr(zmq_gateway, "zmq", HeartbeatCodeMismatchZMQ)

    with pytest.raises(GatewayProtocolError, match="invalid MT5"):
        MQL5ExecutionGateway().request("heartbeat", {"symbol": "XAUUSD"})


def test_gateway_rejects_invalid_endpoint_and_protocol(monkeypatch):
    with pytest.raises(ValueError):
        MQL5ExecutionGateway("http://localhost:5555")

    class InvalidSocket(FakeSocket):
        def recv_json(self):
            return {"schema_version": "2"}

    class InvalidContext(FakeContext):
        def __init__(self):
            self.socket_instance = InvalidSocket()

    class InvalidZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: InvalidContext())

    monkeypatch.setattr(zmq_gateway, "zmq", InvalidZMQ)
    with pytest.raises(GatewayUnavailable, match="invalid MT5"):
        MQL5ExecutionGateway().request("heartbeat", {})


def test_gateway_records_unavailable_metric(monkeypatch):
    class FailingSocket(FakeSocket):
        def recv_json(self):
            raise TimeoutError("timeout")

    class FailingContext(FakeContext):
        def __init__(self):
            self.socket_instance = FailingSocket()

    class FailingZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: FailingContext())

    monkeypatch.setattr(zmq_gateway, "zmq", FailingZMQ)
    metrics = MetricsRegistry()
    with pytest.raises(GatewayUnavailable):
        MQL5ExecutionGateway(metrics=metrics).request("order_request", {})
    assert (
        metrics.get_counter(
            "mt5_gateway_errors_total", labels={"message_type": "order_request"}
        )
        == 1
    )
    assert (
        metrics.get_counter(
            "mt5_gateway_timeouts_total", labels={"message_type": "order_request"}
        )
        == 1
    )


def test_gateway_rejects_response_with_wrong_correlation(monkeypatch):
    class MismatchedSocket(FakeSocket):
        def recv_json(self):
            response = super().recv_json()
            response["correlation_id"] = "different-request"
            return response

    class MismatchedContext(FakeContext):
        def __init__(self):
            self.socket_instance = MismatchedSocket()

    class MismatchedZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: MismatchedContext())

    monkeypatch.setattr(zmq_gateway, "zmq", MismatchedZMQ)
    with pytest.raises(GatewayProtocolError):
        MQL5ExecutionGateway().request("order_request", {})


def test_gateway_rejects_order_result_without_status(monkeypatch):
    class MissingStatusSocket(FakeSocket):
        def recv_json(self):
            response = super().recv_json()
            response["payload"].pop("status")
            return response

    class MissingStatusContext(FakeContext):
        def __init__(self):
            self.socket_instance = MissingStatusSocket()

    class MissingStatusZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: MissingStatusContext())

    monkeypatch.setattr(zmq_gateway, "zmq", MissingStatusZMQ)

    with pytest.raises(GatewayProtocolError, match="invalid MT5"):
        MQL5ExecutionGateway().request("order_request", {})


def test_gateway_rejects_inconsistent_order_result_status(monkeypatch):
    class InconsistentSocket(FakeSocket):
        def recv_json(self):
            response = super().recv_json()
            response["payload"] = {"status": "rejected", "accepted": True}
            return response

    class InconsistentContext(FakeContext):
        def __init__(self):
            self.socket_instance = InconsistentSocket()

    class InconsistentZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: InconsistentContext())

    monkeypatch.setattr(zmq_gateway, "zmq", InconsistentZMQ)

    with pytest.raises(GatewayProtocolError, match="invalid MT5"):
        MQL5ExecutionGateway().request("order_request", {})


def test_gateway_classifies_zmq_timeout_without_resetting_or_retrying(monkeypatch):
    class TimedOutSocket(FakeSocket):
        def recv_json(self):
            raise FakeZMQ.error.Again("timed out")

    class TimedOutContext(FakeContext):
        def __init__(self):
            self.socket_instance = TimedOutSocket()

    class TimedOutZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: TimedOutContext())

    monkeypatch.setattr(zmq_gateway, "zmq", TimedOutZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(GatewayTimeoutError) as exc_info:
        gateway.request("order_request", {})

    assert exc_info.value.code == "timeout"
    assert gateway._socket.closed is False
    assert gateway._socket.send_count == 1


def test_gateway_classifies_transport_error_and_resets_without_retrying(monkeypatch):
    class BrokenSocket(FakeSocket):
        def send_json(self, value):
            super().send_json(value)
            raise FakeZMQ.error.ZMQError("transport failed")

    class BrokenContext(FakeContext):
        def __init__(self):
            self.socket_instance = BrokenSocket()

    class BrokenZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: BrokenContext())

    monkeypatch.setattr(zmq_gateway, "zmq", BrokenZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(GatewayTransportError) as exc_info:
        gateway.request("order_request", {})

    assert exc_info.value.code == "transport"
    assert gateway._context.socket_instance.send_count == 1
    assert gateway._socket is None


def test_gateway_classifies_malformed_response_without_resetting(monkeypatch):
    class MalformedSocket(FakeSocket):
        def recv_json(self):
            return {"schema_version": "2"}

    class MalformedContext(FakeContext):
        def __init__(self):
            self.socket_instance = MalformedSocket()

    class MalformedZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: MalformedContext())

    monkeypatch.setattr(zmq_gateway, "zmq", MalformedZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(GatewayProtocolError) as exc_info:
        gateway.request("order_request", {})

    assert exc_info.value.code == "protocol"
    assert gateway._socket.closed is False
    assert gateway._socket.send_count == 1


def test_gateway_reraises_unexpected_exception_without_resetting_or_retrying(
    monkeypatch,
):
    class UnexpectedSocket(FakeSocket):
        def recv_json(self):
            raise RuntimeError("unexpected failure")

    class UnexpectedContext(FakeContext):
        def __init__(self):
            self.socket_instance = UnexpectedSocket()

    class UnexpectedZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: UnexpectedContext())

    monkeypatch.setattr(zmq_gateway, "zmq", UnexpectedZMQ)
    gateway = MQL5ExecutionGateway()

    with pytest.raises(RuntimeError, match="unexpected failure"):
        gateway.request("order_request", {})

    assert gateway._socket.closed is False
    assert gateway._socket.send_count == 1


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "zmq_contract_v1.json"


@pytest.mark.parametrize(
    "case_name",
    [
        "accepted_order",
        "rejected_order",
        "duplicate_message",
        "timeout",
        "unknown_outcome",
    ],
)
def test_gateway_replays_contract_cases_without_auto_retry(monkeypatch, case_name):
    case = load_contract_document(FIXTURE_PATH).get_case(case_name)
    payload = case.request["payload"]

    class CorpusSocket(FakeSocket):
        def recv_json(self):
            status = case.expected["status"]
            if status == "timeout":
                raise FakeZMQ.error.Again("timed out")
            response = {
                "schema_version": "1.0",
                "message_id": case.request["message_id"],
                "message_type": "order_result",
                "sent_at": "2026-09-17T00:00:00+00:00",
                "correlation_id": self.sent["correlation_id"],
                "source": "mt5",
                "payload": {
                    "accepted": case.expected["accepted"],
                    "status": status,
                },
            }
            if status == "duplicate":
                response["payload"]["duplicate_message_id"] = "case-duplicate-001"
            if status == "rejected":
                response["payload"]["rejection_reason"] = "unsupported_symbol"
            if status == "accepted":
                response["payload"]["order_id"] = "ord-accepted-001"
            if status == "unknown":
                response["payload"]["reason"] = "unknown broker state"
            return response

    class CorpusContext(FakeContext):
        def __init__(self):
            self.socket_instance = CorpusSocket()

    class CorpusZMQ(FakeZMQ):
        class Context:
            instance = staticmethod(lambda: CorpusContext())

    monkeypatch.setattr(zmq_gateway, "zmq", CorpusZMQ)
    gateway = MQL5ExecutionGateway()
    bind_request_context("request-1", case.request["correlation_id"])

    if case_name == "timeout":
        with pytest.raises(GatewayTimeoutError) as exc_info:
            gateway.request(case.request["message_type"], payload)
        assert exc_info.value.code == "timeout"
        assert gateway._socket.send_count == 1
        assert gateway._socket.closed is False
        return

    response = gateway.request(case.request["message_type"], payload)
    assert response["payload"]["accepted"] is case.expected["accepted"]
    assert response["payload"]["status"] == case.expected["status"]
    assert response["correlation_id"] == case.request["correlation_id"]
    assert gateway._socket.send_count == 1
    assert gateway._socket.closed is False
    assert not (
        case.expected["status"] in {"timeout", "unknown"}
        and response["payload"]["accepted"]
    )
