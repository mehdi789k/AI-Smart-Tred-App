# Demo Auto-Trading Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start guarded automatic trading from the Windows Dashboard for a verified MT5 Demo account without requiring manual environment edits, while remaining fail-closed for Live, unavailable, stale, mismatched, or unsafe states.

**Architecture:** Keep the API container read-only with respect to MT5 execution and make the host Dashboard the only automatic-start owner. Extend the existing MT5 connector and readiness model with explicit Demo-account identity and fresh-symbol checks, then let the Dashboard invoke the existing `LiveTradingLoop.start()` only after all gates pass. Preserve the existing workflow, circuit breaker, confirmation-token, audit, and legacy-path protections; automatic startup supplies a server-side Demo-only authorization rather than weakening those controls.

**Tech Stack:** Python 3.12, Streamlit, MetaTrader5 Python package, pytest, PowerShell, existing `LiveTradingLoop`, `LiveOrderWorkflow`, Docker Compose, Markdown documentation.

**Spec:** `docs/superpowers/specs/2026-09-22-dashboard-env-management-design.md` plus the approved in-chat design: automatic Dashboard startup only after verified Demo identity and complete readiness; Live remains explicitly gated; Demo limits remain 0.01 volume, 3 trades/session, and daily loss <= 10.

## Global Constraints

- MT5 side uses strict MQL5 build 4000+ and existing EA/ZeroMQ contracts; do not change the MQL5 ownership boundary.
- Backend remains Python 3.12/FastAPI/SQLAlchemy 2.0 with existing Docker/API separation.
- All order paths remain fail-closed and use `LiveOrderWorkflow`; `MT5_LEGACY_ORDER_PATH_ENABLED` remains `false`.
- Automatic startup is allowed only for a definitively identified Demo account; Live startup still requires explicit operator confirmation.
- Demo guardrails remain maximum volume `0.01`, maximum three trades per session, maximum daily loss `10`, and automatic stop on error/unknown outcome/disconnect.
- No credentials may be logged, committed, or exposed in UI/audit output.
- Do not modify files outside the assigned ownership boundaries; Dashboard/execution/scripts/docs/tests are in scope, MQL5 files are not.
- Every changed public behavior requires focused pytest coverage and related documentation updates.

---

### Task 1: Define explicit Demo-account identity and readiness result

**Files:**
- Modify: `src/python/execution/live_readiness.py`
- Modify: `src/python/dashboard/mt5_connector.py`
- Test: `tests/python/execution/test_live_readiness.py`
- Test: `tests/python/dashboard/test_mt5_connector.py` (create if absent)

**Interfaces:**
- Consumes: existing connector methods `is_connected()`, `get_account_summary()`, `get_symbols_list()`, and `get_symbol_info()`.
- Produces: `LiveReadinessReport.account` with a redacted `trade_mode`/Demo classification and a readiness reason `account_not_demo`; connector method `is_demo_account() -> bool` or equivalent typed helper used by Dashboard startup.

- [ ] **Step 1: Write failing readiness tests** for an account explicitly marked Demo, an account marked Real/Contest, missing account trade mode, login/server mismatch, invisible symbol, and stale/invalid tick. Assert Demo is the only account type that can produce `ready=True`.
- [ ] **Step 2: Run the focused tests**:
  `py -3.12 -m pytest tests\python\execution\test_live_readiness.py tests\python\dashboard\test_mt5_connector.py -q`
  Expected: new Demo identity tests fail before implementation.
- [ ] **Step 3: Implement the smallest typed identity normalization** that accepts the MetaTrader5 account trade-mode constants/values used by the installed package and rejects unknown values. Do not infer Demo from broker name, `.env`, or a missing field.
- [ ] **Step 4: Include only redacted account identity fields** in readiness output and preserve existing exception handling; no password, token, or full account secret may enter logs.
- [ ] **Step 5: Run the focused tests again** and confirm all existing readiness tests remain green.
- [ ] **Step 6: Commit**:
  `git add src\python\execution\live_readiness.py src\python\dashboard\mt5_connector.py tests\python\execution\test_live_readiness.py tests\python\dashboard\test_mt5_connector.py`
  `git commit -m "feat: require explicit demo account readiness"`

### Task 2: Make the Demo profile self-configuring but bounded

**Files:**
- Modify: `src/python/dashboard/env_manager.py`
- Modify: `scripts/start_local_demo.ps1`
- Modify: `tests/python/test_project_configuration.py`
- Modify: `tests/powershell/start_local_demo.Tests.ps1`

