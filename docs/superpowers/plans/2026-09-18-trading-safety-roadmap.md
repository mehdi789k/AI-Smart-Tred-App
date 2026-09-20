# Trading Safety and Delivery Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Smart MT5 Trading System pass local quality gates, establish
controlled LiteFinance Demo validation, and add operational safeguards before
any future Live Trading.

**Architecture:** Keep the existing modular Python system and explicit MT5/EA
ZeroMQ boundary. Strengthen seam contracts, activation governance, recovery,
retention, and observability without introducing a premature microservice split.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL 15 +
TimescaleDB, ZeroMQ, MQL5, pytest, Ruff, Mypy, Docker Compose, Prometheus,
Grafana, and Alertmanager.

**Spec:** `docs/superpowers/specs/2026-09-18-trading-safety-roadmap-design.md`

## Global Constraints

- LiteFinance is the official Demo broker.
- Demo symbols are selected from the user's MT5 Market Watch.
- Demo activation requires the project owner and a second approver.
- Python 3.12 is canonical across locks, CI, and runtime images.
- Recovery target is RPO <= 15 minutes and RTO <= 1 hour.
- Order audit, broker responses, and operational logs retain for 90 days.
- Required API scope is health/readiness, execution, and reconciliation.
- Live Trading remains disabled until Demo, reconciliation, recovery, and activation gates pass.
- Unknown broker outcomes must remain unresolved until reconciliation.
- No credentials may be committed or emitted in logs/artifacts.
- Every phase must be independently reversible and validated before the next phase.

---

### Task 1: Restore local quality gates

**Files:**
- Modify: `src/python/api/app.py` (Ruff formatting only)
- Modify: `src/python/api/zmq_gateway.py` (Ruff formatting only)
- Modify: `tests/python/api/test_execution_safety.py` (Ruff formatting only)
- Modify: `src/python/execution/live_order_workflow.py:1654` (nullable integer guard)
- Test: existing project test and quality commands

**Interfaces:**
- Consumes: existing execution and broker payload types.
- Produces: green pytest, Ruff format, Ruff lint, and Mypy gates.

- [ ] **Step 1: Add a regression test for a nullable broker/status value**

  Extend the nearest existing execution-safety test module with a case that
  passes a missing or non-numeric value to the code path around line 1654 and
  asserts that it is rejected explicitly rather than passed to `int()`.

- [ ] **Step 2: Run the focused regression test and verify it fails**

  Run:

  ```powershell
  py -3 -m pytest tests/python/execution -q --import-mode=importlib
  ```

  Expected: the new case fails with the current Mypy/runtime handling.

- [ ] **Step 3: Implement the smallest typed guard**

  Normalize the optional value with an explicit `None`/numeric guard and
  preserve the existing fail-closed rejection behavior. Do not use `Any`
  casts to silence Mypy.

- [ ] **Step 4: Apply formatting without changing behavior**

  Run:

  ```powershell
  py -3 -m ruff format src/python/api/app.py src/python/api/zmq_gateway.py tests/python/api/test_execution_safety.py
  ```

- [ ] **Step 5: Run all local gates**

  Run:

  ```powershell
  py -3 -m pytest -q --import-mode=importlib
  py -3 -m ruff check src tests
  py -3 -m ruff format --check src tests
  py -3 -m mypy --follow-imports=skip src/python
  ```

  Expected: all commands exit successfully; existing integration skips may
  remain when `TEST_POSTGRES_URL` is absent.

### Task 2: Make activation governance explicit

**Files:**
- Modify: `src/python/execution/live_order_workflow.py`
- Modify: `src/python/api/app.py`
- Modify: `src/python/data/models.py` or the existing execution-control model file
- Test: `tests/python/execution/` and `tests/python/api/`
- Modify: `docs/API_CONTRACT.md`
- Modify: `docs/DATA_CONTRACT.md`

