# Guarded Live Trading Readiness Design

## Goal

Keep the live-trading path enabled for the configured MT5 account while
preventing any real order from being submitted until the runtime connection,
account identity, market data, and persistent risk controls are verified.
The configured demo account remains test-only and must not be used as the
live target.

## Safety boundary

This workflow is split into two explicit phases:

1. **Readiness phase (no order submission):** connect to MT5, verify terminal
   and account metadata without logging credentials, verify the configured
   symbol whitelist and fresh ticks, and verify that the circuit breaker is
   armed and persisted.
2. **Execution phase (separate human confirmation):** present the verified
   account, broker server, symbol, volume, protective levels, and effective
   risk limits. No order endpoint or MT5 `order_send` call is allowed until a
   fresh, scoped confirmation is supplied.

The live gate remains enabled (`MT5_AUTO_TRADING_ENABLED=true` and
`MT5_DEMO_ENABLED=false`) after readiness succeeds. This does not bypass
workflow validation, circuit-breaker state, authorization, or confirmation
requirements.

## Components and data flow

- The Windows startup path loads the existing MT5 credentials from `.env`
  without printing them, sets the live environment flags, and runs readiness
  validation.
- The MT5 connector performs read-only initialization and collects
  `terminal_info`, `account_info`, symbol visibility, and a fresh tick.
- The API readiness surface reports MT5 connectivity and circuit-breaker
  state. An unavailable dependency must keep `trading.allowed` false.
- The execution workflow remains the sole order boundary. It validates
  symbol, magic number, position volume, spread, daily loss, session expiry,
  emergency stop, and a short-lived confirmation token before calling MT5.
- The circuit breaker is initialized from the verified account baseline and
  daily-loss policy, persisted through the existing execution-control store,
  and has no manual override in the live startup path.
- Dashboard and sensor checks verify current connection/data timestamps and
  expose stale or unavailable state rather than success-shaped fallbacks.

## Failure handling

- Missing credentials, failed initialization, account/server mismatch, stale
  ticks, absent symbol whitelist, missing risk limits, unavailable persistence,
  or a tripped breaker stop the workflow.
- Diagnostics must redact passwords and tokens.
- No retry may submit an order after an ambiguous broker response.
- Runtime failures are logged with correlation identifiers and leave the live
  order gate closed until a new readiness check succeeds.

## Verification

- Add or update regression tests for account-identity validation, stale/missing
  market data, circuit-breaker arming, and live-vs-demo environment flags.
- Run targeted Python tests for MT5 connectors, circuit breaker, execution
  workflow, API readiness, and dashboard integration.
- Run the read-only readiness command against the running services.
- Before any real order, show a final confirmation summary and require an
  explicit human approval for the exact symbol, direction, volume, and
  protective levels.
