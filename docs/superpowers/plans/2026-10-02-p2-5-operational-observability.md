# P2.5 Operational Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export trustworthy candle freshness and unresolved-order metrics from every API replica, aggregate and alert on them centrally, and avoid publishing unsupported exposure or daily P&L values.

**Architecture:** Keep the existing API `/metrics`, Prometheus, Alertmanager, and Grafana topology. Each API replica reads shared PostgreSQL state read-only; Prometheus aggregates state gauges with `max` and event counters with `sum`. Explicit per-symbol/timeframe limits gate freshness alerts; exposure and daily P&L remain absent until an authoritative fresh snapshot source exists.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, pytest, Prometheus, Grafana, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-02-p2-5-operational-observability-design.md`

## Global Constraints

- Target deployment is multiple API replicas in one Docker Compose deployment and on one host.
- Reuse the existing API `/metrics` endpoint and Prometheus / Alertmanager / Grafana services; add no exporter service.
- Monitor only explicitly configured `(symbol, timeframe)` pairs, each with a finite positive maximum age.
- Do not infer market calendars or apply a universal timeframe multiplier.
- Do not publish exposure or daily P&L until an authoritative, fresh MT5 account snapshot source is available; missing values must not be represented as zero.
- Keep all work observational and read-only; do not change order admission, execution, risk decisions, MT5 order submission, database schema, or data writers.
- Restrict labels to configured symbols/timeframes and fixed source names. Never label metrics with account IDs, order IDs, credentials, or unbounded identifiers.
- On a sampling failure, expose source unavailability and an error counter; do not present stale observations as a healthy current sample.
- Pass `OBS_CANDLE_MAX_AGE_SECONDS` explicitly into the API container.
- In scale-out mode, retain container port `8000`, allocate distinct host ports from `8000-8099`, and explain how to discover actual host ports with `docker compose ps api`.
- No validation may submit Demo or Live orders or require an MT5 terminal.

---

### Task 1: Parse freshness monitoring configuration

**Files:**
- Create: `src/python/api/metrics_sampler.py`
- Create: `tests/python/api/test_metrics_sampler.py`

**Interfaces:**
- Produces `parse_candle_age_limits(raw: str | None) -> dict[tuple[str, str], float]`.
- Configuration keys use `SYMBOL:TIMEFRAME`; returned symbols are normalized uppercase and timeframes are validated against `Timeframe` from `src/python/data/config.py`.
- A missing or empty value returns `{}`. Invalid JSON, non-object JSON, malformed keys, unsupported timeframes, non-numeric/non-finite values, and zero or negative limits raise `ValueError` with a safe configuration-specific message.

- [ ] **Step 1: Write parser tests first**

Add tests that establish:

```python
def test_parse_candle_age_limits_normalizes_symbol_and_timeframe():
    assert parse_candle_age_limits('{"xauusd:M5": 600}') == {
        ("XAUUSD", "M5"): 600.0
    }


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"XAUUSD": 600}',
        '{"XAUUSD:UNSUPPORTED": 600}',
        '{"XAUUSD:M5": 0}',
        '{"XAUUSD:M5": -1}',
        '{"XAUUSD:M5": NaN}',
        '{"XAUUSD:M5": true}',
        '{"xauusd:M5": 600, "XAUUSD:M5": 900}',
    ],
)
def test_parse_candle_age_limits_rejects_invalid_configuration(raw):
    with pytest.raises(ValueError):
        parse_candle_age_limits(raw)
