# Secure Dashboard Environment Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated, masked `.env` management section to the existing Streamlit Settings page with safe editing, deletion, and `demo`/`live` profile resets.

**Architecture:** Keep filesystem and parsing logic in a focused `src/python/dashboard/env_manager.py` module. The Streamlit page will call that module only after a session-scoped admin-token check, render masked values, and require explicit confirmation for destructive/profile operations. Writes are backed up and atomic; environment changes require a service restart and never enable live trading automatically.

**Tech Stack:** Python 3.12, Streamlit, pytest, pathlib, dataclasses, hmac.

**Spec:** [docs/superpowers/specs/2026-09-22-dashboard-env-management-design.md](../specs/2026-09-22-dashboard-env-management-design.md)

## Global Constraints

- Never return or render raw values for keys matching `PASSWORD`, `TOKEN`, `SECRET`, `DATABASE_URL`, or `PRIVATE_KEY`.
- Resolve and validate the managed `.env` path beneath the project root; reject traversal and invalid variable names.
- Require `DASHBOARD_ADMIN_TOKEN`; if it is absent the management section stays locked.
- Preserve current secret values during profile reset; never generate default credentials.
- `MT5_AUTO_TRADING_ENABLED` remains `false` after either profile reset.
- Back up before every write, write atomically with same-directory temporary file plus `os.replace`, and surface failures.
- Do not add an API endpoint or modify `docs/API_CONTRACT.md`.
- Keep changes within `src/python/dashboard/`, its tests, and directly related documentation.

---

### Task 1: Build the environment manager

**Files:**
- Create: `src/python/dashboard/env_manager.py`
- Test: `tests/python/dashboard/test_env_manager.py`

**Interfaces:**
- Produces `EnvEntry(key: str, value: str, is_sensitive: bool, display_value: str)`.
- Produces `EnvironmentManager(project_root: Path, env_path: Path | None = None, backup_dir: Path | None = None)`.
- Produces `EnvironmentManager.read_entries() -> list[EnvEntry]`, `update(values: Mapping[str, str]) -> None`, `delete() -> None`, and `reset(profile: Literal["demo", "live"]) -> None`.
- Produces `is_sensitive_key(key: str) -> bool`, `mask_value(value: str) -> str`, and profile builders usable directly by tests.

- [ ] **Step 1: Write failing parser and masking tests**

```python
def test_read_entries_masks_sensitive_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MT5_LOGIN=123\nAPI_AUTH_TOKEN=secret\n# keep\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    entries = manager.read_entries()

    assert [(entry.key, entry.display_value) for entry in entries] == [
        ("MT5_LOGIN", "123"),
        ("API_AUTH_TOKEN", "********"),
    ]

def test_sensitive_update_requires_explicit_replacement(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_AUTH_TOKEN=old\nMT5_LOGIN=123\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    manager.update({"MT5_LOGIN": "456"})

    assert env.read_text(encoding="utf-8") == "API_AUTH_TOKEN=old\nMT5_LOGIN=456\n"
```

- [ ] **Step 2: Run the focused tests and verify the new module is missing**

Run: `python -m pytest tests/python/dashboard/test_env_manager.py -q`

Expected: FAIL because `src.python.dashboard.env_manager` does not exist.

- [ ] **Step 3: Implement parsing, sensitivity detection, and validation**

Implement line-preserving `KEY=VALUE` parsing, valid-name checks, and sensitive-key masking. Treat an update value of `None`/omitted as “preserve current value”; never accept the literal masked marker as a replacement for a sensitive value. Reject invalid keys and values containing newline characters with a typed `EnvironmentManagerError`.

- [ ] **Step 4: Add atomic backup, update, delete, and profile reset behavior**

Before each write, copy an existing `.env` to a timestamped file under `backups/`; then write a same-directory temporary file and replace the target with `os.replace`. `delete()` must be idempotent for a missing file but still report whether a file was removed. `reset("demo")` must set `APP_ENV=development`, `MT5_DEMO_ENABLED=true`, `MT5_ENABLED=false`, and `MT5_AUTO_TRADING_ENABLED=false` while applying conservative limits from `.env.example`; `reset("live")` must set `APP_ENV=production`, `MT5_DEMO_ENABLED=false`, preserve existing sensitive values, and keep `MT5_AUTO_TRADING_ENABLED=false`.

- [ ] **Step 5: Add failure and traversal tests**

```python
def test_path_traversal_is_rejected(tmp_path):
    with pytest.raises(EnvironmentManagerError):
        EnvironmentManager(tmp_path, env_path=tmp_path / "nested" / ".." / ".." / ".env")

def test_reset_preserves_credentials_and_disables_auto_trading(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "MT5_PASSWORD=keep-me\nAPI_AUTH_TOKEN=keep-token\n"
        "MT5_AUTO_TRADING_ENABLED=true\n",
        encoding="utf-8",
    )
    EnvironmentManager(tmp_path, env_path=env).reset("live")
    text = env.read_text(encoding="utf-8")
    assert "MT5_PASSWORD=keep-me" in text
    assert "API_AUTH_TOKEN=keep-token" in text
    assert "MT5_AUTO_TRADING_ENABLED=false" in text
```

