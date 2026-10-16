from __future__ import annotations

import json
from typing import Any

try:
    import zmq
except Exception:  # pragma: no cover - optional dependency for containerized deployments.
    zmq = None


class DataStreamer:
    """Publish market-data updates over ZeroMQ when available."""

    def __init__(self, *, endpoint: str = "tcp://127.0.0.1:5555") -> None:
        self.endpoint = endpoint
        self._socket = None
        if zmq is not None:
            self._context = zmq.Context.instance()
            self._socket = self._context.socket(zmq.PUB)
            self._socket.bind(self.endpoint)

    def publish(self, symbol: str, timeframe: str, payload: dict[str, Any]) -> None:
        topic = f"{symbol}_{timeframe}"
        if zmq is None or self._socket is None:
            return
        self._socket.send_string(topic, flags=zmq.SNDMORE)
        self._socket.send_string(json.dumps(payload, default=str))

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