**Interfaces:**
- Consumes: existing `.env` secret values and existing Demo profile reset mechanism.
- Produces: a host-process Demo profile that enables MT5 access and the guarded automatic loop without changing credentials or Live settings.

- [ ] **Step 1: Add failing tests** asserting `build_demo_profile()` returns `MT5_ENABLED=true`, `MT5_DEMO_ENABLED=true`, `MT5_AUTO_TRADING_ENABLED=true`, `MT5_LEGACY_ORDER_PATH_ENABLED=false`, `MT5_DEMO_MAX_TRADE_VOLUME=0.01`, `MT5_DEMO_MAX_TRADES_PER_SESSION=3`, `MT5_DEMO_MAX_DAILY_LOSS=10`, `MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION=true`, and `MT5_DEMO_AUTO_STOP_ON_ERROR=true`.
- [ ] **Step 2: Add a PowerShell script regression test** proving the Demo branch applies these process values and does not enable the Live branch.
- [ ] **Step 3: Run only the configuration and PowerShell tests**; confirm the new expectations fail against the current profile.
- [ ] **Step 4: Update the Demo profile and startup script** so values are applied to the Dashboard host process after `.env` loading, while preserving all existing `.env` secrets and refusing to auto-enable Live mode.
- [ ] **Step 5: Add explicit startup logging of non-secret mode/guardrail state** and never log credential values.
- [ ] **Step 6: Run the focused tests and `docker compose config --quiet`** to verify the profile does not break Compose interpolation.
- [ ] **Step 7: Commit**:
  `git add src\python\dashboard\env_manager.py scripts\start_local_demo.ps1 tests\python\test_project_configuration.py tests\powershell\start_local_demo.Tests.ps1`
  `git commit -m "feat: enable bounded demo profile by default"`

### Task 3: Start the loop automatically only after complete Demo readiness

**Files:**
- Modify: `src/python/execution/automated_loop.py`
- Modify: `src/python/dashboard/app.py`
- Modify: `src/python/dashboard/mt5_connector.py`
- Test: `tests/python/execution/test_automated_loop.py`
- Test: `tests/python/dashboard/test_dashboard_execution_fragment.py`

**Interfaces:**
- Consumes: Task 1 readiness report, Dashboard MT5 connector, existing `LiveTradingLoop.start()` and `restore_active()`.
- Produces: an idempotent Dashboard startup helper such as `start_demo_automatically() -> bool` that starts exactly once per active authorization lease and returns false with an auditable reason when any gate fails.

- [ ] **Step 1: Write failing tests** for automatic start on verified Demo readiness, no start on Real/unknown account, no start on stale tick or unavailable MT5, no duplicate start across Streamlit reruns, and automatic stop after disconnect or broker-disabled latch.
- [ ] **Step 2: Run the focused Dashboard/execution tests** and confirm the new behavior fails before implementation.
- [ ] **Step 3: Implement a single startup decision point** in the existing Dashboard execution fragment. It must:
  1. load/validate the Demo profile;
  2. connect to MT5;
  3. call readiness;
  4. require `report.ready` and explicit Demo identity;
  5. invoke the existing workflow confirmation mechanism with a generated, non-user-secret Demo authorization token;
  6. call `LiveTradingLoop.start()` once;
  7. persist/restore only the existing bounded authorization state.
- [ ] **Step 4: Ensure the helper never calls an order endpoint during readiness and never bypasses `LiveOrderWorkflow`, risk limits, confirmation, or reconciliation.
- [ ] **Step 5: Add user-visible Dashboard states**: `Demo auto-trading active`, `waiting for Demo MT5 readiness`, and `blocked: account is not verified Demo` with timestamps and safe reason codes.
- [ ] **Step 6: Run focused tests, then the complete execution/dashboard test subset**:
  `py -3.12 -m pytest tests\python\execution tests\python\dashboard -q`
- [ ] **Step 7: Commit**:
  `git add src\python\execution\automated_loop.py src\python\dashboard\app.py src\python\dashboard\mt5_connector.py tests\python\execution\test_automated_loop.py tests\python\dashboard\test_dashboard_execution_fragment.py`
  `git commit -m "feat: auto-start guarded demo trading from dashboard"`

### Task 4: Verify the host-to-MT5 Demo seam without real-risk escalation

**Files:**
- Modify: `scripts/verify_demo_readiness.py`
- Modify: `tests/python/test_demo_readiness.py`
- Modify: `tests/e2e/test_litefinance_demo_contract.py`
- Modify: `ops/mt5/litefinance-demo-runbook.md`

