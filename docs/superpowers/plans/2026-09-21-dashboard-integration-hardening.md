# Dashboard Integration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows Demo/Shadow dashboard use the FastAPI boundary for service and OHLC status, expose degraded states safely, and validate the browser startup path without enabling live trading.

**Architecture:** Add a small synchronous dashboard API client with typed response/error handling and environment-driven configuration. Keep direct MT5 access only for account/position/history panels that have no current API endpoint, while making startup and dashboard status distinguish API health, API readiness, and MT5 availability. Update the Windows launcher and Streamlit call sites, then verify with focused Python, PowerShell, and browser smoke tests.

**Tech Stack:** Python 3.12, Streamlit, FastAPI, pytest, PowerShell, Playwright/browser tools, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-21-dashboard-integration-hardening-design.md`

## Global Constraints

- Operate only in Demo/Shadow; do not enable or add a live-order path.
- Keep localhost services bound to loopback; do not add public network exposure.
- Read dashboard API base URL, timeout, and token from environment; never log credentials.
- Treat `/health` as process liveness and `/ready` as dependency readiness.
- Surface timeout, non-2xx, invalid JSON, and schema errors explicitly; no success-shaped fallback.
- Preserve direct MT5 panels only where the current API has no equivalent endpoint.
- Update `docs/API_CONTRACT.md`, `docs/ARCHITECTURE.md`, or relevant runbook documentation when behavior changes.
- Use Windows paths in commands and keep generated artifacts out of the repository root.

---

### Task 1: Add the dashboard API client and regression tests

**Files:**
- Create: `src/python/dashboard/api_client.py`
- Test: `tests/python/dashboard/test_api_client.py`

**Interfaces:**
- Produces `DashboardApiClient(base_url: str, timeout_seconds: float, token: str | None = None)`.
- Produces `DashboardApiError` with `kind`, `message`, and optional `status_code`.
- Produces `DashboardApiClient.health() -> dict[str, Any]`, `ready() -> dict[str, Any]`, and `ohlc(symbol: str, timeframe: str, limit: int = 1000) -> dict[str, Any]`.
- Uses the standard-library HTTP client already available to the dashboard process; do not add a dependency.

- [ ] **Step 1: Write failing tests for successful health, readiness, and OHLC responses.**

  Use a local threaded HTTP server or monkeypatched transport so tests do not
  require Docker. Assert that the client sends `Authorization: Bearer <token>`
  only when configured,
  preserves the requested OHLC query, and returns decoded dictionaries.

- [ ] **Step 2: Add failing tests for timeout, non-2xx, invalid JSON, and invalid response shape.**

  Assert each case raises `DashboardApiError` with a stable `kind` (`timeout`,
  `http`, `invalid_json`, or `schema`) and never returns `{}` or `None` as a
  successful result.

- [ ] **Step 3: Run the focused tests and confirm they fail for the missing client.**

  Run: `py -3.12 -m pytest tests\python\dashboard\test_api_client.py -q`

  Expected: collection or assertion failures because the client and error type
  do not yet exist.

- [ ] **Step 4: Implement the minimal client with URL joining, bounded timeout, and schema guards.**

  Use `urllib.request`/`urllib.error`, normalize a trailing slash in `base_url`,
  set `Accept: application/json`, add `Authorization: Bearer <token>` only for a
  non-empty configured token, parse JSON objects, and validate that OHLC returns
  `data.symbol`, `data.timeframe`, and a list-valued `data.candles`.

- [ ] **Step 5: Run the focused tests and verify all client cases pass.**

  Run: `py -3.12 -m pytest tests\python\dashboard\test_api_client.py -q`

- [ ] **Step 6: Run Ruff on the new module and test.**

  Run: `py -3.12 -m ruff check src\python\dashboard\api_client.py tests\python\dashboard\test_api_client.py`

- [ ] **Step 7: Commit the isolated client change.**

  Run:
  `git add src\python\dashboard\api_client.py tests\python\dashboard\test_api_client.py`
  `git commit -m "feat: add dashboard API client"`

### Task 2: Integrate API status and OHLC into the dashboard safely

**Files:**
- Modify: `src/python/dashboard/app.py`
- Modify: `src/python/dashboard/__init__.py` only if package exports are needed
- Test: `tests/python/dashboard/test_dashboard_integration.py`
- Test: `tests/python/dashboard/test_dashboard_smoke.py`

**Interfaces:**
- Consumes `DashboardApiClient` and `DashboardApiError` from Task 1.
- Produces a cached `get_dashboard_api_client()` configured from
  `DASHBOARD_API_BASE_URL` (default `http://127.0.0.1:8000`),
  `DASHBOARD_API_TIMEOUT_SECONDS` (default `3`), and optional `API_AUTH_TOKEN`.
