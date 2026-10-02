# P2.5 Operational Observability Design

## Goal

Complete P2.5 observability for the documented Docker Compose deployment with
multiple API replicas. Publish trustworthy market-data freshness and unresolved
order-state metrics, aggregate them centrally in Prometheus, and raise actionable
alerts without inventing account-risk values.

## Owner decisions

- Deployment target: multiple API replicas in one Docker Compose deployment and
  on one host.
- Architecture: retain the existing API `/metrics` endpoint and Prometheus /
  Alertmanager / Grafana services; do not add a metrics service.
- Market-data freshness: only explicitly configured symbol/timeframe pairs are
  monitored, each with its own positive maximum age. Do not infer market
  calendars or apply a universal timeframe multiplier.
- Exposure and daily P&L: do not publish or alert on either until a valid, fresh
  authoritative MT5 account snapshot source is available. Missing values must
  not be represented as zero.
- Scope is observational and read-only. It must not change order admission,
  order execution, or risk decisions.

## Current evidence

- `docker-compose.yml` runs the API as a service; the API image starts one
  Uvicorn process per container by default.
- `docs/OBSERVABILITY_DEPLOYMENT_FA.md` documents `--scale api=2`.
- `ops/prometheus/prometheus.yml` uses Docker DNS discovery and retains each API
  replica as a distinct Prometheus target.
- `MetricsRegistry` in `src/python/observability.py` is process-local.
- `src/python/data/models.py` defines `OHLCVBar`, `TradingOrder`, and
  `AccountSnapshot`. No active account-snapshot writer was found in the runtime
  paths examined. Therefore those account snapshots cannot yet be treated as a
  reliable exposure or P&L source.
- The API increments `order_rejections_total` with labels in several rejection
  paths, while the local `AlertManager` reads only an unlabelled series.
- Prometheus already has centralized rules for counters and health signals, but
  it does not yet alert on unresolved broker outcomes or candle-age freshness.

## Design

### Sampling and ownership boundaries

Add a small API-side sampler in the API package and invoke it when `/metrics` is
scraped. It performs read-only queries using the runtime database engine and the
existing SQLAlchemy models:

1. For each configured `(symbol, timeframe)`, read the maximum persisted candle
   timestamp.
2. Count all `unknown` trading orders from durable order state.
3. Record whether each source query succeeded.
4. Update the existing per-process `MetricsRegistry` before rendering metrics.

Each API replica reads the same PostgreSQL source and exports its own scrape.
Prometheus remains the aggregation boundary: state gauges are combined with
`max` (not `sum`) to avoid counting the same database state once per replica;
event counters continue to use `sum`/`rate` as appropriate. No shared filesystem,
Prometheus multiprocess mode, or API replica affinity is introduced.

The sampler is read-only and must not modify files under `src/python/data/` or
`src/python/risk/`. The established API database boundary is used without
adding a new repository write path.

### Metric contract

The exporter will provide:

- `candle_data_available{symbol,timeframe}`: `1` when a valid persisted candle
  timestamp was read; otherwise `0`.
- `candle_data_age_seconds{symbol,timeframe}`: age of the latest persisted
  candle timestamp, calculated against UTC now. A future timestamp is invalid
  and must not be clamped into a healthy zero-age value.
- `candle_stale_after_seconds{symbol,timeframe}`: the configured maximum age
  used by the alert rule.
- `candle_freshness_configured`: `1` if at least one pair is monitored, else
  `0`, so an empty configuration is visible.
- `unknown_orders_count`: exact count of orders in `unknown` state.
- `observability_source_available{source}`: `1` or `0` for the market-data and
  order-state sampling sources.
- `observability_collection_errors_total{source}`: cumulative source sampling
  errors, with bounded source labels.

Labels are limited to configured symbols, timeframes, and fixed source names.
No order IDs, account IDs, credentials, or other unbounded identifiers may be
exported as labels.

