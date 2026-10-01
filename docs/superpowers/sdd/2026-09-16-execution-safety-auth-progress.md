# SDD ledger — plan: docs/superpowers/plans/2026-09-16-execution-safety-auth.md

## Environment

- The checkout is not a Git repository (`NO_GIT`); native worktree and commit
  operations are unavailable.
- Changes are therefore made in the current workspace, with task-scoped
  implementer and reviewer agents and this ledger as the recovery record.
- No `src/mql5/` files may be modified.

## Pre-flight plan scan

| Scope | Shared files/interfaces | Finding | Ruling |
|---|---|---|---|
| Task 1 / Task 3 | `src/python/api/app.py`, authentication dependencies | Task 1 adds role-specific dependencies; Task 3 consumes them for live execution. | Compatible; Task 3 must preserve Task 1 route role boundaries. |
| Task 2 / Task 3 | execution-control repository and workflow injection | Task 2 produces the store; Task 3 consumes it. | Sequential dependency is intentional. |
| Task 4 / Task 3 | gateway failure behavior and unknown order handling | Task 4 changes error classification; Task 3 must preserve unknown outcome semantics. | Compatible; timeout must remain non-retryable and reconcilable. |
| Task 2 / Task 5 | model migration and schema startup | Task 2 adds migration; Task 5 verifies it and removes production `create_all`. | Compatible; migration must be complete before startup verification. |
| Task 5 / Task 6 | startup behavior and architecture documentation | Task 6 documents implemented startup behavior. | Task 6 waits for Task 5 validation. |
| Task 1 | auth files and tests | Protected route tests must match explicit production authentication while preserving isolated test injection. | Consistent with spec. |
| Task 2 | model, repository, migration, tests | The plan specifies typed state, transactions, version conflict, and migration coverage. | Consistent with spec. |
| Task 3 | workflow and app integration | The plan preserves existing gates and adds durable state refresh. | Consistent with spec. |
| Task 4 | gateway and tests | The plan distinguishes transport/protocol/unexpected failures and forbids retries. | Consistent with spec. |
| Task 5 | startup and migration tests | The plan explicitly separates test schema creation from production startup. | Consistent with spec. |
| Task 6 | documentation/report | The plan records evidence without claiming real MT5 execution. | Consistent with spec. |

## Rulings

- Ruling: proceed without Git worktree or commits — the checkout has no `.git`
  metadata, so creating a worktree or commit is impossible; the cost if wrong
  is reduced rollback/history support, mitigated by the ledger and task-scoped
  reviews.

## Task progress

- Task 1: complete (implementation, targeted tests, review, and scoped
  re-review passed; no Git commit available).

## Task 2 review remediation

- Initial Task 2 verdict: `NEEDS_CHANGES`; the original auto-provisioning getter,
  unhandled first-creation uniqueness race, existence-only migration guard, and
  missing scope/actor/version bounds were not accepted.
- Completed remediation: added read-only `load_execution_control` plus explicit
  `provision_execution_control` while retaining backward-compatible getter
  behavior; mapped concurrent expected-version-zero insert conflicts to
  `ConcurrencyConflict`; validated revision `0009` schema and provenance before
  downgrade; and enforced backend-neutral scope, actor, and version bounds.
- Added regression coverage for independent repository races, missing-table
  migration creation, mismatch rejection, pre-existing-table preservation,
  SQLite/PostgreSQL model compilation, empty/overlong inputs, and version checks.
- Validation: focused Task 2/migration tests `21 passed`; complete data suite
  `78 passed, 1 skipped`; ruff format/check passed; isolated migration
  upgrade/downgrade/re-upgrade passed.
- Remaining concerns are recorded in the Task 2 report, including production
  live-caller adoption and `daily_loss` precision policy. No `src/mql5/` or
  order-sending code was changed. No Git commit was created.
## Task 2 re-review follow-up (2026-09-16)

Four latest findings were closed without subagents, commits, or MT5 changes:

- `get_execution_control` is fail-closed by default; explicit provisioning is
  isolated to `provision_execution_control`.
- PostgreSQL DateTime reflection requires timezone-aware columns while SQLite
  reflection remains accepted; regression coverage is dialect-aware.
- Marker schema and provenance are validated. Pre-existing marker tables are
  never trusted for destructive cleanup, and unknown ownership fails closed.
- `0001` no longer creates all current metadata tables, so fresh migration
  ownership is correct through `head -> 0008` downgrade.

Follow-up verification: focused execution/migration tests `26 passed`; complete
`tests/python/data` suite `83 passed, 1 skipped`; ruff format/check passed; and
isolated migration lifecycle `upgrade head -> downgrade 0008 -> upgrade head`
passed. No `src/mql5/` file was modified and no commit was created.