```

Also assert `None` and whitespace return an empty mapping.

- [ ] **Step 2: Run the parser tests and confirm the expected failure**

Run: `py -3.12 -m pytest tests/python/api/test_metrics_sampler.py -q --import-mode=importlib`

Expected: FAIL because `metrics_sampler` and `parse_candle_age_limits` do not exist yet.

- [ ] **Step 3: Implement the parser**

Implement only the typed parser in `metrics_sampler.py`. Use the existing `Timeframe` enum to reject unsupported periods, `math.isfinite` to reject NaN/infinity, and explicit validation for non-empty symbol and timeframe components. Do not add an implicit default threshold.

- [ ] **Step 4: Re-run the parser tests**

Run: `py -3.12 -m pytest tests/python/api/test_metrics_sampler.py -q --import-mode=importlib`

Expected: PASS for valid normalization, empty configuration, and all invalid-input cases.

- [ ] **Step 5: Run Ruff on the parser and test**

Run: `py -3.12 -m ruff check src/python/api/metrics_sampler.py tests/python/api/test_metrics_sampler.py`

Expected: PASS with no lint findings.

### Task 2: Collect read-only database state and expose reliable gauges

**Files:**
- Modify: `src/python/api/metrics_sampler.py`
- Modify: `tests/python/api/test_metrics_sampler.py`
- Modify: `src/python/observability.py`
- Modify: `tests/python/test_observability.py`

**Interfaces:**
- Produces `OperationalMetricsSampler(engine: AsyncEngine | None, metrics: MetricsRegistry, candle_age_limits: Mapping[tuple[str, str], float])`.
- Produces `async OperationalMetricsSampler.collect(*, now: datetime | None = None) -> None`.
- The sampler queries the maximum `OHLCVBar.timestamp` for each configured pair with `DataStatus.PERSISTED`, and counts all `TradingOrder` records with `OrderStatus.UNKNOWN`.
- Exports `candle_data_available{symbol,timeframe}`, successful `candle_data_age_seconds{symbol,timeframe}`, `candle_stale_after_seconds{symbol,timeframe}`, `candle_freshness_configured`, `unknown_orders_count`, `observability_source_available{source}`, and `observability_collection_errors_total{source}`. Fixed source labels are `market_data` and `order_state`.
- Produces `MetricsRegistry.get_counter_total(name: str) -> float`; `AlertManager` uses it when an alert threshold refers to a counter with labels and no unlabelled series.

- [ ] **Step 1: Add failing tests for database sampling**

Extend `test_metrics_sampler.py` with async tests backed by an in-memory SQLite `AsyncDatabase` and real SQLAlchemy models. Insert a symbol and persisted candles for both a fresh and a stale configured pair; insert unknown, accepted, and rejected orders. Assert exact age, per-pair stale limits, availability, and that only unknown orders are counted. Use a fixed UTC `now` so the freshness assertions are deterministic.

Add separate cases for:

- a configured pair with no persisted candle (`candle_data_available=0`, market-data source remains available, and no successful age is fabricated);
- a future candle timestamp (unavailable and not represented as zero age);
- an engine-less sampler (both sources unavailable, with no fabricated order count or candle freshness);
- a candle-query failure that marks market data unavailable but still collects order state;
- an order-query failure that marks order state unavailable but still collects candle state.

For query-failure cases, use a SQLAlchemy engine event listener that raises `SQLAlchemyError` only for the selected table query; keep the other query real.

- [ ] **Step 2: Run the sampling tests and confirm the expected failure**

Run: `py -3.12 -m pytest tests/python/api/test_metrics_sampler.py -q --import-mode=importlib`

Expected: FAIL because `OperationalMetricsSampler` is not implemented.

- [ ] **Step 3: Implement the sampler**

Use SQLAlchemy `select`, `func.max`, and `func.count` against the existing model tables. Query each data source independently so one source failure cannot hide successful results from the other. Catch `SQLAlchemyError` at the database-query boundary, log a structured error without identifiers/secrets, set the affected source unavailable, and increment its bounded source error counter. Convert timestamps to aware UTC; reject future timestamps instead of clamping them. Set source availability to `1` only after that source query succeeds. Leave the account exposure and P&L metric families unregistered.

The query predicates must match the persisted candle and unknown-order enums:

```python
select(func.max(OHLCVBar.timestamp)).where(
    OHLCVBar.symbol == symbol,
    OHLCVBar.timeframe == timeframe,
    OHLCVBar.status == DataStatus.PERSISTED,
)

