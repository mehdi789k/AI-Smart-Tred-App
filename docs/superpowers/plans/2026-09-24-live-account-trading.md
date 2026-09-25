# Live and Demo MT5 Account Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an explicitly confirmed dashboard automation session to trade and manage market/limit orders on either demo or real MT5 accounts while preserving every existing risk and fail-closed gate.

**Architecture:** Replace the dashboard's Demo-only eligibility checks with a connected-account capability that reports the account mode without using it as an eligibility gate. Add read-only MT5 terminal/account/symbol readiness diagnostics before starting or restoring automation, then continue routing all orders through `LiveOrderWorkflow`, whose existing idempotency, risk validation, order management, and non-retryable broker latch remain authoritative.

**Tech Stack:** Python 3.12, Streamlit, MetaTrader5 Python API, pytest, ruff, existing `LiveTradingLoop`, `LiveOrderWorkflow`, and MT5 connector abstractions.

**Spec:** `docs/superpowers/specs/2026-09-24-live-account-trading-design.md`

## Global Constraints

- Do not bypass broker, terminal, account, or symbol trading permissions; retcode `10017` remains fail-closed and non-retryable.
- Keep `MT5_AUTO_TRADING_ENABLED=true` and explicit dashboard confirmation mandatory.
- Preserve symbol whitelist, magic number, maximum volume, daily-loss limit, protected SL/TP, durable execution control, session expiry, and emergency-stop behavior.
- Use the existing market-order, pending-limit, position-management, idempotency, and reconciliation paths; do not create a parallel live-order API.
- All write operations and durable state transitions must remain transactional.
- No credentials may be hardcoded; do not weaken environment-based configuration.
- Add regression tests before production changes and run targeted pytest plus ruff after each task.
- Do not modify `/src/mql5/` for this dashboard/backend change.

---

### Task 1: Replace Demo-only account eligibility with connected-account eligibility

**Files:**
- Modify: `src/python/dashboard/app.py:127-138, 1400-1415, 1600-1630, 2010-2040`
- Modify: `src/python/execution/live_readiness.py:45-105`
- Test: `tests/python/dashboard/test_dashboard_execution_fragment.py`
- Test: `tests/python/execution/test_live_readiness.py`

**Interfaces:**
- Consumes: `mt5_connector.is_connected()`, `mt5_connector.get_account_summary()`, and `mt5_connector.is_demo_account()`.
- Produces: a dashboard helper named `_active_trading_connector() -> bool` that returns true for any connected, readable MT5 account; `validate_live_readiness()` no longer adds `account_not_demo` solely for a real account, while preserving `account["trade_mode"]` as `demo`, `real`, or `unknown`.

- [ ] **Step 1: Write failing tests for real-account eligibility**

Add tests using a connector double with `is_connected() == True`, a readable account summary whose normalized trade mode is `real`, and visible configured symbols. Assert that the connector is eligible, the readiness report does not contain `account_not_demo`, and the report retains `account["trade_mode"] == "real"`.

Add a dashboard regression test that sets `MT5_AUTO_TRADING_ENABLED=true`, supplies a connected real-account connector, and asserts the live automation control is not disabled solely because `MT5_DEMO_ENABLED` is false. Keep the existing disconnected-account and server-gate tests.

- [ ] **Step 2: Run the focused tests and verify the expected failures**

Run:

```powershell
py -3.12 -m pytest tests/python/dashboard/test_dashboard_execution_fragment.py tests/python/execution/test_live_readiness.py -q
```

Expected: the new real-account tests fail because the dashboard and readiness logic currently require Demo mode.

- [ ] **Step 3: Implement the minimal account-mode change**

Rename the dashboard predicate to `_active_trading_connector()` and make it require only a connected connector with a readable account summary. Keep `is_demo_account()` available for display and diagnostics, but do not call it as an eligibility requirement.

Change the live start button's `live_trading_enabled` expression to depend on `MODULES_AVAILABLE` and `LiveTradingLoop.enabled_by_server()` only; the click handler must still reject a missing/disconnected connector.

In persisted live-session restore, replace the Demo-only check with `_active_trading_connector()` and use an error message that says a connected readable MT5 account is required.

