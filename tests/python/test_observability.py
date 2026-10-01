import json

import pytest

from src.python.observability import AlertManager, AuditLogger, MetricsRegistry


def test_metrics_render_counters_gauges_and_histograms():
    metrics = MetricsRegistry()
    metrics.inc("requests_total", labels={"path": "/health"})
    metrics.set_gauge("mt5_connected", 1)
    metrics.observe("request_duration_seconds", 0.25)

    rendered = metrics.render_prometheus()

    assert 'requests_total{path="/health"} 1' in rendered
    assert "mt5_connected 1" in rendered
    assert "request_duration_seconds_count 1" in rendered
    assert "request_duration_seconds_sum 0.25" in rendered


def test_metrics_reject_invalid_values():
    metrics = MetricsRegistry()
    with pytest.raises(ValueError):
        metrics.inc("requests_total", -1)
    with pytest.raises(ValueError):
        metrics.inc("1invalid")


def test_alert_manager_deduplicates_until_reset():
    metrics = MetricsRegistry()
    metrics.inc("errors_total", 2)
    calls = []
    alerts = AlertManager(
        metrics,
        thresholds={"errors_total": 2},
        callback=lambda *args: calls.append(args),
    )

    assert alerts.evaluate() == ["errors_total"]
    assert alerts.evaluate() == []
    alerts.reset("errors_total")
    assert alerts.evaluate() == ["errors_total"]
    assert len(calls) == 2


def test_audit_logger_is_separate_and_redacts_secret(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLogger(path).write("order_rejected", token="secret", reason="mt5_disconnected")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["event"] == "order_rejected"
    assert record["payload"]["token"] == "[REDACTED]"
    assert record["payload"]["reason"] == "mt5_disconnected"
