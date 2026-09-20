# Execution Safety and API Authentication Validation

**Date:** 2026-09-18  
**Scope:** Tasks 1–5 and controlled PostgreSQL/TimescaleDB validation of
`docs/superpowers/plans/2026-09-16-execution-safety-auth.md`

## Implemented behavior covered

- Protected API routes use explicit role checks: `read_only` for OHLC,
  `order_review` for unknown-order review, `signal_execution` for live signal
  execution, and `emergency_stop` for reconciliation. Production credentials
  are fail-closed; anonymous access is available only through an explicit
  isolated test-factory option.
- `execution_control_state` durably stores the implemented account/symbol
  scope, emergency-stop state and reason, demo state, session expiry, demo
  trade count, daily-loss snapshot, optimistic version, actor, and UTC update
  time. Writes use explicit transactions and stale versions are rejected.
- `LiveOrderWorkflow` reloads durable state before live execution and persists
  safety transitions. Missing or unreadable state, database failure, an active
  stop, expiry, limits, or a remaining version conflict prevent broker
  interaction. Workflow recreation does not clear a stop or activate demo.
- The ZeroMQ gateway distinguishes timeout, transport, protocol, and unexpected
  failures. Only transport failures reset the socket. No classified failure
  retries an order; a timeout remains an unknown outcome for reconciliation.
- Production startup verifies the Alembic head and required execution-control
  schema. ORM `create_all` is retained only for explicit test/local setup.

## Validation evidence

The final local validation gate was recorded on 2026-09-16:

| Check | Command | Result |
|---|---|---|
| Python test suite | `py -m pytest -q --import-mode=importlib` | **375 passed, 1 skipped** |
| Compilation | `py -m compileall -q src tests` | Passed |
| Ruff formatting | `py -m ruff format --check src/python/api/auth.py src/python/api/app.py src/python/api/zmq_gateway.py src/python/execution/live_order_workflow.py src/python/data/models.py src/python/data/database.py` | Passed |
| Ruff lint | `py -m ruff check src/python/api/auth.py src/python/api/app.py src/python/api/zmq_gateway.py src/python/execution/live_order_workflow.py src/python/data/models.py src/python/data/database.py` | Passed |
| Alembic | `py -m alembic upgrade head` | Passed |
| Compose syntax | `docker compose --profile dashboard config --quiet` | Passed |

The focused task reports also record migration lifecycle coverage
(`upgrade head -> downgrade 0008 -> upgrade head`), execution-control schema
checks, role separation, workflow restart persistence, gateway classification,
and production startup tests. No secret, credential, real account, `order_send`,
EA, or MQL5 file was used or changed.

## Environment boundaries

The evidence above is local validation, including SQLite-backed tests and
non-destructive migration/schema checks. It does **not** certify:

- a real PostgreSQL 15 + TimescaleDB runtime, extension, or hypertable
  deployment;
- a real EA order path, broker response, or live ZeroMQ transport;
- MQL5 Strategy Tester execution; or
- a complete Demo E2E signal-to-broker flow.

The independent production deployment, real EA/broker order path, and Demo E2E
checks remain explicit P1 gates. Until those pass in a controlled environment,
this report must not be interpreted as production or live-trading approval.

## Read-only PostgreSQL/TimescaleDB probe

On 2026-09-16, the configured local TimescaleDB container was checked without
running migrations or changing data:

- PostgreSQL 15.5 responded successfully.
- The `timescaledb` extension was installed.
- The initial verifier correctly failed closed because the database revision was
  `0006` while the application expected `0009`.
- After explicit owner approval, `scripts/migrate_db.py` upgraded revisions
  `0006 -> 0007 -> 0008 -> 0009`.
- A second read-only verifier run returned `ok=True`, revision `0009`, and no
  issues.
- Both PostgreSQL concurrency integration tests passed with two independent
  connections: one idempotency owner and one execution-control winner.
- The full local regression suite passed with **419 passed, 2 skipped**; the two
  remaining skips are unrelated integrations without their required environment.
- Logging redaction tests passed for assignment-style secrets such as
  `MT5_PASSWORD=...`, exception text, structured JSON output, and legacy
  plain-text handlers. A local heuristic scan of non-secret source,
  documentation, and operational files was performed without printing matched
  values; findings were limited to expected code/tests/placeholders and require
  manual review. No external secret scan or credential rotation was performed.
- Three low-scope type-safety fixes in the risk manager, shadow ledger, and MT5
  execution connector passed focused mypy, Ruff, compile, and 65 related tests.
  The complete mypy run remains non-green because unrelated legacy modules still
  report additional errors.
- The complete `src/python/execution` scope is now green under mypy and Ruff.
  This batch also fixed a stale exception-variable path in the automated loop,
  rejected shadow execution without a ledger, handled nullable dashboard
  confidence safely, and blocked position creation when a filled order lacks a
  fill price.
- A read-only MT5 Demo login probe succeeded: initialization returned true,
  account information was present, terminal connectivity was true, and the
  returned login matched the configured login. No order API was called.
- A real local ZeroMQ REQ/REP transport test passed against a deterministic
  REP stub. It verified the Python envelope source/message type, correlation
  propagation, and a fail-closed `rejected/dry_run` response; it did not call
  MT5 or a broker order API.
- All expected hypertables (`ohlcv_data`, `market_ticks`, and
  `account_snapshots`) were present with one time dimension and a seven-day
  chunk interval.
- A transactional insert probe wrote one valid row to each hypertable and
  rolled the transaction back successfully; no validation rows were retained.
- A real local ZeroMQ `REQ/REP` exchange completed against a deterministic
  dry-run EA stub. The request envelope was received as `source=python`, and
  the response passed the gateway's `source=mt5`, `order_result`, correlation,
  and payload validation.
- Existing controlled Strategy Tester artifacts were independently inspected:
  the broker-symbol run used the fail-closed profile, completed with `Test
  passed`, and recorded `Total Trades=0` and `Total Deals=0`. This validates
  the no-trade baseline only; it does not validate broker execution.

This validates the configured development TimescaleDB instance and its basic
hypertable write lifecycle. It does not certify an independent production
deployment, retention/compression policies, an actual MQL5 EA/DLL endpoint, or
broker execution/reconciliation.
