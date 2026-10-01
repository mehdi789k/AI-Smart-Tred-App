# P1.3 Runtime and Lockfile Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Python 3.12 the single verified runtime for tooling, lockfiles, CI, Docker, and local validation.

**Architecture:** Keep the existing `requirements/*.in` and generated `requirements/lock/*.txt` layout. Align project metadata and validation commands to Python 3.12, regenerate only the affected lockfiles with the repository's existing dependency workflow, then validate installation in a clean disposable virtual environment without touching `.env` or production data.

**Tech Stack:** Python 3.12, pip-tools/pip, Ruff, Mypy, pytest, Alembic, Docker Compose, PowerShell.

**Spec:** `docs/ARCHITECTURE.md`, `docs/PROJECT_ASSESSMENT_FA.md`, and the approved P1.3 design in the current conversation.

## Global Constraints

- Python 3.12 is the canonical runtime, matching `.python-version`, CI, and Docker.
- Do not expose, copy, or print `.env` credentials.
- Do not modify application trading behavior while aligning tooling.
- Preserve the separate Windows-only `requirements/lock/mt5.txt` surface.
- Do not delete user-owned environments, caches, databases, or lockfiles.
- Every generated lockfile must remain reproducible from its corresponding `requirements/*.in` input.
- Validation must include Ruff, Mypy, compilation, tests, migration validation, and Compose syntax validation.

---

### Task 1: Establish the dependency and runtime baseline

**Files:**
- Read: `.python-version`
- Read: `pyproject.toml`
- Read: `requirements/README.md`
- Read: `requirements/*.in`
- Read: `requirements/lock/*.txt`
- Read: `.github/workflows/python-validation.yml`
- Read: `ops/docker/Dockerfile.api`
- Read: `ops/docker/Dockerfile.dashboard`
- Read: `ops/docker/Dockerfile.ml`

**Interfaces:**
- Consumes: Existing runtime metadata and dependency input/lock files.
- Produces: A recorded baseline of all Python-version declarations and lockfile headers; no source-code behavior changes.

- [ ] **Step 1: Search all runtime declarations**

  Run:

  ```powershell
  rg -n "3\.11|3\.12|python_version|target-version|python-version|FROM python" `
    pyproject.toml .python-version requirements .github ops docs
  ```

  Expected: Every declaration is classified as either canonical Python 3.12, an intentional compatibility reference, or a mismatch requiring correction.

- [ ] **Step 2: Inspect lockfile generation metadata**

  Run:

  ```powershell
  Get-ChildItem requirements\lock\*.txt | ForEach-Object {
      Write-Host "==== $($_.FullName) ===="
      Get-Content $_.FullName -TotalCount 12
  }
  ```

  Expected: Each lockfile identifies its generating command/interpreter or otherwise can be traced to its matching `.in` file.

- [ ] **Step 3: Check tool availability without installing anything**

  Run:

  ```powershell
  py -3.12 --version
  py -3.12 -m pip --version
  py -3.12 -m piptools --version
  ```

  Expected: Python 3.12 is available. If `piptools` is unavailable, record that as a dependency-tooling blocker and install only inside a disposable validation environment after explicit dependency-manifest review.

- [ ] **Step 4: Preserve the baseline result**

  Record the exact interpreter versions and current validation status in the task session artifacts, not in the repository root.

### Task 2: Align static-analysis configuration to Python 3.12

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/python/test_project_configuration.py` (create only if an existing configuration-test location does not cover these declarations)

**Interfaces:**
- Consumes: The canonical version `3.12` from `.python-version`.
- Produces: Ruff `target-version = "py312"` and Mypy `python_version = "3.12"` with the existing source scope and safety settings unchanged.

- [ ] **Step 1: Add a failing configuration test**

  Add a test that reads `pyproject.toml` with `tomllib` and asserts:

  ```python
  assert config["tool"]["ruff"]["target-version"] == "py312"
  assert config["tool"]["mypy"]["python_version"] == "3.12"
  ```

