# Execution Safety and API Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require authenticated access to sensitive API data, persist execution safety state, and preserve fail-closed behavior across restarts and ZeroMQ failures.

**Architecture:** Keep `AuthenticationService`, `LiveOrderWorkflow`, `AsyncDatabase`, and `MQL5ExecutionGateway` as the existing boundaries. Add a durable execution-control repository backed by the current SQLAlchemy database, inject it into the workflow, and make API route dependencies role-specific. Production startup uses migrations plus verification; schema creation remains test/local-only.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, Alembic, PostgreSQL 15/TimescaleDB, SQLite for unit tests, pytest, Ruff, Mypy, ZeroMQ/pyzmq.

**Spec:** `docs/superpowers/specs/2026-09-16-execution-safety-auth-design.md`

## Global Constraints

- Do not modify `src/mql5/` in this phase.
- Do not call a real MT5 account or `order_send` in tests.
- Live execution must remain fail-closed when authentication, database state, or gateway state is unavailable.
- Every database write must use an explicit transaction.
- Confirmation tokens, API tokens, and credentials must never be persisted or logged in plaintext.
- Preserve existing order idempotency and unknown-order reconciliation behavior.
- Use UTC-aware timestamps for all persisted safety state.
- Production startup must not use `Base.metadata.create_all`.

---

### Task 1: Lock down API authentication roles

**Files:**
- Modify: `src/python/api/auth.py`
- Modify: `src/python/api/app.py`
- Modify: `.env.example`
- Test: `tests/api/test_app.py`
- Test: `tests/python/api/test_auth.py` (create if absent)

**Interfaces:**
- Consumes: existing `AuthenticationService`, `Principal`, and `require_role`.
- Produces: `ORDER_REVIEW = "order_review"` and route dependencies that require explicit credentials for protected API reads.

- [ ] **Step 1: Inspect existing auth and route tests**

Run:

```powershell
py -m pytest -q tests/api/test_app.py tests/python/api/test_execution_safety.py --import-mode=importlib
```

Expected: existing tests pass; note any tests that intentionally rely on anonymous reads so they can inject explicit test configuration rather than weakening production behavior.

- [ ] **Step 2: Write failing authentication tests**

Add tests that assert:

```python
def test_ohlc_requires_credentials(client):
    response = client.get("/api/v1/data/ohlc?symbol=XAUUSD&timeframe=M5")
    assert response.status_code == 401


def test_unknown_orders_requires_order_review_role(client):
    response = client.get(
        "/api/v1/orders/unknown",
        headers={"X-API-Key": "read-only-key"},
    )
    assert response.status_code == 403
```

Use the existing app fixture and injected environment/configuration patterns; do not add real secrets.

- [ ] **Step 3: Add the role and explicit route dependency**

Define `ORDER_REVIEW` in `auth.py`. In `app.py`, create separate dependencies for
`read_only`, `order_review`, and live execution. Protected API routes must call
`require_role(..., require_configured=True)` rather than accepting the anonymous
fallback.

- [ ] **Step 4: Add safe environment configuration**

Document the role-specific static keys and the production requirement in
`.env.example`. Do not add credential values.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
py -m pytest -q tests/api/test_app.py tests/python/api/test_auth.py --import-mode=importlib
```

Expected: PASS, including the new `401` and `403` assertions.

- [ ] **Step 6: Run formatting and lint checks**

Run:

```powershell
py -m ruff format --check src/python/api/auth.py src/python/api/app.py tests/api/test_app.py tests/python/api/test_auth.py
py -m ruff check src/python/api/auth.py src/python/api/app.py tests/api/test_app.py tests/python/api/test_auth.py
```

Expected: PASS.

### Task 2: Add durable execution-control persistence

**Files:**
- Modify: `src/python/data/models.py`
- Modify: `src/python/data/database.py`
- Create: `migrations/versions/<new_revision>_add_execution_control_state.py`
- Test: `tests/python/data/test_execution_control_state.py`

**Interfaces:**
- Consumes: existing `UTCDateTime`, SQLAlchemy `Base`, `AsyncDatabase`, and repository transaction conventions.
- Produces: `ExecutionControlState` model and repository methods:
  `get_execution_control(scope: str) -> ...`,
  `save_execution_control(...) -> ...`,
  and atomic version-checked updates.

- [ ] **Step 1: Write failing model and repository tests**

Cover:

```python
async def test_execution_control_round_trips_utc_and_state(database):
    state = await database.repository.get_execution_control("demo:XAUUSD")
    assert state is not None
    assert state.emergency_stop is False
    assert state.version == 1


