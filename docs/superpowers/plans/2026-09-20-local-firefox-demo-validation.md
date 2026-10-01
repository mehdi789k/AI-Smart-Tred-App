# Local Firefox and Demo Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and, where necessary, repair the Windows Local Demo stack and Streamlit dashboard in Firefox, while preserving fail-closed trading safety and requiring a separate operator gate for one bounded Demo canary.

**Architecture:** Use the existing PowerShell startup path to run Docker API/database/observability services, the host Streamlit dashboard, and the optional MT5 market-data collector. Keep the dashboard and API in read-only mode through automated and browser checks first; only after those checks pass, inspect the already-open MT5 terminal through the existing connector and evaluate the explicit canary gates. Any repair remains local to the failing boundary and is covered by a targeted regression test.

**Tech Stack:** Windows PowerShell, Python 3.12, pytest, FastAPI, Streamlit, Docker Compose, Firefox, MetaTrader5 host connector, MQL5 safety policy.

**Spec:** `docs/superpowers/specs/2026-09-20-local-firefox-demo-validation-design.md`

## Global Constraints

- Keep `MT5_AUTO_TRADING_ENABLED=false` until the canary phase.
- Never use Live credentials or a Live account.
- Never place an order without a final operator confirmation immediately before submission.
- Do not put credentials in source files, screenshots, reports, fixtures, or chat.
- Preserve unknown broker outcomes for reconciliation; never retry them automatically.
- Stop and request operator input if the account is not clearly Demo, the symbol differs from the approved Market Watch symbol, protective levels are missing, or any state is ambiguous.
- Use Windows paths for commands and existing project directories for artifacts.
- Do not modify `/src/mql5/` unless a reproducible validation failure is directly caused by an MQL5 change and explicit permission is obtained.

---

### Task 1: Establish the automated baseline

**Files:**
- Read: `README.md`
- Read: `docs/API_CONTRACT.md`
- Read: `docs/DATA_CONTRACT.md`
- Read: `src/python/dashboard/app.py`
- Read: `tests/python/dashboard/test_dashboard_smoke.py`
- Read: `tests/python/dashboard/test_dashboard_integration.py`
- Read: `tests/python/dashboard/test_mt5_connector.py`
- Read: `tests/e2e/test_litefinance_demo_contract.py`
- Create: `reports/` only if an existing reporting convention requires a persisted report; otherwise keep command output in the session.

**Interfaces:**
- Consumes: current source, test, and contract files.
- Produces: fresh baseline evidence for dashboard, integration, connector, and Demo contract behavior; no code changes.

- [ ] **Step 1: Confirm the working tree and runtime prerequisites**

Run from the project root:

```powershell
Get-Location
py -3.12 --version
docker compose version
Get-Command firefox -ErrorAction SilentlyContinue
Test-Path .env
```

Expected: project root is `D:\Nojom mali\robat metatreder5\AI Smart Tred App`; Python 3.12 is available; Docker Compose is available; Firefox is either discoverable or its installed path is identified; `.env` status is recorded without printing secrets.

- [ ] **Step 2: Run the targeted dashboard and Demo contract tests**

```powershell
py -3.12 -m pytest `
  tests\python\dashboard `
  tests\e2e\test_litefinance_demo_contract.py `
  -q --import-mode=importlib
```

Expected: tests pass or failures are captured exactly; no order is sent by these tests.

- [ ] **Step 3: Run the relevant static checks**

```powershell
py -3.12 -m compileall -q src tests
py -3.12 -m ruff check src\python\dashboard tests\python\dashboard tests\e2e\test_litefinance_demo_contract.py
py -3.12 -m ruff format --check src\python\dashboard tests\python\dashboard
```

Expected: exit code 0, or a precise list of pre-existing/relevant failures for diagnosis.

- [ ] **Step 4: Record baseline status in the session todo database**

Mark `baseline-validation` as `done` only after the commands finish and store the command results in the final evidence summary. If a prerequisite is missing, mark it `blocked` with the exact missing dependency rather than silently proceeding.

---

### Task 2: Start and verify the Windows Local Demo stack

**Files:**
- Read: `scripts/start_local_demo.ps1`
- Read: `scripts/stop_local_demo.ps1`
- Read: `docker-compose.yml`
- Read: `logs/local_demo_startup.log`
- Read: `logs/dashboard_windows.err.log`
- Read: `logs/dashboard_windows.out.log`
- Read: `logs/market_watch_windows.err.log`
- Read: `logs/market_watch_windows.out.log`

**Interfaces:**
- Consumes: baseline evidence and local environment.
- Produces: healthy local API at `http://127.0.0.1:8000`, dashboard at `http://127.0.0.1:8501`, and running service/process evidence with trading disabled.

