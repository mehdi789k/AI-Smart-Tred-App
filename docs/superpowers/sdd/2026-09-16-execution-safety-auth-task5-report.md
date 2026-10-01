# Task 5 report: production schema startup

Date: 2026-09-16
Status: Complete after independent review and remediation

## Delivered

- Production lifespan now performs read-only Alembic and execution-control
  schema verification instead of calling `Base.metadata.create_all`.
- Local/test initialization remains explicit through `initialize_for_tests()`.
- Production mode rejects missing `DATABASE_URL` and SQLite configuration.
- Runtime verification rejects multiple Alembic revision rows, missing or
  malformed execution-control columns, wrong nullability/types, primary keys,
  or check constraints.
- `scripts/migrate_db.py` upgrades to head, verifies the schema, and records
  system versions.

## Validation

```text
py -m pytest -q tests/api/test_zmq_gateway.py tests/python/data/test_runtime.py tests/python/data/test_schema_verifier.py tests/api/test_app.py --import-mode=importlib
29 passed

py -m alembic upgrade head
passed

docker compose --profile dashboard config --quiet
passed
```