- Produces a status snapshot with independent `health`, `ready`, and `ohlc`
  states, each containing `state`, `checked_at`, and an optional safe `message`.

- [ ] **Step 1: Add failing tests for API-unavailable and API-ready snapshots.**

  Inject a fake client into the dashboard status helper and assert that a
  timeout produces `degraded` without calling the MT5 connector, while valid
  health/readiness produces `healthy`/`ready` independently.

- [ ] **Step 2: Run the focused dashboard tests and confirm the new status helper is absent or fails.**

  Run: `py -3.12 -m pytest tests\python\dashboard\test_dashboard_integration.py tests\python\dashboard\test_dashboard_smoke.py -q`

- [ ] **Step 3: Implement the cached client and explicit status collection.**

  Keep MT5 connector initialization lazy and independent. Catch only
  `DashboardApiError`, display its safe message and error kind, and retain the
  last successful timestamp in Streamlit session state without logging tokens.

- [ ] **Step 4: Add a visible operational status section near the dashboard’s primary controls.**

  Show API liveness, dependency readiness, MT5 connection, last successful
  update time, and a non-color-only degraded explanation. Do not present API
  liveness as trading permission and do not add any write call.

- [ ] **Step 5: Route the dashboard market/OHLC display through the API client where an OHLC view exists.**

  Preserve direct MT5 account/positions/history panels. If the OHLC API is
  unavailable, show the explicit degraded state instead of silently substituting
  an MT5 call.

- [ ] **Step 6: Replace touched Streamlit `use_container_width` calls with the supported `width` argument.**

  Use `width="stretch"` for full-width tables/charts and `width="content"` for
  compact controls, matching the existing layout.

- [ ] **Step 7: Run focused dashboard tests and Ruff.**

  Run:
  `py -3.12 -m pytest tests\python\dashboard\test_dashboard_integration.py tests\python\dashboard\test_dashboard_smoke.py -q`
  and
  `py -3.12 -m ruff check src\python\dashboard\app.py src\python\dashboard\api_client.py tests\python\dashboard`

- [ ] **Step 8: Commit the dashboard integration change.**

  Run:
  `git add src\python\dashboard\app.py tests\python\dashboard\test_dashboard_integration.py tests\python\dashboard\test_dashboard_smoke.py`
  `git commit -m "feat: expose dashboard service status"`

### Task 3: Harden the Windows Demo launcher readiness flow

**Files:**
- Modify: `scripts/start_local_demo.ps1`
- Test: `tests/powershell/start_local_demo.Tests.ps1`
- Modify: `README.md`

**Interfaces:**
- Consumes `GET /health`, `GET /ready`, and `http://127.0.0.1:8501/_stcore/health`.
- Produces explicit startup failures for API liveness, API readiness, or dashboard
  health with the existing nonzero PowerShell error behavior.

- [ ] **Step 1: Add failing Pester cases for API readiness failure and dashboard health failure.**

  Mock `Invoke-WebRequest` and assert the script does not open the browser or
  continue to collector startup when `/ready` returns non-200 or the Streamlit
  health endpoint fails.

- [ ] **Step 2: Run the Pester file to confirm the new readiness assertions fail.**

  Run: `pwsh -NoProfile -File tests\powershell\dashboard_watchdog.Tests.ps1`
  and the targeted `start_local_demo.Tests.ps1` command used by the repository.

