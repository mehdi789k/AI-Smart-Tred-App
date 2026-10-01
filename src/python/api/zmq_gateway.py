"""Fail-closed ZeroMQ request gateway for the MQL5 execution adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from ..logging_config import get_correlation_id, get_logger, log_event
from ..observability import MetricsRegistry

_zmq: Any = None
try:
    import zmq as _zmq
except ImportError:  # pragma: no cover - optional until the gateway is used
    _zmq = None

zmq: Any = _zmq


class GatewayUnavailable(RuntimeError):
    """Raised when the execution gateway cannot be used safely."""

    code = "gateway_unavailable"


class GatewayTimeoutError(GatewayUnavailable):
    """Raised when the execution gateway does not respond before its deadline."""

    code = "timeout"


class GatewayTransportError(GatewayUnavailable):
    """Raised when ZeroMQ reports a transport-level failure."""

    code = "transport"


class GatewayProtocolError(GatewayUnavailable):
    """Raised when the gateway returns a response outside the wire contract."""

    code = "protocol"


# Short aliases keep the failure taxonomy convenient for callers while the
# explicit names make the exception type clear in logs and tracebacks.
GatewayTimeout = GatewayTimeoutError
GatewayTransport = GatewayTransportError
GatewayProtocol = GatewayProtocolError


class MQL5ExecutionGateway:
    """Send one validated request at a time to the EA's REP socket."""

    def __init__(
        self,
        endpoint: str = "tcp://127.0.0.1:5555",
        timeout_ms: int = 1_000,
        *,
        metrics: MetricsRegistry | None = None,
    ):
        if not endpoint.startswith(("tcp://", "ipc://", "inproc://")):
            raise ValueError("unsupported ZeroMQ endpoint")
        if timeout_ms < 100:
            raise ValueError("timeout_ms must be at least 100")
        self.endpoint = endpoint
        self.timeout_ms = timeout_ms
        self._context: Any = None
        self._socket: Any = None
        self._lock = Lock()
        self.metrics = metrics or MetricsRegistry()
        self.logger = get_logger("zmq_gateway")

    def close(self) -> None:
        """Close the socket and release the shared context reference."""
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None

    def reconnect(self) -> None:
        """Drop the current REQ socket so the next request creates a fresh one."""
        with self._lock:
            self.close()

    def _ensure_socket(self) -> Any:
        if zmq is None:
            raise GatewayUnavailable("pyzmq is required for MT5 execution")
        if self._socket is None:
            self._context = self._context or zmq.Context.instance()
            socket = self._context.socket(zmq.REQ)
            self._socket = socket
            try:
                socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
                socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
                socket.connect(self.endpoint)
            except Exception:
                socket.close(linger=0)
                self._socket = None
                raise
        return self._socket

    def request(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Exchange one schema-versioned message, failing closed on protocol errors."""
        if not message_type or not isinstance(payload, dict):
            raise ValueError("message_type and payload are required")
        envelope = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "correlation_id": get_correlation_id() or str(uuid4()),
            "source": "python",
            "payload": payload,
        }
        with self._lock:
            try:
                socket = self._ensure_socket()
                socket.send_json(envelope)
                response = socket.recv_json()
            except TimeoutError as error:
                self.metrics.inc(
                    "mt5_gateway_errors_total", labels={"message_type": message_type}
                )
                self.metrics.inc(
                    "mt5_gateway_timeouts_total",
                    labels={"message_type": message_type},
                )
                log_event(
                    self.logger,
                    40,
                    "mt5_gateway_timeout",
                    message_type=message_type,
                    correlation_id=envelope["correlation_id"],
                )
                raise GatewayTimeoutError(
                    "MT5 gateway did not acknowledge the request"
                ) from error
            except GatewayUnavailable:
                self.metrics.inc(
                    "mt5_gateway_errors_total", labels={"message_type": message_type}
                )
                log_event(
                    self.logger,
                    40,
                    "mt5_gateway_unavailable",
                    message_type=message_type,
                    correlation_id=envelope["correlation_id"],
                )
                raise
            except json.JSONDecodeError as error:
                self.metrics.inc(
                    "mt5_gateway_errors_total", labels={"message_type": message_type}
                )
                self.metrics.inc(
                    "mt5_gateway_protocol_errors_total",
                    labels={"message_type": message_type},
                )
                log_event(
                    self.logger,
                    40,
                    "mt5_gateway_protocol_error",
                    message_type=message_type,
                    correlation_id=envelope["correlation_id"],
                )
                raise GatewayProtocolError("invalid MT5 gateway response") from error
            except Exception as error:
                zmq_error = getattr(zmq, "error", None) if zmq is not None else None
                again_type = getattr(zmq_error, "Again", ())
                zmq_error_type = getattr(zmq_error, "ZMQError", ())
                if not isinstance(again_type, type):
                    again_type = ()
                if not isinstance(zmq_error_type, type):
                    zmq_error_type = ()
                if isinstance(error, again_type):
                    self.metrics.inc(
                        "mt5_gateway_errors_total",
                        labels={"message_type": message_type},
                    )
                    self.metrics.inc(
                        "mt5_gateway_timeouts_total",
                        labels={"message_type": message_type},
                    )
                    log_event(
                        self.logger,
                        40,
                        "mt5_gateway_timeout",
                        message_type=message_type,
                        correlation_id=envelope["correlation_id"],
                    )
                    raise GatewayTimeoutError(
                        "MT5 gateway did not acknowledge the request"
                    ) from error
                if isinstance(error, zmq_error_type):
                    self.metrics.inc(
                        "mt5_gateway_errors_total",
                        labels={"message_type": message_type},
                    )
                    self.metrics.inc(
                        "mt5_gateway_transport_errors_total",
                        labels={"message_type": message_type},
                    )
                    log_event(
                        self.logger,
                        40,
                        "mt5_gateway_transport_error",
                        message_type=message_type,
                        correlation_id=envelope["correlation_id"],
                    )
                    self.close()
                    raise GatewayTransportError(
                        "MT5 gateway transport failed"
                    ) from error
                self.metrics.inc(
                    "mt5_gateway_errors_total", labels={"message_type": message_type}
                )
                self.metrics.inc(
                    "mt5_gateway_internal_errors_total",
                    labels={"message_type": message_type},
                )
                log_event(
                    self.logger,
                    40,
                    "mt5_gateway_internal_error",
                    message_type=message_type,
                    correlation_id=envelope["correlation_id"],
                    error_type=type(error).__name__,
                )
                raise
        required_fields = {
            "schema_version",
            "message_id",
            "message_type",
            "sent_at",
            "correlation_id",
            "source",
            "payload",
        }
        valid_response = (
            isinstance(response, dict)
            and required_fields.issubset(response)
            and response["schema_version"] == "1.0"
            and isinstance(response["message_id"], str)
            and bool(response["message_id"])
            and response["message_type"] == "order_result"
            and isinstance(response["sent_at"], str)
            and isinstance(response["correlation_id"], str)
            and response["correlation_id"] == envelope["correlation_id"]
            and response["source"] == "mt5"
            and isinstance(response["payload"], dict)
        )
        if valid_response:
            payload = response["payload"]
            status = payload.get("status")
            accepted = payload.get("accepted")
            code = payload.get("code")
            valid_status = status in {
                "accepted",
                "rejected",
                "unknown",
                "timeout",
                "partial",
            }
            valid_accepted = accepted is None or isinstance(accepted, bool)
            valid_code = message_type != "heartbeat" or (
                isinstance(code, str) and bool(code)
            )
            status_matches_acceptance = (
                accepted is None
                or (accepted and status in {"accepted", "partial"})
                or (not accepted and status in {"rejected", "unknown", "timeout"})
            )
            heartbeat_ok = message_type != "heartbeat" or (
                status == "accepted"
                and accepted is True
                and code == "heartbeat"
                and isinstance(payload.get("message"), str)
                and bool(payload.get("message"))
                and response["source"] == "mt5"
            )
            valid_response = (
                isinstance(status, str)
                and valid_status
                and valid_accepted
                and valid_code
                and status_matches_acceptance
                and heartbeat_ok
            )
        if not valid_response:
            self.metrics.inc(
                "mt5_gateway_errors_total", labels={"message_type": message_type}
            )
            self.metrics.inc(
                "mt5_gateway_protocol_errors_total",
                labels={"message_type": message_type},
            )
            log_event(
                self.logger,
                40,
                "mt5_gateway_protocol_error",
                message_type=message_type,
                correlation_id=envelope["correlation_id"],
            )
            raise GatewayProtocolError("invalid MT5 gateway response")
        self.metrics.inc(
            "mt5_gateway_requests_total", labels={"message_type": message_type}
        )
        return response
