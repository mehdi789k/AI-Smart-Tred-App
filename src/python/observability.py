"""Operational observability primitives for the trading services."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

try:
    from .logging_config import _safe_value, get_logger, log_event
except ImportError:  # pragma: no cover - compatibility for legacy top-level imports
    from logging_config import _safe_value, get_logger, log_event


class MetricsRegistry:
    """Thread-safe in-process counters, gauges and latency observations."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = (
            defaultdict(float)
        )
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._registry = CollectorRegistry()
        self._prometheus_counters: dict[tuple[str, tuple[str, ...]], Counter] = {}
        self._prometheus_gauges: dict[tuple[str, tuple[str, ...]], Gauge] = {}
        self._prometheus_histograms: dict[tuple[str, tuple[str, ...]], Histogram] = {}

    @staticmethod
    def _key(
        name: str, labels: dict[str, Any] | None
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
            raise ValueError("metric name must contain letters, digits and underscores")
        normalized = tuple(
            sorted((str(key), str(value)) for key, value in (labels or {}).items())
        )
        return name, normalized

    def inc(
        self, name: str, value: float = 1.0, *, labels: dict[str, Any] | None = None
    ) -> None:
        if value < 0:
            raise ValueError("counter increment must not be negative")
        normalized_labels = self._normalize_labels(labels)
        with self._lock:
            self._counters[self._key(name, labels)] += value
            metric = self._prometheus_counters.get((name, normalized_labels))
            if metric is None:
                metric = Counter(
                    name.removesuffix("_total"),
                    "Operational counter",
                    labelnames=normalized_labels,
                    registry=self._registry,
                )
                self._prometheus_counters[(name, normalized_labels)] = metric
            self._observe_child(metric, labels).inc(value)

    def set_gauge(
        self, name: str, value: float, *, labels: dict[str, Any] | None = None
    ) -> None:
        normalized_labels = self._normalize_labels(labels)
        with self._lock:
            self._gauges[self._key(name, labels)] = float(value)
            metric = self._prometheus_gauges.get((name, normalized_labels))
            if metric is None:
                metric = Gauge(
                    name,
                    "Operational gauge",
                    labelnames=normalized_labels,
                    registry=self._registry,
                )
                self._prometheus_gauges[(name, normalized_labels)] = metric
            self._observe_child(metric, labels).set(value)

    def observe(
        self, name: str, value: float, *, labels: dict[str, Any] | None = None
    ) -> None:
        normalized_labels = self._normalize_labels(labels)
        with self._lock:
            metric = self._prometheus_histograms.get((name, normalized_labels))
            if metric is None:
                metric = Histogram(
                    name,
                    "Operational latency histogram",
                    labelnames=normalized_labels,
                    registry=self._registry,
                )
                self._prometheus_histograms[(name, normalized_labels)] = metric
            self._observe_child(metric, labels).observe(value)

    @staticmethod
    def _observe_child(metric: Any, labels: dict[str, Any] | None) -> Any:
        if not labels:
            return metric
        return metric.labels(**labels)

    @staticmethod
    def _normalize_labels(labels: dict[str, Any] | None) -> tuple[str, ...]:
        return tuple(sorted(str(key) for key in (labels or {})))

    def get_counter(self, name: str, *, labels: dict[str, Any] | None = None) -> float:
        with self._lock:
            return self._counters.get(self._key(name, labels), 0.0)

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Return a stable copy for diagnostics and tests."""
        with self._lock:
            return {
                "counters": {
                    f"{name}{_format_labels(labels)}": value
                    for (name, labels), value in self._counters.items()
                },
                "gauges": {
                    f"{name}{_format_labels(labels)}": value
                    for (name, labels), value in self._gauges.items()
                },
            }

    def render_prometheus(self) -> str:
        """Render metrics using Prometheus text exposition format."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
        rendered = generate_latest(self._registry).decode("utf-8")
        # Keep the legacy snapshot counters/gauges as the diagnostic source;
        # Prometheus output comes from bounded, process-safe collectors above.
        if not rendered and (counters or gauges):
            return (
                "\n".join(
                    [
                        *(
                            f"{name}{_format_labels(labels)} {value:g}"
                            for (name, labels), value in counters.items()
                        ),
                        *(
                            f"{name}{_format_labels(labels)} {value:g}"
                            for (name, labels), value in gauges.items()
                        ),
                    ]
                )
                + "\n"
            )
        return rendered


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = (
        f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in labels
    )
    return "{" + ",".join(escaped) + "}"


class AlertManager:
    """Emit a deduplicated alert when a metric crosses a configured threshold."""

    def __init__(
        self,
        metrics: MetricsRegistry,
        *,
        thresholds: dict[str, float] | None = None,
        callback: Callable[[str, float, float], None] | None = None,
    ) -> None:
        self.metrics = metrics
        self.thresholds = thresholds or {}
        self.callback = callback
        self._triggered: set[str] = set()
        self._lock = Lock()
        self.logger = get_logger("alerts")

    def evaluate(self) -> list[str]:
        triggered: list[str] = []
        for metric, threshold in self.thresholds.items():
            value = self.metrics.get_counter(metric)
            with self._lock:
                if value < threshold or metric in self._triggered:
                    continue
                self._triggered.add(metric)
            log_event(
                self.logger,
                40,
                "alert_triggered",
                metric=metric,
                value=value,
                threshold=threshold,
            )
            if self.callback:
                self.callback(metric, value, threshold)
            triggered.append(metric)
        return triggered

    def reset(self, metric: str) -> None:
        with self._lock:
            self._triggered.discard(metric)


class AuditLogger:
    """Append-only audit sink kept separate from operational log files."""

    def __init__(
        self, path: str | os.PathLike[str] = "logs/trading_audit.jsonl"
    ) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def write(self, event: str, **fields: Any) -> None:
        payload = {
            "timestamp": time.time(),
            "event": event,
            "payload": _safe_value(fields),
        }
        encoded = (
            json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
        ).encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