select(func.count()).select_from(TradingOrder).where(
    TradingOrder.status == OrderStatus.UNKNOWN,
)
```

For each candle pair, set `candle_data_available` to `1` and publish age only
when the query returns a non-future timestamp. A successful query with no
persisted candle sets availability to `0` while keeping the market-data source
available. A query error sets market-data source availability to `0`. Apply the
same per-source distinction to order-state query errors. Never overwrite a
source's current availability with a success-shaped fallback.

- [ ] **Step 4: Add a failing regression test for labeled local counters**

In `tests/python/test_observability.py`, add a test that increments:

```python
metrics.inc("order_rejections_total", labels={"reason": "stale"})
metrics.inc("order_rejections_total", labels={"reason": "expired"})
```

Construct `AlertManager(metrics, thresholds={"order_rejections_total": 2})` and assert `evaluate()` returns `["order_rejections_total"]`. Also assert `get_counter_total("order_rejections_total") == 2`.

- [ ] **Step 5: Run the labeled-counter test and confirm the expected failure**

Run: `py -3.12 -m pytest tests/python/test_observability.py::test_alert_manager_aggregates_labeled_counters -q --import-mode=importlib`

Expected: FAIL because `get_counter_total` and labeled-counter aggregation are not implemented.

- [ ] **Step 6: Implement labeled counter aggregation**

Add a lock-protected `get_counter_total` that sums every stored label series for the requested counter. Update `AlertManager._current_value` to preserve existing unlabelled gauge/counter precedence and fall back to the labeled-counter total when only labeled counter series exist. Do not sum unrelated gauges.

- [ ] **Step 7: Run both observability test modules**

Run: `py -3.12 -m pytest tests/python/api/test_metrics_sampler.py tests/python/test_observability.py -q --import-mode=importlib`

Expected: PASS, including all prior behavior and the new source-failure and labeled-counter cases.

- [ ] **Step 8: Run Ruff on the changed implementation and tests**

Run: `py -3.12 -m ruff check src/python/api/metrics_sampler.py src/python/observability.py tests/python/api/test_metrics_sampler.py tests/python/test_observability.py`

Expected: PASS with no lint findings.

### Task 3: Integrate scrape-time collection and Compose configuration

**Files:**
- Modify: `src/python/api/app.py`
- Modify: `tests/api/test_app.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- `create_app()` reads `OBS_CANDLE_MAX_AGE_SECONDS` with `parse_candle_age_limits` during app construction; malformed settings fail explicitly rather than silently disabling monitoring.
- `/metrics` awaits `OperationalMetricsSampler.collect()` before rendering. A handled query failure leaves unrelated metrics renderable and exposes source-unavailable metrics; it does not produce a healthy fallback value.
- The API container receives `OBS_CANDLE_MAX_AGE_SECONDS` through its Compose `environment` mapping.
- The API service publishes container port `8000` using host range `8000-8099`, allowing Compose to assign a distinct host port per replica. The first free host port is allocated; operators discover it rather than assuming `8000` is free.

- [ ] **Step 1: Write API scrape tests first**

In `tests/api/test_app.py`, create an in-memory `AsyncDatabase`, start it through `TestClient` lifespan, insert one unknown order, request `/metrics`, and assert `unknown_orders_count 1` is rendered. Add a failure case whose database engine raises `SQLAlchemyError` for the order query; assert the scrape stays available, order-state source availability is `0`, and no exposure/P&L metric is present. Add a startup test proving malformed `OBS_CANDLE_MAX_AGE_SECONDS` raises `ValueError`.

- [ ] **Step 2: Run the new API tests and confirm the expected failure**

Run: `py -3.12 -m pytest tests/api/test_app.py -q --import-mode=importlib`

Expected: FAIL because the route does not yet collect database-backed metrics or parse the new setting.

- [ ] **Step 3: Integrate the sampler into app construction and `/metrics`**

Parse freshness limits before serving requests, construct the sampler from the runtime database engine and the existing `MetricsRegistry`, and await collection after metrics-token authorization but before calling `render_prometheus()`. Preserve 401 behavior and do not change the route's existing content type.

Keep the route's ordering equivalent to:

```python
if metrics_token and x_api_key != metrics_token:
    raise HTTPException(status_code=401, detail="unauthorized")
await sampler.collect()
return PlainTextResponse(
    metrics.render_prometheus(),
    media_type="text/plain; version=0.0.4",
)
```

- [ ] **Step 4: Wire environment and dynamic replica ports**

Add `OBS_CANDLE_MAX_AGE_SECONDS=` to `.env.example` with an adjacent comment showing a non-empty JSON value. In `docker-compose.yml`, pass `${OBS_CANDLE_MAX_AGE_SECONDS:-}` to the API container; the empty environment value is interpreted by `parse_candle_age_limits` as no configured pairs. Change the API published-port declaration to a host range `8000-8099` targeting container port `8000`, for example:

```yaml
ports:
  - "8000-8099:8000"
```

- [ ] **Step 5: Re-run API and sampler tests**

Run: `py -3.12 -m pytest tests/api/test_app.py tests/python/api/test_metrics_sampler.py -q --import-mode=importlib`

Expected: PASS; `/metrics` invokes the sampler, source failures are explicit, and malformed config fails closed.

