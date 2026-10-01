# Trading Safety and Delivery Roadmap Design

## Goal

Prepare the Smart MT5 Trading System for controlled LiteFinance Demo
validation and, only after explicit safety gates pass, future Live Trading.

## Owner decisions

- Official Demo broker: LiteFinance.
- Symbols: symbols selected by the user in the MetaTrader 5 Market Watch.
- Demo activation approval: project owner plus a second approver.
- Official Git repository: not connected yet.
- Trunk assumption: `main`.
- Canonical runtime: Python 3.12.
- Recovery objective: RPO <= 15 minutes and RTO <= 1 hour.
- Retention: 90 days for order audit, broker responses, and operational logs.
- Required API scope: health/readiness, execution, and reconciliation.
- Product direction: Live Trading is a future goal, but no Live execution is
  allowed before the Demo, reconciliation, recovery, and activation gates pass.
- ML success: a combined risk-adjusted return, drawdown, Sharpe, and stability
  score rather than raw profit alone.

## Safety boundary

All validation starts in Shadow or Demo mode. No code path may enable Live
Trading by default. Timeout and unknown broker outcomes remain unresolved until
reconciliation; they must never be converted into an automatic retry or an
implicit accepted/rejected result.

## Delivery approach

1. Make local quality gates green.
2. Prove the external MT5/EA/ZeroMQ/Broker Demo seam.
3. Add two-person activation governance and recovery evidence.
4. Establish staging, CI enforcement, retention, and operational alerting.
5. Apply the ML evaluation policy to future model promotion.

Each phase is independently reversible. The project remains a modular
monolith with an explicit MT5/EA boundary; no microservice split is introduced
until operational evidence demonstrates a need.

## Verification

The canonical validation commands are:

```powershell
py -3 -m pytest -q --import-mode=importlib
py -3 -m ruff check src tests
py -3 -m ruff format --check src tests
py -3 -m mypy --follow-imports=skip src/python
py -3 -m alembic upgrade head
docker compose --profile dashboard config --quiet
```

The external Demo phase additionally requires a reproducible report covering
heartbeat, accepted, rejected, timeout, duplicate, unknown outcome,
reconciliation, restart, and circuit-breaker recovery scenarios.