**Interfaces:**
- Consumes: existing execution-control persistence and role-based auth.
- Produces: a durable two-person Demo activation record with expiry, scope,
  limits, approvers, and configuration hash.

- [ ] **Step 1: Write failing tests for incomplete activation approval**

  Add tests proving that activation is rejected when the owner approval,
  second approval, expiry, symbol scope, or configuration hash is missing.

- [ ] **Step 2: Write a passing-path test**

  Add a test that creates an activation with owner and second approver,
  selected Market Watch symbols, limits, expiry, and a config hash, then
  verifies that the durable state can be loaded after a fresh workflow object.

- [ ] **Step 3: Implement the activation record and transition guards**

  Reuse the existing execution-control repository and optimistic versioning.
  Add explicit validation and audit events. Keep defaults deny-by-default and
  preserve the existing emergency-stop behavior.

- [ ] **Step 4: Expose only the required operational API surface**

  Keep health/readiness, execution, and reconciliation behavior aligned with
  the existing auth roles. Mark unsupported planned endpoints as planned in
  the contract rather than exposing partial routes.

- [ ] **Step 5: Run focused tests and contract validation**

  ```powershell
  py -3 -m pytest tests/python/execution tests/python/api -q --import-mode=importlib
  py -3 -m ruff check src tests
  py -3 -m mypy --follow-imports=skip src/python
  ```

### Task 3: Build the LiteFinance Demo E2E harness

**Files:**
- Modify: `tests/fixtures/zmq_contract_v1.json`
- Modify: `tests/fixtures/mt5_replay_guardrails_v1.json`
- Create: `tests/e2e/test_litefinance_demo_contract.py`
- Create: `ops/mt5/litefinance-demo-runbook.md`
- Modify: `docs/RUNBOOK_MT5_CIRCUIT_BREAKER_FA.md`

**Interfaces:**
- Consumes: existing EA inputs, ZMQ contract fixtures, replay guardrails, and
  correlation/idempotency fields.
- Produces: reproducible Demo-only validation evidence without Live credentials.

- [ ] **Step 1: Define the scenario matrix**

  Encode scenarios for heartbeat, accepted, rejected, timeout, duplicate,
  unknown outcome, reconciliation, restart, and circuit-breaker activation.

- [ ] **Step 2: Add failing contract assertions**

  Assert that accepted results contain broker identifiers, timeout results are
  unresolved, duplicate keys do not create a second order, and reconciliation
  requires operator evidence.

- [ ] **Step 3: Implement the harness using the existing replay boundary**

  Use the existing gateway and fixture validators. Do not connect tests to a
  production account and do not add an automatic retry path.

- [ ] **Step 4: Write the operator runbook**

  Document LiteFinance Demo setup, Market Watch symbol selection, EA compile,
  `InpAllowLiveTrading=false`, `InpEnableZmq` requirements, evidence capture,
  cleanup, and rollback.

- [ ] **Step 5: Execute the non-secret portion locally**

  ```powershell
  py -3 -m pytest tests/e2e/test_litefinance_demo_contract.py -q
  ```

  The real terminal/broker portion is executed manually in the Demo
  environment and attached to the runbook; it is not simulated as passing.

### Task 4: Establish backup, retention, and recovery evidence

**Files:**
- Modify: `scripts/backup_db.ps1`
- Modify: `scripts/restore_db.ps1`
- Create: `scripts/validate_recovery.ps1`
- Modify: `docker-compose.yml`
- Modify: `docs/deployment/DOCKER_GUIDE.md`
- Modify: `docs/OBSERVABILITY_DEPLOYMENT_FA.md`

**Interfaces:**
- Consumes: current PostgreSQL/TimescaleDB volume and backup/restore scripts.
- Produces: documented RPO <= 15 minutes, RTO <= 1 hour, and 90-day retention
  configuration with a staging validation command.