- [ ] **Step 6: Validate Compose interpolation without starting services**

Run in PowerShell with non-secret validation values:

```powershell
$env:POSTGRES_PASSWORD = "compose-validation-only"
$env:API_AUTH_TOKEN = "compose-validation-only"
$env:GRAFANA_ADMIN_PASSWORD = "compose-validation-only"
docker compose config --quiet
docker compose config
```

Expected: both commands exit successfully; the expanded API environment includes the configured JSON string when supplied (and an empty string otherwise), and the API port target remains `8000` with host range `8000-8099`. Do not run `docker compose up` as part of this task.

- [ ] **Step 7: Run Ruff on the changed API code and tests**

Run: `py -3.12 -m ruff check src/python/api/app.py tests/api/test_app.py`

Expected: PASS with no lint findings.

### Task 4: Add centralized Prometheus alerts and Grafana panels

**Files:**
- Modify: `ops/prometheus/alerts.yml`
- Modify: `ops/grafana/dashboards/smart-trader.json`
- Create: `tests/python/api/test_observability_configuration.py`

**Interfaces:**
- Prometheus alerts use the metric names from Task 2 and aggregate replicated state using `max`/`max by(...)`, never `sum`.
- Event counters such as collection failures continue to use `sum`/`increase` or `rate`.
- The dashboard displays candle age/freshness, source availability/configuration, and unresolved orders. It contains no exposure or daily-P&L panels.

- [ ] **Step 1: Write failing configuration contract tests**

Create tests that parse the Grafana JSON and assert panels exist for candle freshness, data-source availability, and unknown orders. Assert no panel title or PromQL target references exposure or daily P&L. Add tests that verify the Prometheus rule file contains rules for unknown orders, missing/stale configured candles, empty freshness configuration, and unavailable sampling sources, and that state queries use `max` aggregation.

- [ ] **Step 2: Run the configuration tests and confirm the expected failure**

Run: `py -3.12 -m pytest tests/python/api/test_observability_configuration.py -q --import-mode=importlib`

Expected: FAIL because the new alert rules and dashboard panels are absent.

- [ ] **Step 3: Add Prometheus alert rules**

Add:

- critical alert when `max(unknown_orders_count) > 0`;
- warning/critical freshness alerts gated by `max(observability_source_available{source="market_data"}) == 1`, `candle_data_available == 1`, and `candle_data_age_seconds > candle_stale_after_seconds`;
- an explicit missing-candle alert when the market-data source is available but a configured pair has `candle_data_available == 0`;
- an alert when `max(candle_freshness_configured) == 0`;
- an alert for every API replica whose market-data or order-state source gauge is `0`; retain Prometheus's replica-level `instance` label for these availability alerts so one failing replica is not hidden by a healthy peer;
- an event alert for increased `observability_collection_errors_total`.

Use `max by(symbol,timeframe)` or equivalent for shared candle/order state
gauges so identical PostgreSQL state is not multiplied across replicas. Do not
alert on stale age when the market-data source is unavailable.

Representative PromQL for shared unknown-order state and per-replica source
health:

```promql
max(unknown_orders_count) > 0
observability_source_available{source="order_state"} == 0
sum(increase(observability_collection_errors_total[5m])) > 0
```

- [ ] **Step 4: Add Grafana panels**

Extend the existing dashboard JSON with panels using `max by(symbol,timeframe)` for candle age and availability, and `max(unknown_orders_count)` for unresolved orders. Add a panel for source availability and configuration. Keep all existing panels unchanged and omit risk/P&L values.

- [ ] **Step 5: Run the contract tests**

Run: `py -3.12 -m pytest tests/python/api/test_observability_configuration.py -q --import-mode=importlib`

Expected: PASS for every required rule/panel and the absence of unsupported account-risk panels.

- [ ] **Step 6: Validate Prometheus rules and Compose config**

Run `promtool check rules ops/prometheus/alerts.yml` if `promtool` is installed.
Otherwise run the pinned Prometheus container's `promtool check rules` against
a read-only mount:

```powershell
docker run --rm -v "${PWD}\ops\prometheus:/etc/prometheus:ro" prom/prometheus:v2.55.1 promtool check rules /etc/prometheus/alerts.yml
```

Then run `docker compose config --quiet` with the validation-only required
variables from Task 3.

Expected: rule validation and Compose configuration both exit successfully; no services are started.

