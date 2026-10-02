from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _panels():
    dashboard = json.loads(
        (ROOT / "ops" / "grafana" / "dashboards" / "smart-trader.json").read_text(
            encoding="utf-8"
        )
    )
    return dashboard["panels"]


def _rules():
    config = yaml.safe_load(
        (ROOT / "ops" / "prometheus" / "alerts.yml").read_text(encoding="utf-8")
    )
    return {
        rule["alert"]: rule
        for group in config["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }


def _normalized(expression: str) -> str:
    return "".join(expression.lower().split())


def test_dashboard_exposes_candle_freshness_sources_and_unknown_orders():
    panels = _panels()
    titles = {panel["title"] for panel in panels}

    assert {
        "Candle Freshness",
        "Observability Sources & Configuration",
        "Unresolved Orders",
    } <= titles

    expressions = [
        target["expr"] for panel in panels for target in panel.get("targets", [])
    ]
    normalized = [_normalized(expression) for expression in expressions]
    assert any(
        "maxby(symbol,timeframe)(candle_data_age_seconds)" in expr
        for expr in normalized
    )
    assert any("max(unknown_orders_count)" in expr for expr in normalized)
    assert not any(
        forbidden
        in f"{panel['title']} "
        f"{' '.join(target.get('expr', '') for target in panel.get('targets', []))}".lower()
        for panel in panels
        for forbidden in ("exposure", "daily_pnl", "daily pnl")
    )


def test_prometheus_rules_cover_unknown_orders_and_candle_freshness():
    rules = _rules()

    assert {
        "SmartTraderUnknownOrders",
        "SmartTraderCandleMissing",
        "SmartTraderCandleStale",
        "SmartTraderCandleFreshnessUnconfigured",
    } <= rules.keys()
    unknown = _normalized(rules["SmartTraderUnknownOrders"]["expr"])
    missing = _normalized(rules["SmartTraderCandleMissing"]["expr"])
    stale = _normalized(rules["SmartTraderCandleStale"]["expr"])
    unconfigured = _normalized(rules["SmartTraderCandleFreshnessUnconfigured"]["expr"])

    assert "max(unknown_orders_count)>0" in unknown
    assert 'max(observability_source_available{source="market_data"})==1' in missing
    assert "maxby(symbol,timeframe)(candle_data_available)==0" in missing
    assert (
        "maxby(symbol,timeframe)(candle_data_age_seconds)"
        ">on(symbol,timeframe)"
        "maxby(symbol,timeframe)(candle_stale_after_seconds)"
    ) in stale
    assert "maxby(symbol,timeframe)(candle_data_available)==1" in stale
    assert "max(candle_freshness_configured)==0" in unconfigured


def test_prometheus_rules_alert_on_each_unavailable_replica_and_sampling_errors():
    rules = _rules()

    assert "SmartTraderObservabilitySourceUnavailable" in rules
    assert "SmartTraderObservabilityCollectionErrors" in rules
    source_expression = _normalized(
        rules["SmartTraderObservabilitySourceUnavailable"]["expr"]
    )
    error_expression = _normalized(
        rules["SmartTraderObservabilityCollectionErrors"]["expr"]
    )

    assert (
        'observability_source_available{source=~"market_data|order_state"}==0'
        in source_expression
    )
    assert "max(" not in source_expression
    assert (
        "sum(increase(observability_collection_errors_total[5m]))>0" in error_expression
    )