async def test_stale_execution_control_version_is_rejected(database):
    with pytest.raises(ConcurrencyConflict):
        await database.repository.save_execution_control(
            "demo:XAUUSD", expected_version=0, emergency_stop=True
        )
```

Use the existing SQLite async test fixture for unit coverage and keep the
repository API backend-neutral.

- [ ] **Step 2: Add the typed SQLAlchemy model**

Store scope, emergency-stop fields, demo activation fields, session expiry,
trade count, daily loss, version, actor, and UTC update timestamps. Add a
unique primary key on scope and a check preventing negative trade count or
daily loss.

- [ ] **Step 3: Add transactional repository methods**

Implement creation with safe defaults, read, and version-checked update inside
`session.begin()`. Raise a specific `ConcurrencyConflict` instead of silently
overwriting a newer state. Never persist confirmation token plaintext.

- [ ] **Step 4: Add the Alembic migration**

Create the revision using the repository's existing migration format. The
migration must be reversible and must not call ORM `create_all`.

- [ ] **Step 5: Run focused data tests and migration tests**

Run:

```powershell
py -m pytest -q tests/python/data/test_execution_control_state.py tests/python/data/test_migrations.py --import-mode=importlib
py -m alembic upgrade head
```

Expected: PASS with the new table present and migration history valid.

### Task 3: Integrate durable state into live execution

**Files:**
- Modify: `src/python/execution/live_order_workflow.py`
- Modify: `src/python/api/app.py`
- Test: `tests/python/execution/test_demo_operations.py`
- Test: `tests/python/execution/test_live_order_workflow.py`
- Test: `tests/python/api/test_execution_safety.py`

**Interfaces:**
- Consumes: execution-control repository from Task 2 and `ORDER_REVIEW`/auth boundaries from Task 1.
- Produces: workflow construction with injected control store; live order checks that reject unavailable or unsafe durable state.

- [ ] **Step 1: Write failing restart and fail-closed tests**

Add tests that:

```python
def test_recreated_workflow_preserves_emergency_stop(control_store, connector):
    first = build_workflow(control_store, connector)
    first.trip("operator stop")
    second = build_workflow(control_store, connector)
    assert second.emergency_stop_active is True


def test_missing_control_state_rejects_live_order(connector):
    workflow = build_workflow(control_store=None, connector=connector)
    with pytest.raises(LiveOrderRejected, match="durable"):
        workflow.execute_market_order(...)
```

Use existing fake connectors and confirmation helpers; never call MT5.

- [ ] **Step 2: Inject the control store**

Add an optional typed control-store dependency to `LiveOrderWorkflow`. On
construction/startup, load the scoped state. If a live order is requested and
the store is missing or unavailable, raise a stable fail-closed rejection.

- [ ] **Step 3: Persist state transitions**

Persist emergency stop, demo activation/deactivation, authorization expiry,
trade counter, and daily-loss updates using the repository's versioned update.
Refresh durable state before each live order. A restart must not activate Demo
mode or clear emergency stop.

- [ ] **Step 4: Preserve existing safety gates**

Keep symbol, magic, volume, spread, confirmation, daily-loss, SL/TP, and
idempotency checks unchanged in meaning. Do not add automatic retries.

- [ ] **Step 5: Update app dependency construction**

Pass the database repository's execution-control store into the production
workflow. Test app factories must inject a deterministic fake store where
needed.

- [ ] **Step 6: Run focused execution tests**

Run:

```powershell
py -m pytest -q tests/python/execution/test_demo_operations.py tests/python/execution/test_live_order_workflow.py tests/python/api/test_execution_safety.py --import-mode=importlib
```

Expected: PASS, including restart persistence and missing-state rejection.

### Task 4: Classify ZeroMQ failures without retrying orders

**Files:**
- Modify: `src/python/api/zmq_gateway.py`
- Test: `tests/api/test_zmq_gateway.py`

**Interfaces:**
- Consumes: existing `MQL5ExecutionGateway`, metrics registry, and logging helpers.
- Produces: stable gateway error subclasses or error codes for timeout, transport failure, protocol failure, and unexpected internal errors.

- [ ] **Step 1: Write failing gateway classification tests**

Cover timeout, `zmq.error.ZMQError`, malformed response, and unexpected
exception. Assert the socket is reset only for transport failures and that no
retry call is made.

- [ ] **Step 2: Narrow exception handling**

Handle `zmq.error.Again` and `TimeoutError` as timeout; handle
`zmq.error.ZMQError` as transport failure; validate response schema outside the
transport block; re-raise unexpected exceptions after logging.

- [ ] **Step 3: Preserve fail-closed behavior**

All classified gateway failures must prevent an accepted result. Timeout must
remain distinguishable to the caller so the order can enter `unknown` and be
reconciled rather than retried.

- [ ] **Step 4: Run focused gateway tests**

Run:

```powershell
py -m pytest -q tests/api/test_zmq_gateway.py --import-mode=importlib
py -m ruff format --check src/python/api/zmq_gateway.py tests/api/test_zmq_gateway.py
py -m ruff check src/python/api/zmq_gateway.py tests/api/test_zmq_gateway.py
```

Expected: PASS.

### Task 5: Remove production `create_all` from startup

**Files:**
- Modify: `src/python/api/app.py`
- Modify: `src/python/data/database.py`
- Modify: `scripts/migrate_db.py`
- Test: `tests/python/data/test_runtime.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: Alembic migration command and existing schema verifier.
- Produces: production lifespan that verifies migrated schema without calling
  `Base.metadata.create_all`.