In `validate_live_readiness()`, normalize the account trade mode from the connector summary, preserve it in the report, and remove the `account_not_demo` rejection while keeping identity checks and all market-data checks.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
py -3.12 -m pytest tests/python/dashboard/test_dashboard_execution_fragment.py tests/python/execution/test_live_readiness.py -q
```

Expected: all focused tests pass, including the new real-account cases.

- [ ] **Step 5: Commit the bounded account eligibility change**

```powershell
git add src/python/dashboard/app.py src/python/execution/live_readiness.py tests/python/dashboard/test_dashboard_execution_fragment.py tests/python/execution/test_live_readiness.py
git commit -m "fix: allow guarded trading on real MT5 accounts"
```

### Task 2: Add read-only terminal, account, and symbol trading readiness diagnostics

**Files:**
- Modify: `src/python/dashboard/mt5_connector.py:260-315`
- Modify: `src/python/execution/live_readiness.py:1-180`
- Modify: `src/python/dashboard/app.py` readiness/startup rendering near the live trading control
- Test: `tests/python/dashboard/test_mt5_connector.py`
- Test: `tests/python/execution/test_live_readiness.py`

**Interfaces:**
- Consumes: connector account/symbol methods and MT5 constants exposed by the injected module.
- Produces: `LiveReadinessReport.account` fields for `trade_mode`, `terminal_trade_allowed`, and `account_trade_allowed`; per-symbol fields for `trade_mode`, `volume_min`, `volume_max`, `volume_step`, `trade_stops_level`, and quote freshness; stable reasons such as `terminal_trade_disabled`, `account_trade_disabled`, and `<SYMBOL>:trade_disabled`.

- [ ] **Step 1: Write failing readiness tests**

Create connector/module doubles with explicit terminal and account permission flags and symbol metadata. Add one test asserting a ready real account when terminal/account/symbol permissions are enabled. Add one test asserting `terminal_trade_disabled` when terminal trading is disabled. Add one test asserting `account_trade_disabled` when account trading is disabled. Add one test asserting `<symbol>:trade_disabled` when the symbol trade mode is disabled.

- [ ] **Step 2: Run the readiness tests and verify they fail**

Run:

```powershell
py -3.12 -m pytest tests/python/execution/test_live_readiness.py tests/python/dashboard/test_mt5_connector.py -q
```

Expected: failures show the report currently lacks the permission fields and stable restriction reasons.

- [ ] **Step 3: Extend connector summaries without sending orders**

Expose terminal and account permission values from `terminal_info()` and `account_info()` through the existing connector summary/read-only methods. Normalize optional fields defensively; if a broker or test double does not expose a field, record `None` and let readiness report an explicit unavailable reason rather than treating it as allowed.

Expose symbol trade mode and order-volume/stop-distance metadata through the existing `get_symbol_info()` path. Do not call `order_send`, `order_check`, or any modifying API from readiness.

- [ ] **Step 4: Implement fail-closed readiness reasons**

Update `validate_live_readiness()` to:

```python
if terminal_trade_allowed is False:
    reasons.append("terminal_trade_disabled")
if account_trade_allowed is False:
    reasons.append("account_trade_disabled")
if symbol_trade_mode_is_disabled:
    reasons.append(f"{symbol}:trade_disabled")