- [ ] **Step 6: Run the focused suite and commit**

Run: `python -m pytest tests/python/dashboard/test_env_manager.py -q`

Expected: PASS with coverage for masking, preserve-on-edit, atomic write failures, deletion, both profiles, and traversal rejection.

Commit: `git add src/python/dashboard/env_manager.py tests/python/dashboard/test_env_manager.py && git commit -m "feat: add safe environment file manager"`

### Task 2: Integrate the locked management section into Settings

**Files:**
- Modify: `src/python/dashboard/app.py` in the Settings branch and translation map
- Test: `tests/python/dashboard/test_dashboard_env_management.py`

**Interfaces:**
- Consumes `EnvironmentManager` from Task 1.
- Produces a Settings expander that is locked unless the submitted password equals `DASHBOARD_ADMIN_TOKEN` using `hmac.compare_digest`.
- Keeps admin credentials out of `st.session_state`, logs, and rendered JSON.

- [ ] **Step 1: Write integration tests for lock and safe rendering**

```python
def test_environment_manager_is_locked_without_admin_token(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ADMIN_TOKEN", raising=False)
    assert dashboard_app._dashboard_env_access_allowed("") is False

def test_environment_manager_access_uses_constant_time_comparison(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "expected")
    assert dashboard_app._dashboard_env_access_allowed("expected") is True
    assert dashboard_app._dashboard_env_access_allowed("wrong") is False
```

- [ ] **Step 2: Run the focused integration tests and verify failure**

Run: `python -m pytest tests/python/dashboard/test_dashboard_env_management.py -q`

Expected: FAIL because the access helper and UI integration do not exist.

- [ ] **Step 3: Add the session-scoped authentication gate**

Add a helper that reads the configured token only for comparison, stores only a boolean unlock state, and renders a password input plus lock status. If the environment variable is unset, show an explicit configuration error and no file values.

- [ ] **Step 4: Add masked table and non-secret edit controls**

Render entries from `EnvironmentManager.read_entries()`. Use text inputs for non-sensitive values and blank password inputs for sensitive replacements. On save, pass only changed non-empty fields plus explicitly entered sensitive replacements to `update()`, then show a restart-required warning. Do not use `st.json()` for raw environment content.

- [ ] **Step 5: Add destructive/profile controls with confirmation**

Use separate confirmation checkboxes/buttons for delete, reset demo, and reset live. Call `delete()`/`reset()` only after confirmation, show the backup/write result, clear the unlock boolean after the operation, and warn that running API/dashboard/collector processes must be restarted. Never call trading-start functions from this section.

- [ ] **Step 6: Run dashboard tests and commit**

Run: `python -m pytest tests/python/dashboard/test_dashboard_env_management.py tests/python/dashboard/test_dashboard_integration.py -q`

Expected: PASS with no unhandled Streamlit render exception.

Commit: `git add src/python/dashboard/app.py tests/python/dashboard/test_dashboard_env_management.py && git commit -m "feat: manage environment settings from dashboard"`

### Task 3: Document operations and run regression validation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DOCKER_GUIDE.md` or the existing local dashboard runbook section identified by search
- Test: existing `tests/python/dashboard/test_dashboard_smoke.py`

**Interfaces:**
- Documents `DASHBOARD_ADMIN_TOKEN`, masked-secret behavior, backups, profile semantics, and restart requirements.
- Leaves `docs/API_CONTRACT.md` unchanged.

- [ ] **Step 1: Add operational documentation**

Document that operators must set `DASHBOARD_ADMIN_TOKEN` outside source control, that raw secrets are never displayed, that backups are written under `backups/`, and that `live` reset does not enable order execution.

- [ ] **Step 2: Extend smoke coverage**

Add the Settings page to the existing safe-render assertions with no admin token and verify the section remains locked without raising an exception.

- [ ] **Step 3: Run focused validation**

Run: `python -m pytest tests/python/dashboard/test_env_manager.py tests/python/dashboard/test_dashboard_env_management.py tests/python/dashboard/test_dashboard_integration.py -q`

Expected: PASS.

- [ ] **Step 4: Run lint/type checks for changed Python files**

Run: `ruff check src/python/dashboard/env_manager.py src/python/dashboard/app.py tests/python/dashboard/test_env_manager.py tests/python/dashboard/test_dashboard_env_management.py`

Expected: PASS with no new diagnostics.

- [ ] **Step 5: Inspect repository hygiene**

Run: `git status --short; Get-ChildItem -Force`

Expected: only intended source, test, and documentation changes remain; no `.env` contents, temporary files, or generated runtime artifacts are added.

- [ ] **Step 6: Commit documentation and validation changes**

Commit: `git add docs/ARCHITECTURE.md docs/DOCKER_GUIDE.md tests/python/dashboard/test_dashboard_smoke.py && git commit -m "docs: describe dashboard environment operations"`