- [ ] **Step 1: Validate compose configuration without changing services**

```powershell
docker compose config
```

Expected: valid configuration. Do not expose or print `.env` values.

- [ ] **Step 2: Run the documented startup script**

```powershell
Set-Location -LiteralPath 'D:\Nojom mali\robat metatreder5\AI Smart Tred App'
.\scripts\start_local_demo.ps1
```

Expected: Docker services start, API health becomes HTTP 200, Streamlit health becomes HTTP 200, dashboard smoke tests pass, and `MT5_AUTO_TRADING_ENABLED` remains false.

- [ ] **Step 3: Verify service and process health**

```powershell
docker compose ps
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/ready
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8501/_stcore/health
Get-Content logs\dashboard_windows.pid
Get-Content logs\local_demo_startup.log -Tail 40
```

Expected: API health and dashboard health succeed; readiness either succeeds or reports a documented dependency issue; PID files refer to live processes.

- [ ] **Step 4: Inspect only relevant new errors**

```powershell
Get-Content logs\dashboard_windows.err.log -Tail 100
Get-Content logs\market_watch_windows.err.log -Tail 100
Get-Content logs\errors_20260919.log -Tail 100
```

Expected: any error is classified as startup blocker, expected MT5-unavailable warning, or unrelated historical evidence.

- [ ] **Step 5: Mark local startup complete**

Mark `local-stack-startup` as `done` only when both health endpoints and the dashboard smoke test pass. If Docker or Python is unavailable, stop and ask for the missing prerequisite rather than enabling live behavior.

---

### Task 3: Exercise every dashboard page in Firefox

**Files:**
- Read: `src/python/dashboard/app.py`
- Read: `tests/python/dashboard/test_dashboard_smoke.py`
- Read: `src/python/dashboard/mt5_connector.py`
- Read: `src/python/dashboard/data_manager.py`
- Create: browser evidence under the session artifact directory, not the repository root.

**Interfaces:**
- Consumes: healthy dashboard URL from Task 2.
- Produces: browser evidence for each sidebar page, read-only controls, visible errors, and console/network failures.

- [ ] **Step 1: Open Firefox at the dashboard**

Use the browser integration to open `http://127.0.0.1:8501` in Firefox. If Firefox is not installed or cannot be automated, report the exact blocker and continue only with the approved non-browser checks.

- [ ] **Step 2: Enumerate sidebar navigation options**

Read the accessibility snapshot and record the exact sidebar radio/button labels. Do not infer page names from source code when the running UI exposes different labels.

- [ ] **Step 3: Test each page in a fresh navigation state**

For each sidebar option:

1. Select the option.
2. Wait for the page to settle.
3. Read the accessibility tree.
4. Capture console errors and failed API/network requests.
5. Verify that unavailable MT5 data is represented by an explicit status/error/empty state rather than an unhandled exception.

- [ ] **Step 4: Exercise safe controls only**

Test refresh, filters, symbol/timeframe selectors, model artifact selectors, export/download controls, and connection controls when present. Do not click any control whose label or effect can submit, enable, or confirm an order.

- [ ] **Step 5: Mark browser validation**

Mark `firefox-dashboard-testing` as `done` only when all pages are visited and each failure is either absent, explicitly rendered, or recorded as a repair candidate with page and control name.

---

### Task 4: Repair reproducible validation blockers