- [ ] **Step 1: Add a recovery test checklist**

  Cover database restore, API restart, EA restart, unknown order replay,
  circuit-breaker persistence, and audit continuity.

- [ ] **Step 2: Implement retention configuration**

  Add explicit retention settings for order audit, broker responses, and logs.
  Keep secrets out of command output and backup filenames.

- [ ] **Step 3: Implement the staging recovery validator**

  The script must fail explicitly when backup, restore, schema verification,
  or post-restore smoke checks fail. It must report elapsed time for RTO
  measurement.

- [ ] **Step 4: Execute and record the recovery drill**

  Run the validator against a disposable/staging database, record measured
  RPO/RTO, and attach the result to the runbook. Do not claim production
  recovery based on a local-only run.

### Task 5: Close CI and staging governance

**Files:**
- Modify: `.github/workflows/python-validation.yml`
- Modify: `requirements/README.md`
- Create: `docs/CI_ENFORCEMENT.md`
- Modify: `docs/PROJECT_REVIEW_FA.md`

**Interfaces:**
- Consumes: existing CI jobs and Python 3.12 lockfiles.
- Produces: documented CI commands, staging requirements, and explicit manual
  branch-protection handoff.

- [ ] **Step 1: Add a CI check for runtime/lock alignment**

  Run the existing `scripts/validate_runtime_lock_alignment.ps1` or its
  canonical equivalent in CI without introducing a second conflicting command.

- [ ] **Step 2: Keep the full validation job stable**

  Ensure CI runs tests, Ruff, Mypy, migration validation, and Compose config
  validation under Python 3.12.

- [ ] **Step 3: Document enforcement as a human action**

  Record that a repository administrator must make the stable CI job a required
  status check on `main`, require PR review, and require branches to be
  up-to-date. Do not claim this is enforced from local files.

- [ ] **Step 4: Add staging concurrency and secret-injection requirements**

  Document the two-worker PostgreSQL test and secret rotation/injection
  evidence required before Demo activation.

### Task 6: Add ML promotion criteria and operational hygiene

**Files:**
- Modify: `src/python/ml/` existing evaluation/registry module
- Test: `tests/python/ml/`
- Modify: `docs/BACKTEST_STAGE_D.md`
- Modify: `docs/PROJECT_REVIEW_FA.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: current model registry, walk-forward reports, and artifact checksums.
- Produces: a combined evaluation record for risk-adjusted return, drawdown,
  Sharpe, and stability, plus explicit rollback metadata.

- [x] **Step 1: Add failing tests for incomplete model evaluation**

  Reject promotion when a model lacks dataset version, feature schema hash,
  checksum, walk-forward evidence, or one of the required metrics.

- [x] **Step 2: Implement the combined evaluation record**

  Preserve existing artifact compatibility and store metric values plus the
  evaluation policy version. Do not optimize for raw profit alone.

- [x] **Step 3: Add artifact and runtime-state hygiene**

  Keep only required fixtures in source control; exclude mutable caches,
  runtime databases, logs, and generated artifacts unless explicitly
  versioned and checksummed.

- [ ] **Step 4: Run ML and full regression validation**

  ```powershell
  py -3 -m pytest tests/python/ml -q --import-mode=importlib
  py -3 -m pytest -q --import-mode=importlib
  ```

## Phase gates

- Phase 0 exits only when all local quality commands are green.
- Phase 1 exits only with Demo E2E evidence; replay-only evidence is
  insufficient for claiming broker validation.
- Phase 2 exits only with two-person activation and recovery evidence.
- Phase 3 exits only after CI runs and a repository administrator confirms
  required status checks on `main`.
- Phase 4 exits only when model promotion criteria are tested and documented.

## Rollback

- Revert each phase as a separate change set.
- Keep Live Trading disabled during all phases.
- Retain the prior database image and backup before schema changes.
- Retain the prior model artifact and registry entry before model promotion.
- Never retry an unknown broker outcome automatically.
