"""ZeroMQ PUB/SUB publisher for normalized ticks and OHLCV updates."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from threading import Event
from typing import Any
from uuid import uuid4

from .config import DataConfig, Timeframe
from .mt5_connector import MT5Connector
from .validators import validate_ohlcv_rows

logger = logging.getLogger(__name__)

try:
    import zmq as _zmq
except ImportError:  # pragma: no cover - optional for persistence-only deployments
    _zmq = None  # type: ignore[assignment]

_zmq_api: Any = _zmq


class StreamerUnavailableError(RuntimeError):
    """Raised when publication is requested without pyzmq."""


class MarketDataStreamer:
    """Publish contract-stable JSON messages on a ZeroMQ PUB socket."""

    def __init__(
        self,
        connector: MT5Connector,
        config: DataConfig,
        *,
        repository: Any | None = None,
        context: Any | None = None,
        socket: Any | None = None,
    ) -> None:
        self.connector = connector
        self.config = config
        self.repository = repository
        self._context = context
        self.socket = socket
        self._started = False
        self._last_bar_timestamps: dict[tuple[str, str], datetime] = {}
        self._last_tick_timestamps: dict[str, datetime] = {}
        self._ingestion_id = str(uuid4())

    @property
    def endpoint(self) -> str:
        """Return the configured bind endpoint."""

        return self.config.zmq_endpoint

    def start(self) -> None:
        """Create and bind a PUB socket exactly once."""

        if self._started:
            return
        if self.socket is None:
            if _zmq is None:
                raise StreamerUnavailableError(
                    "ZeroMQ is optional; install the 'pyzmq' package to stream data"
                )
            self._context = self._context or _zmq_api.Context.instance()
            self.socket = self._context.socket(_zmq_api.PUB)
        self.socket.bind(self.endpoint)
        self._started = True

    def close(self) -> None:
        """Close the socket without terminating a shared ZeroMQ context."""

        if self.socket is not None:
            try:
                self.socket.close(linger=0)
            except TypeError:
                self.socket.close()
        self.socket = None
        self._started = False

    def publish(
        self,
        *,
        symbol: str,
        timeframe: Timeframe | str,
        data: Mapping[str, Any],
        kind: str = "ohlcv",
    ) -> dict[str, Any]:
        """Publish one JSON message under ``{symbol}_{timeframe}``."""

        if not symbol:
            raise ValueError("symbol must not be empty")
        timeframe_value = Timeframe(timeframe).value if kind == "ohlcv" else str(timeframe)
        topic = f"{symbol}_{timeframe_value}"
        body = {
            "schema_version": 1,
            "type": kind,
            "symbol": symbol,
            "timeframe": timeframe_value,
            "timestamp": _json_value(data.get("timestamp", datetime.now(timezone.utc))),
            "data": {str(key): _json_value(value) for key, value in data.items()},
        }
        # Keep the canonical nested ``data`` object while also exposing fields
        # at the envelope level for lightweight consumers and older clients.
        for key, value in body["data"].items():
            body.setdefault(key, value)
        if self.socket is None or not self._started:
            raise RuntimeError("streamer must be started before publishing")
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if hasattr(self.socket, "send_multipart"):
            self.socket.send_multipart([topic.encode("utf-8"), encoded])
        else:
            self.socket.send_string(f"{topic} {encoded.decode('utf-8')}")
        return body

    def publish_tick(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Publish a normalized tick."""

        return self.publish(
            symbol=str(row["symbol"]),
            timeframe="tick",
            data=row,
            kind="tick",
        )

    def publish_bar(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Publish a normalized OHLCV bar."""

        return self.publish(
            symbol=str(row["symbol"]),
            timeframe=Timeframe(row["timeframe"]).value,
            data=row,
            kind="ohlcv",
        )

    def stream_once(self) -> int:
        """Poll and publish one tick per symbol and the latest bar per subscription."""

        self.start()
        published = 0
        for row in self.connector.iter_ticks(self.config.symbols):
            timestamp = _as_utc(row.get("timestamp"))
            if timestamp is not None and timestamp <= self._last_tick_timestamps.get(
                str(row["symbol"]), datetime.min.replace(tzinfo=timezone.utc)
            ):
                continue
            if self.repository is not None:
                awaitable = self.repository.bulk_insert_ticks([row])
                _run_or_schedule(awaitable)
            self.publish_tick(row)
            if timestamp is not None:
                self._last_tick_timestamps[str(row["symbol"])] = timestamp
            published += 1
        for symbol in self.config.symbols:
            for timeframe in self.config.timeframes:
                try:
                    rows = self.connector.get_rates(symbol, timeframe, count=1)
                except Exception:
                    logger.exception("Unable to poll %s %s", symbol, timeframe.value)
                    continue
                if not rows:
                    continue
                row = rows[-1]
                row = dict(row)
                row.setdefault("symbol", symbol)
                row.setdefault("timeframe", timeframe.value)
                try:
                    validate_ohlcv_rows([row])
                except (KeyError, TypeError, ValueError) as error:
                    logger.warning("Dropping invalid live bar %s %s: %s", symbol, timeframe.value, error)
                    continue
                timestamp = _as_utc(row.get("timestamp"))
                key = (symbol, timeframe.value)
                if timestamp is not None and timestamp <= self._last_bar_timestamps.get(
                    key, datetime.min.replace(tzinfo=timezone.utc)
                ):
                    continue
                if self.repository is not None:
                    row = dict(row)
                    row.setdefault("source", "mt5_official")
                    row.setdefault(
                        "ingestion_metadata",
                        {
                            "ingestion_id": self._ingestion_id,
                            "received_at": datetime.now(timezone.utc).isoformat(),
                            "collector": "incremental_collector",
                        },
                    )
                    _run_or_schedule(self.repository.bulk_upsert_candles([row]))
                self.publish_bar(row)
                if timestamp is not None:
                    self._last_bar_timestamps[key] = timestamp
                published += 1
        return published

    def run(self, stop_event: Event | None = None, on_iteration: Any | None = None) -> None:
        """Run the polling loop until stopped or interrupted."""

        stop_event = stop_event or Event()
        self.start()
        try:
            while not stop_event.is_set():
                published = self.stream_once()
                if on_iteration is not None:
                    on_iteration(published)
                stop_event.wait(self.config.poll_interval_seconds)
        finally:
            self.close()


class MarketDataSubscriber:
    """Minimal SUB helper useful to consumers and integration tests."""

    def __init__(self, endpoint: str, *, context: Any | None = None, socket: Any | None = None) -> None:
        self.endpoint = endpoint
        self._context = context
        self.socket = socket

    def connect(self, topics: Iterable[str] = ("",)) -> None:
        """Connect and subscribe to one or more topic prefixes."""

        if self.socket is None:
            if _zmq is None:
                raise StreamerUnavailableError("install 'pyzmq' to subscribe")
            self._context = self._context or _zmq_api.Context.instance()
            self.socket = self._context.socket(_zmq_api.SUB)
        self.socket.connect(self.endpoint)
        for topic in topics:
            self.socket.setsockopt_string(_zmq_api.SUBSCRIBE if _zmq else 6, topic)

    def receive(self) -> tuple[str, dict[str, Any]]:
        """Receive and decode one topic/message pair."""

        if self.socket is None:
            raise RuntimeError("subscriber is not connected")
        if hasattr(self.socket, "recv_multipart"):
            topic, payload = self.socket.recv_multipart()
            return topic.decode("utf-8"), json.loads(payload.decode("utf-8"))
        message = self.socket.recv_string()
        topic, payload = message.split(" ", 1)
        return topic, json.loads(payload)

    def close(self) -> None:
        """Close the SUB socket."""

        if self.socket is not None:
            try:
                self.socket.close(linger=0)
            except TypeError:
                self.socket.close()
            self.socket = None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _run_or_schedule(value: Any) -> None:
    """Run repository coroutines in synchronous polling code when safe."""

    if not hasattr(value, "__await__"):
        return
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        import asyncio

        asyncio.run(value)
    else:
        loop.create_task(value)


DataStreamer = MarketDataStreamer


class IncrementalCollector(MarketDataStreamer):
    """Named collector facade for deployments that only want incremental data.

    It deliberately reuses the streamer's idempotent persistence and safe
    polling loop, while suppressing repeated ticks/bars between polls.
    """

    pass


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
DataPublisher = MarketDataStreamer

__all__ = [
    "DataStreamer",
    "DataPublisher",
    "MarketDataStreamer",
    "MarketDataSubscriber",
    "StreamerUnavailableError",
]