```

Keep unknown/unavailable values distinguishable from `False`; unknown metadata must produce an explicit unavailable diagnostic where the existing contract requires it, not a success-shaped default.

- [ ] **Step 5: Render readiness diagnostics before automation confirmation**

Show the account mode and the exact readiness reasons near the dashboard trading controls. If a selected symbol is disabled, show the symbol name and its trade mode. Do not allow the confirmation flow to start when readiness is not ready.

- [ ] **Step 6: Run focused tests and lint**

Run:

```powershell
py -3.12 -m pytest tests/python/execution/test_live_readiness.py tests/python/dashboard/test_mt5_connector.py tests/python/dashboard/test_dashboard_execution_fragment.py -q
py -3.12 -m ruff check src/python/execution/live_readiness.py src/python/dashboard/mt5_connector.py src/python/dashboard/app.py tests/python/execution/test_live_readiness.py tests/python/dashboard/test_mt5_connector.py tests/python/dashboard/test_dashboard_execution_fragment.py
```

Expected: all tests pass and ruff reports no errors.

- [ ] **Step 7: Commit readiness diagnostics**

```powershell
git add src/python/execution/live_readiness.py src/python/dashboard/mt5_connector.py src/python/dashboard/app.py tests/python/execution/test_live_readiness.py tests/python/dashboard/test_mt5_connector.py tests/python/dashboard/test_dashboard_execution_fragment.py
git commit -m "feat: expose MT5 trading readiness diagnostics"
```

### Task 3: Verify market, pending-limit, and management behavior for both account modes

**Files:**
- Modify: `src/python/execution/live_order_workflow.py` only if readiness integration requires a tightly coupled guard
- Modify: `src/python/execution/automated_loop.py` only if diagnostics need to be surfaced in cycle errors
- Test: `tests/python/execution/test_live_order_workflow.py`
- Test: `tests/python/execution/test_automated_loop.py`
- Test: `tests/python/execution/test_live_order_workflow.py` pending-order and position-management sections

**Interfaces:**
- Consumes: existing `LiveOrderWorkflow.execute_market_order()`, `execute_pending_order()`, `modify_position_stop()`, and `LiveTradingLoop`.
- Produces: regression evidence that account mode does not alter the execution API and that retcode `10017` remains a terminal broker-disabled outcome.

- [ ] **Step 1: Add failing regression tests for both account modes**

Parameterize the existing workflow connector tests with `demo` and `real` account summaries where the workflow is constructed through the dashboard-facing path. Assert that a valid market order and a valid pending limit order reach the same workflow methods for either mode. Add a management test that updates a position stop and confirms the request uses the configured magic number and symbol.

Add a `10017` test that asserts `LiveOrderRejected.retcode == 10017`, the message contains the exact MT5 comment and the diagnostic guidance, and the automated loop status becomes `broker_trading_disabled` without retrying.

- [ ] **Step 2: Run the new execution tests and verify failures**

Run:

```powershell
py -3.12 -m pytest tests/python/execution/test_live_order_workflow.py tests/python/execution/test_automated_loop.py -q
```

Expected: any failures must be caused by the new account-mode or management assertion, not by an invalid fixture.

- [ ] **Step 3: Make only tightly coupled execution changes**

Do not add a separate real-account order path. If a readiness result must be passed into startup, add a typed guard before `workflow.confirm_automation()`/`loop.start()`. Leave `_send_checked()` as the single broker-send path, preserve durable audit fields, and keep `10017`, `10026`, and `10027` non-retryable.

- [ ] **Step 4: Run the complete execution regression set**

Run:

```powershell
py -3.12 -m pytest tests/python/execution/test_live_order_workflow.py tests/python/execution/test_automated_loop.py -q
```

Expected: all market, pending, position-management, rejection, idempotency, and broker-latch tests pass.

- [ ] **Step 5: Commit execution verification/fixes**

```powershell
git add src/python/execution/live_order_workflow.py src/python/execution/automated_loop.py tests/python/execution/test_live_order_workflow.py tests/python/execution/test_automated_loop.py
git commit -m "test: preserve guarded order management for all MT5 accounts"
```

### Task 4: Update contracts and run the full verification suite

**Files:**
- Modify: `docs/API_CONTRACT.md` live execution/readiness sections
- Modify: `docs/DATA_CONTRACT.md` account/readiness and rejection fields
- Modify: `docs/ARCHITECTURE.md` only if the current architecture description states Demo-only execution
- Test: existing Python execution/dashboard test suites

**Interfaces:**
- Consumes: the final readiness report fields and rejection/audit payload shape from Tasks 1-3.
- Produces: documentation that states Demo and Real are both supported when MT5 readiness and safety gates pass, while broker-disabled accounts remain blocked.

- [ ] **Step 1: Update the API contract**

Document that account trade mode is informational (`demo`, `real`, or `unknown`) and is not itself an execution rejection. Document readiness failure reasons for terminal/account/symbol trade permissions and the existing `10017` non-retryable behavior.

- [ ] **Step 2: Update the data contract**

Document the readiness account/symbol fields and that `order_rejected` audit records include `retcode`, exact broker `reason`, and user-facing `rejection_message`.

- [ ] **Step 3: Run targeted and full relevant tests**

Run:

```powershell
py -3.12 -m pytest tests/python/execution tests/python/dashboard tests/python/api -q
py -3.12 -m ruff check src/python tests/python
git diff --check
```

Expected: pytest exits with zero failures, ruff exits cleanly, and `git diff --check` reports no whitespace errors.

- [ ] **Step 4: Inspect the final worktree**

Run:

```powershell
git --no-pager status --short
git --no-pager diff --stat
```

Confirm only the intended source, test, and documentation paths changed. Do not remove unrelated pre-existing user changes or runtime logs.

- [ ] **Step 5: Commit documentation and final verification**

```powershell
git add docs/API_CONTRACT.md docs/DATA_CONTRACT.md docs/ARCHITECTURE.md
git commit -m "docs: document demo and real MT5 readiness"
```
