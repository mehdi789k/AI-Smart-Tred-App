# Task 1 report: execution safety authentication roles

Date: 2026-09-16
Plan: `docs/superpowers/plans/2026-09-16-execution-safety-auth.md`
Spec: `docs/superpowers/specs/2026-09-16-execution-safety-auth-design.md`
Status: Complete

## Scope delivered

- Added the `ORDER_REVIEW = "order_review"` role and the
  `API_AUTH_ORDER_REVIEW_TOKEN` static-key mapping.
- Added an explicit `allow_anonymous=False` application-factory option. The
  production default is fail-closed; only an explicit factory override can
  enable the legacy anonymous read-only test behavior.
- Protected `GET /api/v1/data/ohlc` with the read-only role and configured
  credentials by default.
- Protected `GET /api/v1/orders/unknown` with the order-review role. A
  read-only principal receives 403 and an unauthenticated caller receives
  401 before repository access.
- Preserved live execution enforcement for `signal_execution` and
  reconciliation enforcement for `emergency_stop`. An order-review principal
  cannot execute or reconcile.
- Kept `/health` public and did not add authentication to non-executing
  readiness behavior.
- Documented the new empty role key and production fail-closed requirement in
  `.env.example`; no real credential was added.

## Files changed

- `src/python/api/auth.py`
- `src/python/api/app.py`
- `.env.example`
- `tests/api/test_app.py`
- `tests/python/api/test_auth.py` (new)
- `tests/python/api/test_execution_safety.py`
- `docs/superpowers/sdd/2026-09-16-execution-safety-auth-task1-report.md`

No file under `src/mql5/` was modified.

## Test evidence

Baseline before the regression tests:

```text
py -m pytest -q tests/api/test_app.py tests/python/api/test_execution_safety.py --import-mode=importlib
17 passed
```

Final requested command:

```text
py -m pytest -q tests/api/test_app.py tests/python/api/test_execution_safety.py --import-mode=importlib
20 passed in 3.74s
```

Additional role-specific regression test:

```text
py -m pytest -q tests/python/api/test_auth.py --import-mode=importlib
1 passed in 3.09s
```

Formatting and linting:

```text
py -m ruff format --check src/python/api/auth.py src/python/api/app.py tests/api/test_app.py tests/python/api/test_execution_safety.py tests/python/api/test_auth.py
5 files already formatted

py -m ruff check src/python/api/auth.py src/python/api/app.py tests/api/test_app.py tests/python/api/test_execution_safety.py tests/python/api/test_auth.py
All checks passed!
```

The red phase was observed before implementation: the new route tests reached
the database without the new dependencies, the role mapping was absent, and
the order-review execution test was not yet protected by the new role. The
final suite passed after the production changes and deterministic symbol setup

## Safety and privacy checks

- Tests use injected fakes and the HTTP test client only.
- No MT5 connection, real account, or `order_send` call was executed.
- API tokens are supplied only as in-memory test environment values and are
  not logged or persisted by the implementation.
- No commit was created because this checkout has no Git metadata.

## Concerns / follow-up

- `allow_anonymous` is intentionally opt-in and should remain limited to
  isolated test factories; production callers must use the default.
- Existing execution validation still rejects a non-whitelisted symbol before
  credential validation, preserving the established test and safety behavior.
- This task does not implement durable execution-control persistence or alter
  gateway/reconciliation internals; those remain for later plan tasks.
- Validation was targeted as requested; the full repository test suite was not
  run.
## Follow-up fix: Task 1 review findings

- Added an anonymous `GET /api/v1/orders/unknown` regression test asserting `401` and verifying the repository getter is not called.
- Added a positive order-review-token test asserting a successful response and serialized unknown-order data using an injected repository fixture.
- Strengthened the static-key authentication test to assert the principal roles are exactly `{ORDER_REVIEW}` and that `principal.has(ORDER_REVIEW)` is true.

Validation:

```text
py -m pytest -q tests/api/test_app.py tests/python/api/test_auth.py --import-mode=importlib
12 passed

py -m pytest -q tests/api/test_app.py tests/python/api/test_execution_safety.py tests/python/api/test_auth.py --import-mode=importlib
23 passed in 4.16s

py -m ruff format --check tests/api/test_app.py tests/python/api/test_auth.py
2 files already formatted

py -m ruff check tests/api/test_app.py tests/python/api/test_auth.py
All checks passed!
```

No production files were changed and no Git/commit operations were used.