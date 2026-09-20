# Local Demo Staging Design

## Scope

This design prepares the Smart MT5 Trading System on a Windows workstation for
controlled testing with a LiteFinance Demo account. It does not enable Live
Trading, accept Live credentials, or authorize unattended order submission.

## Safety boundary

- `InpAllowLiveTrading=false` remains mandatory.
- `MT5_AUTO_TRADING_ENABLED=false` remains the default until an explicit,
  operator-watched Demo canary is approved.
- Credentials are entered by the operator in MT5 or local secret injection.
  They must not be sent in chat, committed, logged, or included in evidence.
- The dashboard is bound to localhost and is an observation/control surface;
  backend risk controls remain authoritative.
- Unknown or timed-out broker outcomes remain unresolved until reconciliation.
- Any symbol mismatch, unexpected order, duplicate submission, missing
  protective level, failed health check, or protocol error stops the run.

## Components

1. TimescaleDB 2.13.1 / PostgreSQL 15 for staging persistence.
2. API service with migrations and execution safety controls.
3. Prometheus, Alertmanager, and Grafana for local observability.
4. Streamlit dashboard on `localhost:8501`.
5. MetaTrader 5 Demo terminal and EA, connected only after local gates pass.

## Execution sequence

1. Validate Docker Desktop, MT5, repository configuration, and local ports.
2. Validate Compose configuration without exposing secrets.
3. Start database, API, and observability services.
4. Run migrations and health/readiness checks.
5. Run offline contract and safety tests.
6. Start the dashboard and verify it reads the API.
7. Operator logs into the LiteFinance Demo account and records exact Market
   Watch symbols without sharing account credentials.
8. Compile the EA in strict mode and verify safe inputs.
9. Run heartbeat and read-only checks.
10. Only after an explicit operator canary approval, run one minimal Demo order.
11. Reconcile, verify audit continuity, and stop/rollback on any discrepancy.

## Verification and stop conditions

The local staging gate is passed only when Compose validation, migration,
health/readiness, offline contract tests, and dashboard health all succeed.
The Demo gate additionally requires owner and second-approver authorization,
exact symbol scope, expiry, limits, and evidence with credentials redacted.

This design intentionally defers Live Trading, unattended execution, broker
credential automation, public dashboard exposure, and production deployment.
