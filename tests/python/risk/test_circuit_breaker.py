import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))

from risk.circuit_breaker import CircuitBreaker

from src.python.observability import AuditLogger, MetricsRegistry


def test_circuit_breaker_trips_and_manual_override_allows_trading():
    breaker = CircuitBreaker(1000)
    assert breaker.evaluate([{"pnl": -20}])["trading_allowed"] is False
    breaker.set_manual_override(True)
    result = breaker.evaluate([{"pnl": -20}])
    assert result["trading_allowed"] is True
    assert result["stop_reason"] is None


def test_manual_override_requires_boolean():
    with pytest.raises(ValueError, match="boolean"):
        CircuitBreaker(1000).set_manual_override("yes")


def test_circuit_breaker_exports_trip_metric_and_audit(tmp_path):
    metrics = MetricsRegistry()
    audit = AuditLogger(tmp_path / "audit.jsonl")
    breaker = CircuitBreaker(1000, metrics=metrics, audit_logger=audit)

    result = breaker.evaluate([{"pnl": -20}])

    assert result["trading_allowed"] is False
    assert metrics.get_counter("circuit_breaker_trips_total") == 1
    assert metrics.snapshot()["gauges"]["circuit_breaker_tripped"] == 1
    assert "circuit_breaker_tripped" in (tmp_path / "audit.jsonl").read_text()


def test_circuit_breaker_audit_preserves_correlation_id(tmp_path, monkeypatch):
    metrics = MetricsRegistry()
    audit = AuditLogger(tmp_path / "audit.jsonl")
    breaker = CircuitBreaker(1000, metrics=metrics, audit_logger=audit)
    monkeypatch.setattr(
        "risk.circuit_breaker.get_correlation_id",
        lambda: "corr-test-1",
    )

    breaker.evaluate([{"pnl": -20}])

    assert '"correlation_id": "corr-test-1"' in (tmp_path / "audit.jsonl").read_text()