- [ ] **Step 3: Add a reusable `Wait-HttpReady` helper in the launcher.**

  Give it URI, label, attempts, and delay parameters; distinguish timeout from
  HTTP failure in `Write-StartupLog`; call it for `/health`, `/ready`, and the
  Streamlit health endpoint.

- [ ] **Step 4: Preserve Demo safety environment values and open the browser only after all checks pass.**

  Do not change live confirmation gates, direct-MT5 ownership rules, or PID
  cleanup behavior. Keep browser URL on `127.0.0.1:8501`.

- [ ] **Step 5: Run Pester and the Python demo-readiness regression tests.**

  Run:
  `pwsh -NoProfile -File tests\powershell\start_local_demo.Tests.ps1`
  and
  `py -3.12 -m pytest tests\python\test_demo_readiness.py tests\python\test_project_configuration.py -q`

- [ ] **Step 6: Update the local run instructions with the separate meanings of health and readiness.**

  Document that `/health` only proves process liveness, `/ready` proves
  dependency readiness, and dashboard/MT5 degraded status does not authorize
  trading.

- [ ] **Step 7: Commit the launcher and documentation change.**

  Run:
  `git add scripts\start_local_demo.ps1 tests\powershell\start_local_demo.Tests.ps1 README.md`
  `git commit -m "fix: gate dashboard startup on readiness"`

### Task 4: Verify Windows and browser behavior without real orders

**Files:**
- Modify: `tests/python/dashboard/test_dashboard_execution_fragment.py` only if a regression fixture is required
- Create: `tests/browser/dashboard-smoke.spec.ts` only if an existing browser test directory is absent
- Modify: `docs/superpowers/plans/2026-09-21-dashboard-integration-hardening.md` only for execution notes

**Interfaces:**
- Consumes the running local Demo/Shadow stack and the status UI from Task 2.
- Produces repeatable browser smoke evidence for API-ready and API-degraded states.

- [ ] **Step 1: Run the existing focused Python and PowerShell suites before browser validation.**

  Run:
  `py -3.12 -m pytest tests\python\dashboard tests\python\api -q --import-mode=importlib`
  and
  `pwsh -NoProfile -File tests\powershell\dashboard_watchdog.Tests.ps1`.

- [ ] **Step 2: Start only the local Demo/Shadow services using the existing launcher.**

  Run:
  `pwsh -NoProfile -File scripts\start_local_demo.ps1 -TradingMode Demo`

  Verify `127.0.0.1:8000/health`, `127.0.0.1:8000/ready`, and
  `127.0.0.1:8501/_stcore/health` before opening the UI. Do not provide live
  confirmation variables.

- [ ] **Step 3: Inspect the dashboard in the integrated browser.**

  Open `http://127.0.0.1:8501`, verify the status section has no console
  errors, confirm API health/readiness and MT5 state are independently shown,
  and confirm no order action is triggered by navigation or refresh.

- [ ] **Step 4: Exercise the degraded browser state.**

  Stop or temporarily isolate the API process using the existing project
  stop/restart mechanism, reload the dashboard, and verify an explicit
  degraded message with no fallback order/data mutation. Restore the Demo
  stack afterward.

- [ ] **Step 5: Run compile, Ruff format check, and the relevant regression suites.**

  Run:
  `py -3.12 -m compileall -q src tests`
  `py -3.12 -m ruff check src tests indicators filters mt5_account`
  `py -3.12 -m ruff format --check src tests indicators filters mt5_account`
  `py -3.12 -m pytest tests\python\dashboard tests\python\api tests\python\test_demo_readiness.py -q --import-mode=importlib`

- [ ] **Step 6: Inspect the root and changed paths for task-created artifacts.**

  Confirm no browser traces, temporary files, credentials, or caches were added
  to the repository root; remove only artifacts created by this task.

- [ ] **Step 7: Record verification results and commit the final test-only/documentation evidence.**

  Run:
  `git status --short`
  then commit only intentional source, test, and documentation files with:
  `git commit -m "test: verify dashboard demo integration"`