**Files:**
- Modify: only the source file owning the reproduced failure, most likely `src/python/dashboard/app.py`, `src/python/dashboard/mt5_connector.py`, or `src/python/dashboard/data_manager.py`
- Test: the nearest existing dashboard test file, or a new focused test under `tests/python/dashboard/` when no suitable test exists
- Modify: `docs/API_CONTRACT.md`, `docs/DATA_CONTRACT.md`, or `docs/ARCHITECTURE.md` only if the repaired behavior changes a documented contract

**Interfaces:**
- Consumes: a concrete failing browser/test case from Task 3.
- Produces: a surgical fix with a regression test and unchanged fail-closed execution behavior.

- [ ] **Step 1: Convert the first reproducible failure into a failing regression test**

Use the smallest existing test harness. For a dashboard render failure, follow the existing `AppTest.from_file(...).run()` pattern. For connector behavior, use the existing mock/fixture conventions in `test_mt5_connector.py`. The test must assert the observable failure behavior, not implementation details.

- [ ] **Step 2: Reproduce the failure with the smallest applicable dashboard test file**

Run the existing dashboard test file that owns the reproduced behavior, for example:

```powershell
py -3.12 -m pytest tests\python\dashboard\test_dashboard_smoke.py -q
```

Expected: FAIL for the original defect. If the owning test file is different, use that exact existing file instead; if it passes, discard the proposed fix and gather a more exact reproduction.

- [ ] **Step 3: Implement the minimal root-cause fix**

Preserve explicit logging and user-visible error states. Do not add broad exception catches, silent defaults, automatic order retries, or credential handling.

- [ ] **Step 4: Run the focused and neighboring checks**

```powershell
py -3.12 -m pytest tests\python\dashboard\test_dashboard_smoke.py -q
py -3.12 -m pytest tests\python\dashboard -q --import-mode=importlib
```

If the regression belongs to `test_dashboard_integration.py` or
`test_mt5_connector.py`, run that exact existing file instead of the smoke
test in the first command.

Expected: focused regression and dashboard suite pass.

- [ ] **Step 5: Re-run Firefox on the repaired flow**

Repeat the exact browser interaction that exposed the defect, then revisit all sidebar pages to ensure the fix did not regress navigation.

- [ ] **Step 6: Mark repairs complete**

Mark `targeted-repairs` as `done` only after the regression test and browser reproduction both pass. If no defect is found, mark it done with evidence that no code change was required.

---

### Task 5: Perform MT5 read-only verification and canary gate

**Files:**
- Read: `src/python/dashboard/mt5_connector.py`
- Read: `src/python/execution/policy.py`
- Read: `src/python/execution/live_order_workflow.py`
- Read: `src/mql5/ExecutionPolicy.mqh`
- Read: `ops/mt5/litefinance-demo-runbook.md`
- Read: `ops/mt5/SmartTraderEA.demo.set`

**Interfaces:**
- Consumes: repaired, healthy dashboard and an operator-open MT5 terminal.
- Produces: read-only account/terminal/symbol evidence and a gate decision; no order submission until a separate final confirmation.

- [ ] **Step 1: Ask for terminal readiness without requesting credentials in chat**

Ask the operator to open and log into the Demo account in MT5, confirm the terminal visibly labels it Demo, attach the EA only with `InpAllowLiveTrading=false`, and state the exact Market Watch symbol intended for the canary. Never request the password in chat.

- [ ] **Step 2: Verify read-only connection**

Use the dashboard connection control or existing connector path to read terminal/account status, approved symbol quote, positions, and order history. Record only redacted status, symbol, timestamps, and result codes.

- [ ] **Step 3: Verify every gate**

Confirm all of the following before canary consideration:

```text
account_is_demo = true
approved_symbol = exact Market Watch symbol
max_volume <= 0.01
daily_loss_limit is configured
symbol whitelist contains only approved symbol
magic number matches 26090901
SL and TP are present and valid
automatic retry is disabled
MT5_AUTO_TRADING_ENABLED is explicitly enabled only for the canary window
```

- [ ] **Step 4: Stop on any ambiguity**

If the account, symbol, protective levels, broker state, or connection is uncertain, keep execution disabled, mark `demo-canary-gate` as `blocked`, and request the exact operator decision needed.

- [ ] **Step 5: Request final canary confirmation**