- [ ] **Step 2: Run the focused test**

  Run:

  ```powershell
  py -3.12 -m pytest -q tests/python/test_project_configuration.py
  ```

  Expected: FAIL before the metadata change because the current values are `py311` and `3.11`.

- [ ] **Step 3: Update only the version declarations**

  Change:

  ```toml
  [tool.ruff]
  target-version = "py312"

  [tool.mypy]
  python_version = "3.12"
  ```

  Do not alter lint selection, exclusions, Mypy strictness, or source paths.

- [ ] **Step 4: Run the focused test again**

  Run:

  ```powershell
  py -3.12 -m pytest -q tests/python/test_project_configuration.py
  ```

  Expected: PASS.

- [ ] **Step 5: Run static checks against the changed metadata**

  Run:

  ```powershell
  py -3.12 -m ruff check src tests
  py -3.12 -m ruff format --check src tests
  py -3.12 -m mypy --follow-imports=skip src/python
  ```

  Expected: All commands pass without source behavior changes.

### Task 3: Regenerate and verify dependency lockfiles

**Files:**
- Modify, only if generated output changes: `requirements/lock/api.txt`
- Modify, only if generated output changes: `requirements/lock/dashboard.txt`
- Modify, only if generated output changes: `requirements/lock/full.txt`
- Modify, only if generated output changes: `requirements/lock/ml.txt`
- Modify, only if generated output changes: `requirements/lock/mt5.txt`
- Read: `requirements/base.in`, `requirements/api.in`, `requirements/dashboard.in`, `requirements/full.in`, `requirements/ml.in`, `requirements/mt5.in`, `requirements/test.in`

**Interfaces:**
- Consumes: Each existing `.in` file and Python 3.12 `pip-compile`.
- Produces: Lockfiles regenerated deterministically for Python 3.12; no unrequested dependency additions or removals.

- [ ] **Step 1: Confirm the generator command**

  Run:

  ```powershell
  py -3.12 -m piptools compile --help
  ```

  Expected: The installed pip-tools version supports the repository's existing lock generation options. Do not invent a new dependency-management system.

- [ ] **Step 2: Capture the current lockfile diff**

  Run:

  ```powershell
  git diff -- requirements
  ```

  Expected: No unrelated pre-existing changes are overwritten. If Git metadata is unavailable, copy hashes and timestamps to the session artifact instead.

- [ ] **Step 3: Regenerate each lockfile from its matching input**

  Use the repository's established command/options. The intended command shape is:

  ```powershell
  py -3.12 -m piptools compile `
    --generate-hashes `
    --output-file requirements\lock\full.txt `
    requirements\full.in
  ```

  Repeat for `api.in`, `dashboard.in`, `ml.in`, and `mt5.in`, preserving their existing options and output paths. If an input includes another input with `-c` or `-r`, preserve that relationship.

- [ ] **Step 4: Verify lockfile provenance**

  Run:

  ```powershell
  rg -n "python|pip-compile|piptools|generated" requirements\lock\*.txt
  ```

  Expected: The generated headers and constraints are consistent with Python 3.12 and the corresponding `.in` files.

- [ ] **Step 5: Review the dependency diff**

  Run:

  ```powershell
  git diff --stat -- requirements
  git diff -- requirements\lock
  ```

  Expected: Only interpreter-marker, resolver, or reproducibility changes caused by Python 3.12 are present. Any unexpected package upgrade must stop the task for review rather than being accepted automatically.

### Task 4: Add clean-install validation

**Files:**
- Create: `scripts/validate_runtime_lock_alignment.ps1`
- Modify: `requirements/README.md`
- Test: `tests/python/test_project_configuration.py`

**Interfaces:**
- Consumes: A selected lockfile, Python 3.12 launcher, and a temporary virtual-environment path.
- Produces: Exit code `0` only when installation and all configured quality gates pass; never reads or prints `.env`.

- [ ] **Step 1: Write the failing script contract test**

  Assert that the script:

  - requires Python 3.12;
  - creates a temporary environment outside the repository;
  - installs from `requirements/lock/full.txt`;
  - runs compile, Ruff, Mypy, pytest, Alembic, and Compose checks;
  - removes the temporary environment in a `finally` block;
  - does not reference `.env` contents.

- [ ] **Step 2: Run the contract test**

  Run:

  ```powershell
  py -3.12 -m pytest -q tests/python/test_project_configuration.py
  ```

  Expected: FAIL until the script and documentation contract are implemented.

- [ ] **Step 3: Implement the disposable validation script**

  The script must use a unique directory under `$env:TEMP`, invoke `py -3.12 -m venv`, install only the selected lockfile, run each validation command with `$ErrorActionPreference = "Stop"`, and remove only its own resolved temporary directory.

- [ ] **Step 4: Document safe usage**

  Add a section to `requirements/README.md`:

  ```text
  py -ExecutionPolicy Bypass -File scripts/validate_runtime_lock_alignment.ps1
  ```

  State that the command validates dependency reproducibility and does not connect to MT5 or submit orders.

- [ ] **Step 5: Run the clean-install validation**

  Run:

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass `
    -File scripts\validate_runtime_lock_alignment.ps1
  ```

  Expected: A fresh Python 3.12 environment installs successfully and every configured check exits zero.

### Task 5: Synchronize CI, Docker, and project documentation

**Files:**
- Modify: `.github/workflows/python-validation.yml` only where needed to call the canonical Python 3.12 validation path
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/PROJECT_ASSESSMENT_FA.md`
- Modify: `docs/PROJECT_REVIEW_FA.md`
- Modify: `requirements/README.md`

