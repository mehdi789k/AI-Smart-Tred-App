# Task 4 report: ZeroMQ failure taxonomy

Date: 2026-09-16
Status: Complete after independent review and remediation

## Delivered

- Added stable `GatewayTimeoutError`, `GatewayTransportError`, and
  `GatewayProtocolError` classifications with machine-readable codes.
- Classified `TimeoutError` and `zmq.error.Again` as timeout without automatic
  retry or socket reset.
- Classified `zmq.error.ZMQError` as transport, reset the socket, and still
  performed no retry.
- Validated the complete MT5 response envelope, including source, message type,
  correlation ID, and payload.
- Classified malformed JSON and malformed envelopes as protocol failures.
- Preserved unexpected exception propagation after structured logging.
- Closed sockets created during connection setup when setup fails.

## Validation

```text
py -m pytest -q tests/api/test_zmq_gateway.py tests/python/execution/test_demo_operations.py --import-mode=importlib
17 passed

py -m ruff check [Task 4 files]
All checks passed!
```

No real ZeroMQ/EA or MT5 order was used.