Immediately before any order call, ask for confirmation of the exact symbol, direction, volume, entry context, SL, TP, and maximum accepted loss. Do not reuse an earlier general approval as this final confirmation.

---

### Task 6: Execute and reconcile the bounded Demo canary

**Files:**
- Read: `src/python/execution/live_order_workflow.py`
- Read: `src/python/execution/zmq_gateway.py`
- Read: `src/python/execution/reconciliation.py`
- Read: `src/python/risk/manager.py`
- Read: `src/mql5/OrderManager.mqh`
- Read: `src/mql5/RiskManager.mqh`
- Write: existing append-only audit/ledger locations only; never write credentials

**Interfaces:**
- Consumes: a completed canary gate and final operator confirmation from Task 5.
- Produces: exactly zero or one bounded Demo order result, plus reconciliation evidence; unknown results remain unresolved.

- [ ] **Step 1: Enable only the approved canary flags**

Set the minimum required environment/input values for the approved Demo window. Keep legacy order paths disabled and keep all symbols except the approved exact symbol blocked. Verify the effective configuration before submission.

- [ ] **Step 2: Submit exactly one bounded order**

Invoke the existing `LiveOrderWorkflow`/dashboard execution path, not a direct broker API call. Enforce volume `<= 0.01`, mandatory SL/TP, magic `26090901`, symbol whitelist, daily-loss circuit breaker, idempotency, and no retry.

- [ ] **Step 3: Capture the broker result**

Record only redacted correlation/order/deal identifiers, response classification, UTC timestamp, symbol, volume, and protective-level confirmation. Never log credentials.

- [ ] **Step 4: Reconcile or stop**

For accepted results, verify the broker position/order read-only. For timeout/unknown results, do not retry; use the existing reconciliation workflow and request operator review. For rejection or any mismatch, activate the documented stop/circuit-breaker procedure.

- [ ] **Step 5: Disable execution immediately**

Restore `MT5_AUTO_TRADING_ENABLED=false`, `InpAllowLiveTrading=false`, and `InpEnableZmq=false` after the canary evidence is captured. Verify no second order was submitted.

- [ ] **Step 6: Mark canary status**

Mark `demo-canary-execution` as `done` only with broker/reconciliation evidence. Mark it `blocked` if the gate or final confirmation was not granted; do not treat blocked as a failure of the software.

---

### Task 7: Final verification and cleanup

**Files:**
- Read: `logs/`
- Read: `data/`
- Read: `models/`
- Read: `reports/`
- Read: `.env`
- Read: root directory listing

**Interfaces:**
- Consumes: all task evidence.
- Produces: final Persian status report distinguishing automated tests, Firefox tests, MT5 read-only checks, and canary evidence.

- [ ] **Step 1: Re-run the targeted final checks**

```powershell
py -3.12 -m pytest tests\python\dashboard tests\e2e\test_litefinance_demo_contract.py -q --import-mode=importlib
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8501/_stcore/health
```

- [ ] **Step 2: Verify execution is disabled after the run**

Check effective environment and EA settings without printing secrets:

```powershell
Write-Output "MT5_AUTO_TRADING_ENABLED=$env:MT5_AUTO_TRADING_ENABLED"
Write-Output "MT5_LEGACY_ORDER_PATH_ENABLED=$env:MT5_LEGACY_ORDER_PATH_ENABLED"
Select-String -Path ops\mt5\SmartTraderEA.demo.set -Pattern "InpAllowLiveTrading|InpEnableZmq"
```

Expected: execution remains disabled unless the operator explicitly chose to leave a controlled Demo session open; if left open, state that clearly and request cleanup confirmation.

- [ ] **Step 3: Check root hygiene**

```powershell
Get-ChildItem -Force
Get-ChildItem reports -ErrorAction SilentlyContinue
```

Remove only artifacts created by this validation that are explicitly temporary and known by path. Do not delete user-owned logs, databases, models, audit records, or evidence.

- [ ] **Step 4: Report outcome with evidence**

Include commands, pass/fail status, repaired files, browser pages tested, MT5 availability, canary result, unresolved blockers, and the exact safety state at shutdown. Link every referenced repository file using absolute Markdown links in the response.
