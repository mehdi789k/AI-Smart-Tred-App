# Guarded Live Trading Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep live mode enabled while proving the configured MT5 account, market feed, persistent risk controls, and guarded order boundary are ready before any real order is allowed.

**Architecture:** Add a read-only live-readiness probe that validates MT5 terminal/account identity, whitelisted symbols, and fresh ticks. Wire the Windows live startup path and API readiness surface to fail closed unless a durable circuit breaker is armed; keep `LiveOrderWorkflow` as the only order boundary and require a separate exact-order confirmation before `order_send`.

**Tech Stack:** Python 3.12, FastAPI, MetaTrader5, pytest, PowerShell, PostgreSQL/SQLAlchemy execution-control persistence.

**Spec:** `docs/superpowers/specs/2026-09-21-guarded-live-trading-readiness-design.md`

## Global Constraints

- MT5 side remains strict MQL5 build 4000+; Python remains 3.12 with type hints.
- `MT5_AUTO_TRADING_ENABLED=true` and `MT5_DEMO_ENABLED=false` are required for live startup.
- No credentials, passwords, API keys, or confirmation tokens may be logged.
- Every order path must retain symbol whitelist, magic-number, maximum-volume, daily-loss, spread, emergency-stop, and short-lived confirmation checks.
- Readiness checks must never call `order_send`, `/api/v1/signals/{signal_id}/execute`, or any order endpoint.
- Missing or ambiguous dependencies fail closed; no success-shaped fallback is permitted.

---

### Task 1: Add read-only live readiness validation

**Files:**
- Create: `src/python/execution/live_readiness.py`
- Test: `tests/python/execution/test_live_readiness.py`

**Interfaces:**
- Consumes: An MT5 connector exposing `is_connected()`, `get_account_summary()`, `get_symbol_info(symbol)`, and `get_symbols_list(visible_only=True)`.
- Produces: `LiveReadinessReport(ready: bool, reasons: tuple[str, ...], account: dict[str, object], symbols: dict[str, dict[str, object]])` and `validate_live_readiness(...) -> LiveReadinessReport`.

- [ ] **Step 1: Write failing tests for account identity and fresh market data**

```python
def test_readiness_requires_expected_account_server_and_fresh_tick():
    report = validate_live_readiness(
        connector=FakeConnector(
            account={"login": 123, "server": "LiveBroker-Demo"},
            symbols={"XAUUSD": {"bid": 100.0, "ask": 100.2, "timestamp": utc_now()}},
        ),
        expected_login=456,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        max_tick_age_seconds=30,
    )
    assert report.ready is False
    assert "account_login_mismatch" in report.reasons
    assert "account_server_mismatch" in report.reasons
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest tests/python/execution/test_live_readiness.py -q`

Expected: FAIL because `src/python/execution/live_readiness.py` does not exist.

- [ ] **Step 3: Implement the validator**

Implement `validate_live_readiness` with these exact checks:
`is_connected()` must be true; account login and server must match the expected
values; every configured symbol must be visible and have finite positive bid
and ask values; each tick timestamp must be UTC and no older than
`max_tick_age_seconds`; the allowed-symbol set must be non-empty. Return all
failure reason codes without exposing credential values.

- [ ] **Step 4: Add regression cases for unavailable connector, missing symbol, stale tick, and valid readiness**

```python
def test_valid_readiness_is_ready():
    report = validate_live_readiness(
        FakeConnector.valid(),
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        max_tick_age_seconds=30,
    )
    assert report.ready is True
    assert report.reasons == ()
```

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/python/execution/test_live_readiness.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the isolated validator**

```powershell
git add src/python/execution/live_readiness.py tests/python/execution/test_live_readiness.py
git commit -m "feat: add read-only live readiness validation"
```

### Task 2: Arm the persistent circuit breaker for live startup

**Files:**
- Modify: `src/python/api/app.py` in runtime dependency construction and `/ready`
- Modify: `src/python/risk/circuit_breaker.py` only if the existing constructor needs a typed live factory
- Test: `tests/api/test_app.py`
- Test: `tests/python/risk/test_circuit_breaker.py`

