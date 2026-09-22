# Task 3 report — dashboard environment operations

## Changed files

- `docs/ARCHITECTURE.md`: documented the Streamlit-only environment-management boundary, fail-closed token gate, masking and replacement behavior, timestamped backups, demo/live reset safety, restart requirement, and unchanged API contract.
- `docs/deployment/DOCKER_GUIDE.md`: added the operator runbook for `DASHBOARD_ADMIN_TOKEN`, secret handling, backup-before-mutation behavior, conservative demo reset, non-enabling live reset, and required process restarts.
- `tests/python/dashboard/test_dashboard_smoke.py`: added a Settings-page smoke test that removes `DASHBOARD_ADMIN_TOKEN`, disables MT5 dashboard access, and verifies the page renders without an unhandled exception. The existing Windows module skip remains unchanged.

`docs/API_CONTRACT.md` was not modified. No credentials, `.env` contents, or runtime artifacts were added.

## Documented behavior

The dashboard requires `DASHBOARD_ADMIN_TOKEN` to be supplied outside source control. Without it, environment management stays locked. Sensitive values remain masked and are replaced through blank password fields without exposing the current value. Replacement, profile reset, and deletion operations back up the environment with a timestamp under `backups/` first. Demo reset is conservative and disables MT5/live execution. Live reset changes environment mode while preserving sensitive values, does not enable order execution, and keeps `MT5_AUTO_TRADING_ENABLED=false`. Operators must restart dashboard, API, and collector processes after changes.

## Validation

- `py -3.12 -m pytest tests/python/dashboard/test_env_manager.py tests/python/dashboard/test_dashboard_env_management.py tests/python/dashboard/test_dashboard_integration.py -q` — **59 passed**.
- `py -3.12 -m ruff check src/python/dashboard/env_manager.py src/python/dashboard/app.py tests/python/dashboard/test_env_manager.py tests/python/dashboard/test_dashboard_env_management.py` — **failed** on a pre-existing I001 import-order issue in `src/python/dashboard/app.py`; no implementation file was changed for this task.
- `git diff --check` — **passed** (only Git line-ending warnings for changed text files).
- `py -3.12 -m pytest tests/python/dashboard/test_dashboard_smoke.py -q` — **2 skipped** on Windows, as required by the existing module-level skip behavior.

## Commit

`e04b577` — `docs: describe dashboard environment operations`

Only the three intended Task 3 files were staged and committed. Existing unrelated worktree changes remain unstaged and untouched.

## Concerns

The requested Ruff command remains red because Task 1/2's existing `src/python/dashboard/app.py` import block is not isort-compliant (`I001`). Fixing it would modify protected implementation outside this documentation/smoke task, so it was intentionally left unchanged.

## Final fix wave — 2026-09-22

### Findings addressed

- Sensitive admin-token and replacement password widgets use one-shot state cleanup; raw values are removed from `st.session_state` after unlock/save submission.
- `DATABASE_URL`, database URI/URL aliases, and database-prefixed URL/URI names are classified as sensitive and masked.
- New environment keys are accepted only when present in the project `.env.example` allowlist; updates to already-existing keys remain permitted.
- Delete/reset controls now show an explicit warning to stop active API, collector, broker, and trading services first; no automatic stopping was added, and explicit checkboxes remain required.
- Environment mutations are serialized by a process-wide re-entrant lock and atomic writes use unique same-directory temporary files before `os.replace`.
- Corrected dashboard import ordering for Ruff I001.

### Regression coverage

Added tests for database URL masking, allowlist rejection and existing-key updates, unique temporary files, sensitive Streamlit session-state cleanup, and the updated atomic-write cleanup behavior.

### Validation commands and outputs

- `pytest -q tests\\python\\dashboard\\test_env_manager.py tests\\python\\dashboard\\test_dashboard_env_management.py` — **17 passed**.
- `ruff check src\\python\\dashboard\\env_manager.py src\\python\\dashboard\\app.py tests\\python\\dashboard\\test_env_manager.py tests\\python\\dashboard\\test_dashboard_env_management.py` — **All checks passed**.
- `git --no-pager diff --check` — **Passed** (Git emitted only normal LF/CRLF conversion warnings).

Unrelated pre-existing worktree changes were not staged.
