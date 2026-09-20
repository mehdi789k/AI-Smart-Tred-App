# P0.1 Demo Readiness Evidence

Date: 2026-09-18

## Safety outcome

No order was sent. The checks in this report are read-only or metadata-only.

## Evidence

- `scripts/verify_mt5_demo.py`: passed; MT5 connected to a Demo server and reported zero orders sent.
- Broker symbol discovery found `XAUUSD_l`; the requested base symbol `XAUUSD` is not available.
- `XAUUSD_l` metadata was read successfully:
  - digits: 2
  - point: 0.01
  - volume minimum/step: 0.01 / 0.01
  - stop level: 0 points
  - visible: true
- At the read-only quote observed during the check, a bounded BUY scenario would calculate SL one dollar below ask and TP one and a half dollars above ask. These values were not submitted.

## Preflight result after safe configuration update

The non-secret Demo gates were corrected after explicit confirmation:

- broker symbol: `XAUUSD_l`
- maximum position volume: `0.02`
- maximum daily loss: `10 USD`
- automatic trading: disabled

The API lifecycle now connects the MT5 runtime only when `MT5_ENABLED` is
explicitly enabled, and disconnects it during shutdown. With the reviewed
environment loaded:

- `/ready`: `mt5=ready`, `trading.allowed=true`
- `scripts/verify_demo_readiness.py`: passed
- bounded API dry-run for BUY `0.02` with the observed SL/TP: passed
- no order endpoint was called with `dry_run=false`

## Remaining blocking conditions

1. The EA/ZeroMQ path has not been connected and exercised against the broker.
2. Broker acceptance and post-order reconciliation have not been evidenced.
3. The circuit breaker is not backed by a configured runtime control store in this local API process; readiness therefore is not a substitute for final order approval.

P0.1 remains blocked for real execution. Only after EA/ZeroMQ connectivity,
broker acceptance, reconciliation, and final operator confirmation may a single
Demo order be considered.