**Interfaces:**
- Consumes: Verified account equity/balance and `MT5_MAX_DAILY_LOSS`/`MT5_MAX_DAILY_LOSS_PERCENT`.
- Produces: A configured `runtime_circuit_breaker` with `trading_allowed` false until it has a verified account baseline and a successful initial evaluation; `/ready` reports `armed`, `tripped`, or `unavailable`.

- [ ] **Step 1: Add a failing API readiness test for missing live risk configuration**

```python
def test_ready_blocks_live_trading_when_circuit_breaker_is_unavailable(client):
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "circuit_breaker_unavailable"
```

- [ ] **Step 2: Run the focused API and breaker tests**

Run: `python -m pytest tests/api/test_app.py tests/python/risk/test_circuit_breaker.py -q`

Expected: FAIL for the new case because readiness currently returns a
success-shaped response when no circuit breaker is configured.

- [ ] **Step 3: Implement fail-closed breaker initialization**

Construct the breaker only after MT5 account information is available, use the
configured daily-loss policy, evaluate it against the current daily trade
history, and keep the dependency status `unavailable` if initialization or
evaluation fails. Do not call `set_manual_override(True)` in live startup.
Return HTTP 503 for live readiness when the breaker is unavailable or
unarmed; preserve the existing non-order health endpoint.

- [ ] **Step 4: Add tests for armed, tripped, and initialization-failure states**

```python
def test_ready_reports_armed_circuit_breaker(fake_runtime):
    response = fake_runtime.client.get("/ready")
    assert response.status_code == 200
    assert response.json()["data"]["dependencies"]["circuit_breaker"] == "armed"
    assert response.json()["data"]["trading"]["allowed"] is True
```

- [ ] **Step 5: Run the targeted tests**

Run: `python -m pytest tests/api/test_app.py tests/python/risk/test_circuit_breaker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the circuit-breaker wiring**

```powershell
git add src/python/api/app.py src/python/risk/circuit_breaker.py tests/api/test_app.py tests/python/risk/test_circuit_breaker.py
git commit -m "feat: require armed circuit breaker for live readiness"
```

### Task 3: Add a live readiness command and wire the Windows startup path

**Files:**
- Create: `scripts/verify_live_readiness.py`
- Modify: `scripts/start_local_demo.ps1` in live-mode environment setup and validation
- Test: `tests/python/test_live_readiness_script.py`
- Modify: `docs/RUNBOOK_MT5_CIRCUIT_BREAKER_FA.md` with the live readiness command and stop conditions

**Interfaces:**
- Consumes: `.env` values `MT5_LOGIN`, `MT5_SERVER`, `MT5_TERMINAL_PATH`, `MT5_LIVE_SYMBOLS`, and the API `/health` and `/ready` documents.
- Produces: A non-zero exit code on any failed read-only check and a redacted JSON/text summary on success.

- [ ] **Step 1: Write failing script tests**

```python
def test_live_readiness_script_never_calls_order_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.verify_live_readiness.fetch_json", lambda *args, **kwargs: calls.append(args) or healthy_payload())
    assert run_validation(...) == 0
    assert all("/execute" not in call[1] for call in calls)