- Task 2: complete after independent re-review (`PASS`). Full regression after
  the schema-version assertion update: `362 passed, 1 skipped`.

## Task 3 completion (2026-09-16)

- Task 3 is complete after implementation and independent read-only review
  (`PASS`).
- `LiveOrderWorkflow` now consumes an injected execution-control repository,
  reloads state before each live-order path, rejects unavailable or missing
  durable state, and persists state transitions with optimistic version checks.
- Emergency-stop, demo activation, session expiry, trade count, and daily-loss
  state survive workflow recreation; explicit `control_store=None` remains
  fail-closed.
- Production API workflow construction passes the database repository into the
  workflow. Existing risk, confirmation, idempotency, and unknown-outcome
  protections remain in place.
- Focused execution/API safety tests: `55 passed`.
- Full regression suite after Task 3: `368 passed, 1 skipped`.
- `compileall`, Ruff check, and Ruff format checks passed for changed files.
- Independent review found no high-confidence issues. No `src/mql5/` file was
  changed, no real MT5 order was sent, and no commit was created.

## Task 4 completion (2026-09-16)

- Task 4 is complete after implementation, independent review, and
  remediation.
- ZeroMQ timeout, transport, protocol, malformed JSON, and unexpected
  internal failures now have distinct fail-closed behavior and stable error
  codes. Timeout and protocol failures do not reset the socket; transport
  failures reset it; no classified failure retries an order.
- Response envelopes are validated for schema, source, message type, payload,
  and correlation ID before being returned.
- Focused gateway/demo tests: `17 passed`; Ruff passed.

## Task 5 completion (2026-09-16)

- Task 5 is complete after implementation, independent review, and
  remediation.
- Production startup uses read-only migration/schema verification and rejects
  missing `DATABASE_URL`, SQLite production configuration, multiple migration
  heads, and malformed execution-control schema.
- Local/test schema creation remains explicit through `initialize_for_tests()`.
- Focused runtime/API/schema tests: `29 passed`; Alembic upgrade and Docker
  Compose config validation passed.

## Controlled TimescaleDB validation (2026-09-16)

- The configured PostgreSQL 15.5/TimescaleDB instance is at Alembic revision
  `0009` and passes the read-only schema verifier.
- `ohlcv_data`, `market_ticks`, and `account_snapshots` are registered as
  hypertables with one `timestamp` dimension and a seven-day chunk interval.
- A transaction inserted valid probe rows into all three hypertables and then
  rolled back successfully; no probe data was retained.
- A local real ZeroMQ `REQ/REP` dry-run exchange passed the gateway envelope,
  source, message type, correlation, and payload checks against a deterministic
  stub; no broker or MT5 endpoint was involved.
- This closes the hypertable lifecycle gate for the configured development
  database only. Independent production deployment/recovery and real MT5/EA
  transport remain open.

## Strategy Tester baseline assessment (2026-09-16)

- The repository contains controlled Strategy Tester artifacts from
  `2026-09-13` for the broker symbol `XAUUSD_l`.
- The fail-closed profile kept `InpAllowLiveTrading=false` and
  `InpEnableZmq=false`; the recorded run ended with `Test passed`,
  `Total Trades=0`, and `Total Deals=0`.
- This closes the no-trade Strategy Tester baseline gate, not the real
  EA-to-broker order path. Demo E2E, broker response, reconciliation, and
  independent production deployment remain open.

## Task 6 completion (2026-09-16)

- API, data, architecture, and Persian project-assessment documents were
  updated, and the final execution-safety validation report was added under
  `reports/security/`.
- Documentation distinguishes implemented local validation from pending
  PostgreSQL/TimescaleDB, MT5/EA, Strategy Tester, and controlled Demo E2E
  gates.
- Final complete regression after Tasks 3–5: `375 passed, 1 skipped`.

## P1 PostgreSQL validation update (2026-09-16)

- The configured TimescaleDB container was verified read-only: PostgreSQL
  15.5 and the `timescaledb` extension were healthy.
- The first verifier run found the database at revision `0006`; production
  startup correctly failed closed because head `0009` and
  `execution_control_state` were missing.
- After explicit owner approval, `scripts/migrate_db.py` applied `0007`,
  `0008`, and `0009`, then verified the schema and recorded versions.
- The post-migration verifier returned `ok=True`, revision `0009`, and no
  issues.
- PostgreSQL idempotency concurrency coverage passed with two independent
  connections (`1 passed`).
- A fixed test key was replaced with a per-run UUID to prevent stale data from
  producing a false failure.
- Full regression remains `375 passed, 1 skipped`. All nine changed source/test
  files pass Ruff and compile validation. A repository-wide Ruff run still
  reports unrelated pre-existing files outside this task's scope.
