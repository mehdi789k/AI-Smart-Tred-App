# Task 2 report: durable execution-control persistence

Date: 2026-09-16
Plan: `docs/superpowers/plans/2026-09-16-execution-safety-auth.md`
Spec: `docs/superpowers/specs/2026-09-16-execution-safety-auth-design.md`
Status: Remediated after review (`NEEDS_CHANGES` findings addressed)

## Scope delivered

- Kept the typed `ExecutionControlState` model and added backend-neutral
  validation for non-empty `scope` (maximum 255 characters) and `actor`
  (maximum 128 characters). The model now also checks the same bounds and
  requires `version >= 1`, in addition to the existing non-negative counters.
- Added the read-only `load_execution_control(scope)` loader, returning
  `ExecutionControlState | None` without provisioning a missing scope.
  Added explicit `provision_execution_control(scope)` and retained
  `get_execution_control(scope, provision=True)` for existing callers; live
  callers can use either `load_execution_control` or `provision=False` to
  distinguish unavailable durable state from fail-closed defaults.
- Wrapped first-creation writes in an explicit transaction boundary. A unique
  insert race after `expected_version=0` now rolls back and raises
  `ConcurrencyConflict` instead of leaking raw `IntegrityError`.
- Hardened Alembic revision `0009`: it verifies exact columns, reflected types,
  nullability, defaults, primary key, and named check constraints rather than
  trusting table existence. A mismatch raises an explicit schema error. A
  private provenance marker records whether `0009` created the table, so
  downgrade drops only migration-owned tables and preserves valid pre-existing
  tables, including tables created earlier by `0001` metadata setup.
- Preserved the current `daily_loss` representation (`Numeric(20, 8)`). Its
  precision/rounding policy remains a follow-up concern for a future API/data
  contract change; this remediation does not broaden that API change.
- No file under `src/mql5/` was modified. No MT5 connection or `order_send` call
  was executed.

## Files changed for this remediation

- `src/python/data/models.py`
- `src/python/data/database.py`
- `migrations/versions/0009_execution_control_state.py`
- `tests/python/data/test_execution_control_state.py`
- `tests/python/data/test_migrations.py`
- `docs/superpowers/sdd/2026-09-16-execution-safety-auth-task2-report.md`
- `docs/superpowers/sdd/2026-09-16-execution-safety-auth-progress.md`

## Test evidence

RED phase for the review findings was observed before implementation: the new
loader, concurrency, validation, version-check, migration-schema, and
pre-existing-table tests failed against the original implementation (including
raw race behavior and existence-only migration handling).

Focused regression tests:

```text
py -m pytest -q tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py --import-mode=importlib
21 passed in 11.31s
```

Complete requested data suite:

```text
py -m pytest -q tests/python/data --import-mode=importlib
78 passed, 1 skipped in 20.98s
```

Formatting and linting:

```text
py -m ruff format --check src/python/data/models.py src/python/data/database.py migrations/versions/0009_execution_control_state.py tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py
5 files already formatted

py -m ruff check src/python/data/models.py src/python/data/database.py migrations/versions/0009_execution_control_state.py tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py
All checks passed!
```

Migration lifecycle check on an isolated temporary SQLite database:

```text
py -m alembic upgrade head
py -m alembic downgrade 0008
py -m alembic upgrade head
migration upgrade head -> downgrade 0008 -> upgrade head passed
```

Migration tests additionally cover upgrading from an actual `0008` database
with the table removed, mismatch rejection, and downgrade preservation of a
valid pre-existing table. The model bound/check expressions are compiled for
both SQLite and PostgreSQL dialects, while repository validation occurs before
backend-specific SQL.

## Status / remaining concerns

- Status: Task 2 review remediation is implemented and validated; no commit was
  created.
- The existing local `AsyncDatabase.initialize()` path still uses metadata-based
  schema creation for test/local initialization. Production startup migration
  ownership and live workflow consumption of the read-only loader remain in
  Tasks 3 and 5.
- Task 3 must use `load_execution_control` (or `get_execution_control(...,
  provision=False)`) before any live execution decision and fail closed when it
  returns `None`; this repository change does not silently make missing state
  executable.
- `daily_loss` remains `Numeric(20, 8)` and its domain precision/rounding
  policy is intentionally recorded as a remaining data-contract concern.
- The workspace is not a Git repository; no Git commit was attempted.
## Task 2 re-review follow-up (2026-09-16)

The latest four re-review findings were fixed surgically:

1. `get_execution_control` now defaults to read-only (`provision=False`). All
   provisioning tests use the explicit `provision_execution_control` method;
   missing state is never implicitly created by the getter.
2. Revision `0009` passes the reflected dialect into type validation and
   requires `timezone=True` for PostgreSQL DateTime columns. SQLite DateTime
   reflection remains portable. A dialect-aware regression test covers both
   behaviors.
3. The provenance marker schema is validated before use. A pre-existing marker
   is recorded as not migration-owned; an existing current-revision row or
   malformed marker fails closed. Downgrade drops the marker only when the
   current migration created it, and drops execution state only when recorded
   as owned by revision `0009`.
4. Revision `0001` now creates only its historical initial table set instead of
   dynamically creating every table in current metadata. Fresh `head` ->
   `0008` downgrade therefore removes the `0009` table and owned marker while
   preserving the `0008` lifecycle tables.

Additional regression coverage now includes fresh-chain ownership, pre-existing
and malformed markers, marker ownership ambiguity, and dialect-aware DateTime
validation. No `src/mql5/` file was changed.

Validation evidence for this follow-up:

```text
py -m pytest -q tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py --import-mode=importlib
26 passed in 22.17s

py -m pytest -q tests/python/data --import-mode=importlib
83 passed, 1 skipped in 28.09s

py -m ruff format --check src/python/data/database.py migrations/versions/0001_initial_schema.py migrations/versions/0009_execution_control_state.py tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py
5 files already formatted

py -m ruff check src/python/data/database.py migrations/versions/0001_initial_schema.py migrations/versions/0009_execution_control_state.py tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py
All checks passed!

Isolated SQLite lifecycle: upgrade head -> downgrade 0008 -> upgrade head passed,
with execution_control_state and its migration-owned marker present after the
final upgrade.
```

No commit was created.