- [ ] **Step 1: Write a failing startup test**

Inject a database double whose `create_schema` raises if called. Start the API
lifespan and assert production mode uses migration verification instead.

- [ ] **Step 2: Separate explicit test initialization from production startup**

Keep `AsyncDatabase.initialize_for_tests()` or the existing test-only schema
helper for SQLite. Production app startup must call the migration/verification
path and never invoke `create_all`.

- [ ] **Step 3: Add schema verification failure behavior**

If migration head or required execution-control columns are missing, fail
startup with a clear error and do not expose live execution.

- [ ] **Step 4: Run migration and app tests**

Run:

```powershell
py -m pytest -q tests/python/data/test_runtime.py tests/python/data/test_migrations.py tests/api/test_app.py --import-mode=importlib
py -m alembic upgrade head
docker compose --profile dashboard config --quiet
```

Expected: PASS.

### Task 6: Update contracts, priority assessment, and validation report

**Files:**
- Modify: `docs/API_CONTRACT.md`
- Modify: `docs/DATA_CONTRACT.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/PROJECT_ASSESSMENT_FA.md`
- Create: `reports/security/execution-safety-auth-validation.md`

**Interfaces:**
- Consumes: implemented behavior and test results from Tasks 1–5.
- Produces: documentation that distinguishes implemented, tested, and planned
  behavior and records the updated priority order.

- [ ] **Step 1: Document authentication and role changes**

Update API contract route permissions, error responses, production
configuration requirements, and the new `order_review` role.

- [ ] **Step 2: Document execution-control state**

Update the data contract with scope, versioning, UTC rules, audit fields, and
fail-closed semantics. Do not document fields that are not implemented.

- [ ] **Step 3: Update architecture and priority order**

Mark completed P0 items, retain unimplemented PostgreSQL/MT5 integration work
as P1, and state that real Demo E2E remains blocked until those gates pass.

- [ ] **Step 4: Record validation evidence**

Record exact commands, dates, pass/fail counts, skipped tests, and any
environment limitations in the report. Do not claim a real MT5 test occurred.

- [ ] **Step 5: Run the complete validation gate**

Run:

```powershell
py -m compileall -q src tests
py -m pytest -q --import-mode=importlib
py -m ruff format --check src/python/api/auth.py src/python/api/app.py src/python/api/zmq_gateway.py src/python/execution/live_order_workflow.py src/python/data/models.py src/python/data/database.py
py -m ruff check src/python/api/auth.py src/python/api/app.py src/python/api/zmq_gateway.py src/python/execution/live_order_workflow.py src/python/data/models.py src/python/data/database.py
py -m mypy --follow-imports=skip src/python/api/auth.py src/python/api/app.py src/python/api/zmq_gateway.py src/python/execution/live_order_workflow.py src/python/data/models.py src/python/data/database.py
py -m alembic upgrade head
docker compose --profile dashboard config --quiet
```

Expected: all commands exit with code `0`; document any environment-only
limitation rather than hiding it.