**Interfaces:**
- Consumes: Clean-install evidence from Task 4.
- Produces: Documentation and CI statements that match the executable validation path.

- [ ] **Step 1: Add a failing documentation assertion**

  Extend the configuration test to assert that repository documentation names Python 3.12 as canonical and does not state that P1.3 is still unresolved after validation evidence is recorded.

- [ ] **Step 2: Update CI without weakening checks**

  Keep:

  ```yaml
  python-version: "3.12"
  ```

  Ensure CI installs the regenerated full lockfile and runs the same full-repository quality gates. Do not change changed-file validation into a weaker or silent success path.

- [ ] **Step 3: Update status documentation**

  Mark P1.3 as locally complete only with the clean-install evidence. Keep P1.5 and P0.1 open until their independent gates pass.

- [ ] **Step 4: Run documentation and configuration tests**

  Run:

  ```powershell
  py -3.12 -m pytest -q tests/python/test_project_configuration.py
  ```

  Expected: PASS.

### Task 6: Final P1.3 verification and handoff

**Files:**
- Read: all files changed above
- Modify: none unless verification exposes a directly related defect

**Interfaces:**
- Consumes: All P1.3 changes and clean-install evidence.
- Produces: A release-gate result that explicitly says whether P1.3 passed and identifies the next gate, P1.5.

- [ ] **Step 1: Run the repository-wide validation suite**

  Run:

  ```powershell
  py -3.12 -m ruff check src tests
  py -3.12 -m ruff format --check src tests
  py -3.12 -m mypy --follow-imports=skip src/python
  py -3.12 -m compileall -q src tests
  py -3.12 -m pytest -q --import-mode=importlib
  py -3.12 -m alembic upgrade head
  docker compose --profile dashboard config --quiet
  ```

  Expected: Every command exits zero.

- [ ] **Step 2: Inspect root hygiene**

  Run:

  ```powershell
  Get-ChildItem -Force
  ```

  Expected: No temporary virtual environment, lock-generation cache, report, or debug artifact remains in the repository root.

- [ ] **Step 3: Record the P1.3 handoff**

  Record exact commands, exit codes, Python version, lockfile set, and unresolved environmental blockers in `docs/PROJECT_REVIEW_FA.md`. Do not claim P1.5 or P0.1 completion from P1.3 evidence.

- [ ] **Step 4: Commit the coherent P1.3 change**

  ```powershell
  git add pyproject.toml requirements .github scripts docs tests
  git commit -m "chore: align Python runtime and dependency locks" `
    -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
  ```

  If the checkout is not a Git repository, report that commit verification is unavailable and do not fabricate commit evidence.