**Interfaces:**
- Consumes: the readiness payload produced by Task 1 and the Dashboard host process from Task 3.
- Produces: a readiness command that distinguishes “services healthy but MT5 unavailable” from “Demo trading ready,” and a documented no-order/controlled-canary verification path.

- [ ] **Step 1: Add failing tests** requiring explicit Demo identity in `validate_demo_readiness()` when automatic trading is requested, while retaining a separate read-only direct-dashboard health mode.
- [ ] **Step 2: Run the readiness and E2E contract tests** and confirm the stricter automatic mode fails safely on current unavailable MT5.
- [ ] **Step 3: Implement separate flags/results** for `--allow-direct-dashboard` read-only checks versus `--require-demo-trading` automatic activation checks; never let the former authorize orders.
- [ ] **Step 4: Update the runbook** with the new automatic-start prerequisites, exact safe stop conditions, and expected readiness states.
- [ ] **Step 5: Run the focused readiness/E2E tests and verify the command does not call an order endpoint.
- [ ] **Step 6: Commit**:
  `git add scripts\verify_demo_readiness.py tests\python\test_demo_readiness.py tests\e2e\test_litefinance_demo_contract.py ops\mt5\litefinance-demo-runbook.md`
  `git commit -m "test: separate demo readiness from read-only health"`

### Task 5: Update architecture and operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/API_CONTRACT.md` only if readiness payload fields change
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the final behavior and payload names from Tasks 1–4.
- Produces: documentation that accurately states automatic Demo startup, fail-closed Live behavior, guardrails, and the MT5 host/container boundary.

- [ ] **Step 1: Document the exact automatic-start state machine**: profile load → MT5 connect → Demo identity → account/server match → symbol/tick readiness → circuit breaker → loop start.
- [ ] **Step 2: Document that `ready` alone does not authorize trading and that `trading.allowed=true` is required for the automatic path.
- [ ] **Step 3: Document safe blocked states and the commands for observing health without submitting orders.
- [ ] **Step 4: Add the change to `CHANGELOG.md` without claiming broker execution evidence that has not been collected.
- [ ] **Step 5: Run documentation consistency checks if present; otherwise inspect the changed Markdown for stale claims.
- [ ] **Step 6: Commit**:
  `git add README.md docs\ARCHITECTURE.md docs\API_CONTRACT.md CHANGELOG.md`
  `git commit -m "docs: describe automatic guarded demo trading"`

### Task 6: Full verification and controlled runtime check

**Files:**
- No source changes expected; inspect generated logs only under `logs/`.

- [ ] **Step 1: Run project validation**:
  `py -3.12 -m pytest -q --import-mode=importlib`
  `py -3.12 -m compileall -q src tests`
  `py -3.12 -m ruff check src tests indicators filters mt5_account`
  `py -3.12 -m ruff format --check src tests indicators filters mt5_account`
- [ ] **Step 2: Run `docker compose config --quiet` and start the guarded local stack with `scripts\start_local_demo.ps1 -TradingMode Demo`.
- [ ] **Step 3: Verify `/health`, `/ready`, Dashboard health, and the redacted Dashboard status. Do not treat `mt5=unavailable` as Demo trading readiness.
- [ ] **Step 4: If a real Demo terminal is connected, run the documented controlled canary only after readiness reports explicit Demo identity and all limits are armed; capture broker identifiers and reconcile any unknown result. If MT5 is unavailable, stop with no order attempt.
- [ ] **Step 5: Record final evidence in the existing logs/reports directories and remove only task-created temporary artifacts.
- [ ] **Step 6: Commit only source/documentation/test changes; never commit `.env`, credentials, runtime logs, database files, screenshots, or broker identifiers.

---

## Self-review

- **Spec coverage:** Demo-only automatic startup is covered by Tasks 2–4; explicit identity and readiness by Task 1; fail-closed behavior and existing limits are preserved by Task 3; documentation and verification are covered by Tasks 5–6.
- **Placeholder scan:** No `TBD`, `TODO`, or unspecified implementation step is used; every task names files, interfaces, tests, commands, and expected outcomes.
- **Type consistency:** Task 1 produces `LiveReadinessReport.ready` and explicit Demo identity; Task 3 consumes that report and exposes `start_demo_automatically() -> bool`; Task 4 consumes the same readiness payload; Tasks 5–6 document and verify the resulting contract.