```

- [ ] **Step 2: Run the focused script tests**

Run: `python -m pytest tests/python/test_live_readiness_script.py -q`

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement the read-only command**

Load environment values without printing passwords. Call `/health` and
`/ready`, validate live flags, dependency states, `trading.allowed`, configured
symbol count, and the MT5 read-only report. Print only account login/server,
symbol status, breaker status, and reason codes.

- [ ] **Step 4: Wire `start_local_demo.ps1`**

Keep Demo behavior unchanged. In Live mode, require both
`-ConfirmLiveTrading` and `LIVE_TRADING_CONFIRMATION`, set
`MT5_AUTO_TRADING_ENABLED=true`, `MT5_DEMO_ENABLED=false`,
`MT5_LEGACY_ORDER_PATH_ENABLED=false`, and invoke
`scripts\verify_live_readiness.py` before opening the dashboard or starting
any automated loop. Abort with a clear startup error if validation fails.

- [ ] **Step 5: Run script tests and PowerShell syntax validation**

Run: `python -m pytest tests/python/test_live_readiness_script.py -q`

Run: `$null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path 'scripts\start_local_demo.ps1'), [ref]$null, [ref]$null); if ($?) { 'PowerShell parse passed' }`

Expected: both commands succeed.

- [ ] **Step 6: Commit startup/readiness integration**

```powershell
git add scripts/verify_live_readiness.py scripts/start_local_demo.ps1 tests/python/test_live_readiness_script.py docs/RUNBOOK_MT5_CIRCUIT_BREAKER_FA.md
git commit -m "feat: gate live startup on read-only readiness"
```

### Task 4: Verify dashboard sensors and guarded execution without placing an order

**Files:**
- Modify: `src/python/dashboard/app.py` only where stale/unknown MT5 sensor state is rendered
- Modify: `src/python/execution/live_order_workflow.py` only if a missing breaker/readiness dependency can currently pass
- Test: `tests/python/dashboard/test_dashboard_integration.py`
- Test: `tests/python/execution/test_live_order_workflow.py`

**Interfaces:**
- Consumes: The readiness report and existing workflow configuration/control store.
- Produces: Explicit connected/stale/unavailable sensor states and a guarded execution path that rejects before MT5 for every missing safety condition.

- [ ] **Step 1: Add failing tests for stale sensor state and unavailable breaker**

```python
def test_dashboard_does_not_render_stale_tick_as_live():
    state = build_sensor_state(last_tick_at=utc_now() - timedelta(minutes=5), max_age_seconds=30)
    assert state.status == "stale"
    assert state.trading_allowed is False
```

```python
def test_workflow_rejects_without_armed_breaker():
    adapter = workflow_with_breaker(None)
    with pytest.raises(LiveOrderRejected, match="circuit_breaker_unavailable"):
        adapter.execute_market_order("XAUUSD", "BUY", 0.01, magic=7, confirmation_token="token")
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/python/dashboard/test_dashboard_integration.py tests/python/execution/test_live_order_workflow.py -q`

Expected: FAIL for the new regression cases.

- [ ] **Step 3: Implement explicit sensor and workflow guards**

Render a stale or unavailable state with its timestamp and reason, never as
connected. Add the smallest workflow guard needed to require the armed
circuit-breaker/readiness dependency before broker interaction. Preserve
ambiguous-result handling and existing confirmation-token semantics.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/python/dashboard/test_dashboard_integration.py tests/python/execution/test_live_order_workflow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit guarded sensor/execution behavior**

```powershell
git add src/python/dashboard/app.py src/python/execution/live_order_workflow.py tests/python/dashboard/test_dashboard_integration.py tests/python/execution/test_live_order_workflow.py
git commit -m "fix: keep live execution closed on stale sensors or missing risk"
```

### Task 5: Run the complete verification gate and prepare final order confirmation

**Files:**
- Test: `tests/python/execution/test_live_readiness.py`
- Test: `tests/python/test_live_readiness_script.py`
- Test: `tests/api/test_app.py`
- Test: `tests/python/risk/test_circuit_breaker.py`
- Test: `tests/python/dashboard/test_dashboard_integration.py`
- Test: `tests/python/execution/test_live_order_workflow.py`

- [ ] **Step 1: Run the complete targeted suite**

Run:

```powershell
python -m pytest tests/python/execution/test_live_readiness.py tests/python/test_live_readiness_script.py tests/api/test_app.py tests/python/risk/test_circuit_breaker.py tests/python/dashboard/test_dashboard_integration.py tests/python/execution/test_live_order_workflow.py -q
```

Expected: PASS with no skipped safety tests.

- [ ] **Step 2: Run the read-only live readiness command**

Run:

```powershell
python scripts\verify_live_readiness.py --base-url http://127.0.0.1:8000
```

Expected: exit code 0, live flags enabled, expected account/server reported
without credentials, configured symbols fresh, and breaker `armed`.

- [ ] **Step 3: Inspect repository hygiene**

Run:

```powershell
git status --short
Get-ChildItem -Force
```

Expected: only intentional source, test, documentation, and plan/spec changes
remain; no credentials, caches, logs, or generated artifacts are added.

- [ ] **Step 4: Stop before any order**

Present the verified account login/server, terminal path basename, symbols,
effective volume/daily-loss/spread limits, breaker state, and sensor timestamps.
Ask for a separate confirmation containing the exact symbol, direction, volume,
stop-loss, take-profit, and explicit approval. Do not invoke an order endpoint
in this task without that final confirmation.
