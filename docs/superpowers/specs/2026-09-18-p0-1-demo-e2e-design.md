# P0.1 Demo E2E Execution Design

## Goal

Complete the real Python–ZeroMQ–EA–Demo broker path without allowing an order
unless every safety gate, durable control-store check, and final operator
confirmation succeeds.

## Scope

This design covers one bounded Demo `BUY` scenario for broker symbol
`XAUUSD_l`, volume `0.02`, stop loss one dollar below the current ask, and take
profit one and a half dollars above the current ask. It includes EA/ZeroMQ
transport validation, broker result classification, durable reconciliation,
runtime circuit-breaker evidence, and the final confirmation boundary.

It does not enable live trading globally, add automatic retries, or change the
signal-generation and ML subsystems.

## Safety invariants

- `MT5_AUTO_TRADING_ENABLED=false` remains the default and automated loops stay
  disabled during validation.
- Timeout, malformed response, and unknown broker outcome are never accepted and
  never retried automatically.
- Only `XAUUSD_l`, `BUY`, `0.02`, approved protection prices, and magic
  `26090901` are allowed for the bounded scenario.
- The daily-loss circuit breaker is `10 USD`; an unconfigured durable control
  store blocks execution.
- The final order-send boundary requires a short-lived confirmation token whose
  action hash includes symbol, side, volume, SL, and TP.
- No credential, account login, password, or server secret is written to
  source, fixtures, logs, or reports.

## Components and data flow

1. The API creates a durable order intent and obtains a confirmation token.
2. The workflow refreshes the durable execution-control row and validates
   emergency stop, session, daily loss, symbol, volume, magic, quote and
   protection levels.
3. The ZeroMQ gateway sends one versioned envelope to the EA and accepts only a
   correlated, schema-valid response.
4. The EA applies its own symbol, magic, spread, volume, circuit-breaker, and
   live-trading gates before calling `OrderSend`.
5. The workflow records accepted, rejected, or unknown outcome. Unknown remains
   unresolved.
6. Reconciliation reads MT5 order/deal history and resolves an unknown intent
   only when exactly one matching record exists.

## Verification

The implementation must add or extend tests for heartbeat, correlation,
duplicate, timeout, accepted, rejected, unknown, durable circuit-breaker
reservation, final confirmation, and unique reconciliation. A real terminal
readiness check may be run read-only. A real Demo order is a separate,
explicitly confirmed operational action and must not be inferred from test
success.
