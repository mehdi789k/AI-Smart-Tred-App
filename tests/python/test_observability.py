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


def test_alert_manager_supports_gauge_thresholds_and_reads_current_values():
    metrics = MetricsRegistry()
    metrics.set_gauge("data_freshness_seconds", 180)
    metrics.set_gauge("account_exposure_ratio", 0.62)

    alerts = AlertManager(
        metrics,
        thresholds={
            "data_freshness_seconds": 120,
            "account_exposure_ratio": 0.5,
        },
    )

    assert metrics.get_gauge("data_freshness_seconds") == 180
    assert metrics.get_gauge("account_exposure_ratio") == 0.62
    assert alerts.evaluate() == ["data_freshness_seconds", "account_exposure_ratio"]


def test_get_counter_total_sums_all_labeled_series():
    metrics = MetricsRegistry()
    metrics.inc("order_rejections_total", 2, labels={"symbol": "EURUSD"})
    metrics.inc("order_rejections_total", 3, labels={"symbol": "GBPUSD"})

    get_counter_total = getattr(metrics, "get_counter_total", None)

    assert callable(get_counter_total)
    assert get_counter_total("order_rejections_total") == 5


def test_alert_manager_uses_labeled_counter_series_when_unlabeled_series_is_missing():
    metrics = MetricsRegistry()
    metrics.inc("order_rejections_total", 2, labels={"symbol": "EURUSD"})
    metrics.inc("order_rejections_total", 1, labels={"symbol": "GBPUSD"})
    alerts = AlertManager(metrics, thresholds={"order_rejections_total": 3})

    assert alerts.evaluate() == ["order_rejections_total"]


def test_audit_logger_is_separate_and_redacts_secret(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLogger(path).write("order_rejected", token="secret", reason="mt5_disconnected")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["event"] == "order_rejected"
    assert record["payload"]["token"] == "[REDACTED]"
    assert record["payload"]["reason"] == "mt5_disconnected"