`OBS_CANDLE_MAX_AGE_SECONDS` will configure candle-age limits as a JSON object
keyed by `SYMBOL:TIMEFRAME`, for example
`{"XAUUSD:M5":600}`. Every configured value must be finite and strictly
positive. An invalid mapping fails explicitly at startup. An empty mapping is
allowed but sets `candle_freshness_configured` to `0` and is surfaced by a
configuration alert rather than silently appearing healthy.

### Failure handling

- A database query error is logged as a structured operational error, marks
  only the affected source unavailable, and increments its error counter.
- A sampling failure must not render a stale previously observed value as a
  current healthy sample. Availability metrics gate all related freshness
  alerts.
- The `/metrics` handler continues to render unrelated process-local metrics
  when possible; scrape availability itself remains observable.
- A missing candle is distinct from a fresh candle with age zero.
- Invalid future timestamps are marked unavailable and logged; they are not
  normalized into a healthy age.
- Exposure and daily P&L metric families are omitted until their authoritative
  source and freshness contract are separately designed and approved.

### Alerts and dashboard

Add Prometheus rules for:

- any unresolved `unknown_orders_count > 0` (critical);
- missing or stale configured candle data, using the exported per-pair maximum
  age and availability state;
- missing freshness configuration, so monitored operation cannot silently
  lack a threshold;
- unavailable market-data or order-state sampling sources.

Rules aggregate repeated state gauges with `max by(...)`; rejection and
collection-error counters remain aggregated as events. Alert notification
delivery continues through the existing Alertmanager and its operator-provided
receiver configuration.

Extend the existing Grafana overview with explicit panels for candle freshness,
monitoring configuration/source availability, and unresolved orders. Do not add
exposure or P&L panels until valid data is available.

Retain the local `AlertManager` as a process-local log signal and correct its
metric lookup so thresholds can read labelled counter/gauge series. It is not
the cross-replica alerting authority; Prometheus and Alertmanager remain so.

## Files expected to change

- `src/python/api/app.py` and a focused API-side sampler module.
- `src/python/observability.py` for correct local aggregation over labeled
  metric series.
- `.env.example` for the freshness-map setting and its explanation.
- `ops/prometheus/alerts.yml` and the existing Grafana dashboard.
- `docs/OBSERVABILITY_DEPLOYMENT_FA.md` and `docs/PROJECT_ASSESSMENT_FA.md`.
- Focused Python tests for the sampler, metrics, alert rules/configuration, and
  API scrape behavior.

No order-execution, MT5 order-submission, data-writer, risk-policy, or database
schema changes are part of this phase.

## Validation

1. Unit tests verify threshold parsing, fresh/stale/missing/future candle
   behavior, exact unknown-order counts, source failure signaling, and labeled
   `AlertManager` lookups.
2. API tests verify `/metrics` refreshes the metrics and does not return
   success-shaped data after a sampling failure.
3. Prometheus configuration and rules are parsed with the available
   `promtool` or equivalent repository validation.
4. Compose configuration is validated.
5. Run the focused tests, then the full Python test suite and applicable Ruff
   checks.
6. Update P2.5 in the project assessment only after validation evidence is
   available.

No validation may submit Demo or Live orders or require a real MT5 terminal.

## Acceptance criteria

1. Each replica exposes current, source-available metrics from PostgreSQL.
2. Multiple replicas do not multiply shared unknown-order or freshness gauges
   in Prometheus queries.
3. An unknown order, missing monitored candle, stale candle, missing freshness
   configuration, or unavailable sampling source produces the intended alert.
4. The local rejection alert lookup recognizes labeled rejection series.
5. Invalid thresholds and future timestamps cannot appear healthy.
6. Exposure and daily P&L are absent, not zero or estimated, until a separately
   approved authoritative account-snapshot contract exists.
7. All relevant automated validation passes, and no real/demo execution path
   is invoked.

## Out of scope

- Defining MT5 account exposure or daily-P&L formulas.
- Creating or modifying the account-snapshot writer.
- Introducing a background sampler, exporter service, shared metrics
  filesystem, or a new database schema.
- Changing live or demo trading behavior, risk limits, or circuit-breaker
  policy.
- Adding external alert receiver credentials to the repository.
