# Local Firefox and Demo Validation Design

## Goal

Bring the existing Smart MT5 Trading System up on Windows, validate the
read-only dashboard in Firefox, repair defects that block the validation
workflow, and only then perform an explicitly bounded Demo canary if all
safety gates pass.

## Scope

- Inspect the existing contracts, startup scripts, dashboard pages, tests, and
  runtime logs before changing code.
- Run the smallest relevant automated baseline first.
- Start the documented local stack with live order execution disabled.
- Open the Streamlit dashboard at `http://127.0.0.1:8501` in Firefox.
- Exercise every dashboard navigation page and read-only control, including
  loading, empty, unavailable-MT5, and error states where the UI exposes them.
- Verify API health/readiness and inspect dashboard, API, collector, and error
  logs for actionable failures.
- Fix only defects that are reproducible and relevant to startup, dashboard
  behavior, or the approved validation flow; add regression coverage where
  practical.
- Use the existing MT5 connector for read-only account, quote, position, and
  order-history checks only after the local dashboard path is healthy.
- A Demo canary is a separate final phase, limited to one order, at most
  `0.01` lots, an operator-approved symbol visible in Market Watch, mandatory
  protective levels, no automatic retry, and immediate stop on any mismatch or
  uncertain broker result.

## Safety boundaries

- Keep `MT5_AUTO_TRADING_ENABLED=false` until the canary phase.
- Never use Live credentials or a Live account.
- Never place an order without a final operator confirmation immediately before
  submission.
- Do not put credentials in source files, screenshots, reports, fixtures, or
  chat.
- Preserve unknown broker outcomes for reconciliation; never retry them
  automatically.
- Stop and request operator input if the account is not clearly Demo, the
  symbol differs from the approved Market Watch symbol, protective levels are
  missing, or any state is ambiguous.

## Validation flow

1. Baseline: run dashboard smoke/integration tests and relevant project checks.
2. Startup: run the Windows local Demo startup path and verify process health.
3. Browser: use Firefox to inspect all sidebar pages and read-only interactions.
4. Diagnosis: correlate browser failures with API responses and runtime logs.
5. Repair: make surgical changes and rerun targeted tests plus browser checks.
6. MT5 read-only: connect through the configured terminal only if available.
7. Canary gate: verify all safety values and obtain final confirmation.
8. Canary execution: submit one bounded Demo order, capture broker result, and
   reconcile or stop on unknown status.
9. Cleanup: disable execution, stop temporary processes as appropriate, and
   verify no credentials or temporary artifacts were added to the project root.

## Success criteria

- The documented local services and dashboard are healthy on Windows.
- Every dashboard page renders in Firefox without an unhandled exception.
- Read-only controls have observable, correct outcomes or explicit error states.
- Any code fix is covered by targeted automated validation.
- No order is sent unless all canary gates pass and the operator confirms.
- The final report distinguishes automated, browser, MT5 read-only, and broker
  canary evidence.