- [ ] **Step 7: Run Ruff on the configuration tests**

Run: `py -3.12 -m ruff check tests/python/api/test_observability_configuration.py`

Expected: PASS with no lint findings.

### Task 5: Document operations, update the assessment, and validate the complete slice

**Files:**
- Modify: `docs/OBSERVABILITY_DEPLOYMENT_FA.md`
- Modify: `README.md`
- Modify: `docs/deployment/DOCKER_GUIDE.md`
- Modify: `docs/PROJECT_ASSESSMENT_FA.md`

**Interfaces:**
- Deployment documentation states the meaning and example of `OBS_CANDLE_MAX_AGE_SECONDS`, the read-only data sources, alert behavior, source-failure interpretation, and that exposure/P&L metrics are intentionally unavailable.
- Scale-out instructions show `docker compose ps api` and explain that each replica publishes a distinct host port from `8000-8099`; internal Prometheus scraping remains on container port `8000`.
- README and Docker guide describe `localhost:8000` as the default only while that host port is available; they direct users to `docker compose ps api` for an allocated port and for scale-out.
- The project assessment marks P2.5 complete only after all verification evidence in this task passes. Do not claim production readiness or Demo/Live readiness.

- [ ] **Step 1: Update the deployment guide**

Document a concrete configuration example:

```env
OBS_CANDLE_MAX_AGE_SECONDS={"XAUUSD:M5":600}
```

Explain that every configured threshold is positive and explicit, an empty mapping raises a configuration alert, metrics are sampled from PostgreSQL during `/metrics` scrapes, Prometheus uses max for shared gauges, and missing/failing data is not a zero or healthy result. Update the scale-out instructions:

```powershell
docker compose up -d --scale api=2 api prometheus
docker compose ps api
```

Explain how to read each published host port from the `PORTS` column and keep
internal target port `8000` unchanged. The host range begins at `8000`, but if
that port is already occupied Docker may allocate another available port in the
range; users should inspect `docker compose ps api` instead of assuming a host
port. Update the API URL rows in `README.md` and `docs/deployment/DOCKER_GUIDE.md`
with the same default-if-free qualification and the `docker compose ps api`
discovery instruction.

- [ ] **Step 2: Run a documentation scope and whitespace check**

Run: `git diff --check -- docs/OBSERVABILITY_DEPLOYMENT_FA.md README.md docs/deployment/DOCKER_GUIDE.md`

Expected: PASS with no whitespace errors in the deployment guide and both existing API URL references.

- [ ] **Step 3: Run the complete focused test slice**

Run:

```powershell
py -3.12 -m pytest tests\python\api\test_metrics_sampler.py tests\python\test_observability.py tests\python\api\test_observability_configuration.py tests\api\test_app.py -q --import-mode=importlib
```

Expected: PASS for all focused API, sampler, metrics, and configuration tests.

- [ ] **Step 4: Run the complete Python test suite**

Run: `py -3.12 -m pytest -q --import-mode=importlib`

Expected: exit code `0`; report the exact pass/skip counts. Do not claim the suite passed if it did not finish successfully.

- [ ] **Step 5: Run Ruff and Compose validation for the final diff**

Run:

```powershell
py -3.12 -m ruff check src tests
py -3.12 -m ruff format --check src tests
docker compose config --quiet
```

Set only the three validation-only Compose required variables from Task 3 in the current PowerShell process before Compose validation. Expected: every command exits successfully.

- [ ] **Step 6: Update the P2.5 assessment from observed evidence**

Change the P2.5 row in `docs/PROJECT_ASSESSMENT_FA.md` from open to complete only after the focused and full suites, Ruff, rule validation, and Compose validation pass. Record the implemented metric/alert scope and actual validation counts. Explicitly retain exposure/P&L and production notification delivery as deferred, and keep Demo/Live readiness separate.

- [ ] **Step 7: Inspect the final status and diff scope**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: all P2.5 changes are confined to the planned files, pre-existing worktree changes remain untouched, and no runtime logs, database files, credentials, temporary exports, or generated reports were created.

- [ ] **Step 8: Preserve existing uncommitted work**

Do not stage or commit the implementation as a whole-file change: `src/python/observability.py` already has pre-existing uncommitted edits, and other unrelated worktree changes are present. Leave P2.5 changes in the working tree for explicit review; if a commit is later requested, first isolate only the P2.5 hunks and show the staged diff before committing.
