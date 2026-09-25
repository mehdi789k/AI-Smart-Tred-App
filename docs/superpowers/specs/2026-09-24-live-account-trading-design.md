# Live and Demo MT5 Account Trading Design

## Goal

Allow the dashboard's explicitly confirmed automated trading session to use
either a demo or real MT5 account. The system must continue to rely on MT5's
own account, terminal, broker, and symbol permissions and must not weaken the
existing risk gates.

## Current root cause

The dashboard has an intentional Demo-only policy in multiple independent
locations:

- `_active_demo_connector()` rejects non-demo accounts.
- Persisted live-session restore requires `_active_demo_connector()`.
- The Start Auto Trading control requires `MT5_DEMO_ENABLED`.
- `validate_live_readiness()` reports `account_not_demo`.

Separately, MT5 retcode `10017` means trading is disabled somewhere in the
broker/account/terminal/symbol chain. Removing the dashboard Demo-only gate
must not bypass this broker-side restriction.

## Design

### Account eligibility

Replace Demo-only eligibility with a connected, readable MT5 trading account
check. Preserve the account trade mode (`demo`, `real`, or `unknown`) as
observable readiness metadata and logs, but do not reject an account solely
because it is real.

The following gates remain mandatory:

- `MT5_AUTO_TRADING_ENABLED=true`
- explicit dashboard confirmation
- connected MT5 terminal and readable account
- symbol whitelist
- valid quote and symbol trading metadata
- magic number validation
- maximum volume and daily-loss limits
- protected SL/TP policy
- durable execution-control state and session expiry

### MT5 readiness

Before starting or restoring automation, validate terminal/account/symbol
trading metadata without submitting an order. Readiness must expose enough
diagnostic data to distinguish:

- terminal trade permission disabled
- account trade permission disabled
- symbol trade mode disabled
- missing or stale quote
- invalid volume or stop-distance constraints

Readiness failures remain fail-closed. The dashboard should show the account
mode and the exact readiness reason.

### Execution and management

Use the existing `LiveOrderWorkflow` for both account modes. Preserve market
orders, pending limit orders, position stop modification, break-even, partial
close, durable idempotency, and unknown-outcome reconciliation.

Retcode `10017` remains non-retryable and activates the broker-disabled latch.
The user-facing message must retain the exact MT5 comment, retcode, symbol, and
diagnostic guidance.

### Validation

Add regression coverage for:

- real-account readiness being accepted when other gates pass
- persisted live-session restore on a real account
- dashboard start control not requiring Demo mode
- market and pending execution paths remaining available for both modes
- 10017 remaining fail-closed and non-retryable
- readiness diagnostics identifying terminal/account/symbol restrictions

Update API, data, or architecture documentation only where the externally
observable contract changes.
