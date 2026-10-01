# Staged Test Coverage Uplift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a reproducible full-suite coverage baseline, improve tests around fail-closed readiness, and prepare safe incremental CI coverage gates.

**Architecture:** Reuse the canonical Windows CI dependency locks and pytest selection. Store the virtual environment, coverage database, and XML report under a unique system temporary directory; keep test additions isolated to the existing live-readiness fake-connector suite. Decide the next CI floor from repeated full-suite measurements, not from a stale or reduced report.

**Tech Stack:** Python 3.12, pytest, pytest-cov, coverage.py, PowerShell, existing project lockfiles.

**Spec:** [2026-09-26-staged-coverage-uplift-design.md](../specs/2026-09-26-staged-coverage-uplift-design.md)

## Global Constraints

- Use Python 3.12 and install `requirements\lock\full.txt` plus `requirements\lock\mt5.txt`.
- Coverage for the CI floor must come from the complete canonical pytest suite and `src/python` source selection.
- Tests must not initialize a real MT5 terminal, contact a broker, or submit real or demo orders.
- Initial source changes are prohibited; add or adjust tests and directly related test documentation only.
- Keep coverage data and XML in a unique system temporary directory, not in the repository.
- Do not raise a CI threshold until the baseline and test increment are reviewed.
- Do not change or remove existing user-owned data, logs, models, or market-data files.

---

## File Map

- `tests/python/execution/test_live_readiness.py` — add deterministic rejection/failure cases using the existing `FakeConnector` and `valid_connector` helpers.
- `.github/workflows/python-validation.yml` — do not change in this first increment; any later threshold edit requires an accepted, repeated baseline.
- `docs/CI_ENFORCEMENT.md` — update only when a numerical threshold is later approved and enforced.
- `docs/superpowers/specs/2026-09-26-staged-coverage-uplift-design.md` — governing design; no implementation changes.

## Task 1: Capture the canonical full-suite baseline

**Files:**
- No repository files are to be changed.
- Temporary files: unique directory under `$env:TEMP` for venv and coverage outputs.

**Interfaces:**
- Consumes: `requirements\lock\full.txt`, `requirements\lock\mt5.txt`, canonical pytest options from `.github/workflows/python-validation.yml`.
- Produces: successful full-suite result counts, total and per-file coverage output, and an XML report in the temporary directory.

- [ ] **Step 1: Record the current dirty-worktree inventory**

Run in the repository root:

```powershell
git status --short
git diff --name-only
```

Retain the output in the task session so pre-existing changes can be distinguished from validation side effects. Do not stage or overwrite any pre-existing file.

- [ ] **Step 2: Create a disposable Python 3.12 environment and full locks**

Run in the repository root:

```powershell
$ErrorActionPreference = "Stop"
$tempRoot = Join-Path $env:TEMP ("smart-mt5-coverage-" + [guid]::NewGuid())
$venvPath = Join-Path $tempRoot "venv"
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
py -3.12 -m venv $venvPath
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 venv creation failed." }
$python = Join-Path $venvPath "Scripts\python.exe"
& $python -m pip install -r requirements\lock\full.txt -r requirements\lock\mt5.txt
if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed." }
```

Expected: both lockfiles install into the disposable environment; no repository dependency manifest is changed.

- [ ] **Step 3: Run the complete CI pytest selection with temporary coverage outputs**

Continue in the same PowerShell process, from the repository root:

```powershell
$env:COVERAGE_FILE = Join-Path $tempRoot ".coverage"
$coverageXml = Join-Path $tempRoot "coverage.xml"
& $python -m pytest -q --import-mode=importlib `
    --cov=src/python `
    --cov-report=term-missing:skip-covered `
    "--cov-report=xml:$coverageXml"
$pytestExit = $LASTEXITCODE
if ($pytestExit -ne 0) { throw "Canonical pytest baseline failed with exit code $pytestExit." }
if (-not (Test-Path -LiteralPath $coverageXml)) { throw "Coverage XML was not created." }
```

Expected: pytest runs the full test selection from the canonical CI workflow, exits zero, prints a total coverage percentage and per-file report, and creates `coverage.xml` under `$tempRoot`.

- [ ] **Step 4: Reconcile and record the baseline**

Record the exact lock versions, test counts, total coverage, and coverage percentages for `src/python/execution/live_readiness.py`, `src/python/risk/calculator.py`, `src/python/risk/manager.py`, and `src/python/execution/live_order_workflow.py`. Compare the fresh result with the earlier reported 59% and saved 36% snapshot. Use the fresh full-suite result as the new baseline; do not use a reduced test run to set a gate.

- [ ] **Step 5: Verify worktree safety and clean only this run's temporary paths**

Run `git status --short` and compare it with Step 1. If a pre-existing modified data, log, model, or market-data file changed during validation, stop and report it; do not restore or delete it. Once the pytest process has exited, remove only the exact `$tempRoot` created in Step 2. Confirm `Test-Path -LiteralPath $tempRoot` returns false.

Expected: no baseline output or coverage database remains in the repository; unrelated existing worktree changes remain untouched.

- [ ] **Step 6: Review the baseline before proceeding**

Present the result and the exact changed paths to the user. Proceed to Task 2 only if the full suite and coverage report succeeded and no unrelated user-owned data was modified.

## Task 2: Extend fake-only live-readiness failure coverage

**Files:**
- Modify: `tests/python/execution/test_live_readiness.py`
- Do not modify: `src/python/execution/live_readiness.py`, any order-sending module, or any user data.

**Interfaces:**
- Consumes: existing `FakeConnector`, `valid_connector(now)`, and `validate_live_readiness`.
- Produces: tests for invalid freshness policy, future/unparseable timestamps, and unavailable account/symbol data. All checks remain read-only and use local fakes.

- [ ] **Step 1: Confirm candidate branches are still uncovered in Task 1's full report**

Use the `coverage.xml`/per-file report from Task 1 to check these exact cases in `src/python/execution/live_readiness.py`:

1. `max_tick_age_seconds <= 0` and non-finite age limits add `invalid_tick_age_limit`.
2. A future timestamp and an unparseable timestamp reject readiness as `stale_tick` and `tick_timestamp_unavailable`, respectively.
3. Exceptions from `get_account_summary`, `get_symbols_list`, and `get_symbol_info` fail closed with their corresponding unavailable reasons.

Do not add a duplicate case if the baseline already covers it; report that branch as already covered and add only the remaining cases.

- [ ] **Step 2: Add a test for invalid tick-age policy**

Add this parameterized test to `tests/python/execution/test_live_readiness.py`:

```python
@pytest.mark.parametrize("max_age", [0, -1, float("nan"), float("inf")])
def test_readiness_rejects_invalid_tick_age_policy(max_age):
    now = datetime.now(timezone.utc)
    report = validate_live_readiness(
        valid_connector(now),
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        max_tick_age_seconds=max_age,
        now=now,
    )

    assert report.ready is False
    assert "invalid_tick_age_limit" in report.reasons
```

- [ ] **Step 3: Add tests for future and invalid timestamps**

Add these tests:

```python
def test_readiness_rejects_future_tick_timestamp():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now + timedelta(seconds=1))

    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "XAUUSD:stale_tick" in report.reasons


def test_readiness_rejects_unparseable_tick_timestamp():
    now = datetime.now(timezone.utc)
    connector = valid_connector(now)
    connector.symbols["XAUUSD"]["timestamp"] = "not-a-timestamp"

    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "XAUUSD:tick_timestamp_unavailable" in report.reasons
```

- [ ] **Step 4: Add a fake connector failure test**

Add this test using a local subclass; do not mock or import the real MT5 connector:

```python
def test_readiness_fails_closed_when_account_and_symbol_services_fail():
    now = datetime.now(timezone.utc)

    class UnavailableConnector(FakeConnector):
        def get_account_summary(self):
            raise OSError("account service unavailable")

        def get_symbols_list(self, visible_only=True):
            raise OSError("symbol watch unavailable")

        def get_symbol_info(self, symbol):
            raise OSError("symbol data unavailable")

    connector = UnavailableConnector(
        account={},
        symbols={},
        connected=True,
    )
    report = validate_live_readiness(
        connector,
        expected_login=123,
        expected_server="LiveBroker",
        allowed_symbols=frozenset({"XAUUSD"}),
        now=now,
    )

    assert report.ready is False
    assert "account_unavailable" in report.reasons
    assert "symbol_watch_unavailable" in report.reasons
    assert "XAUUSD:symbol_data_unavailable" in report.reasons
```

- [ ] **Step 5: Run focused tests and quality checks**

Run:

```powershell
py -3.12 -m pytest -q --import-mode=importlib tests\python\execution\test_live_readiness.py
py -3.12 -m ruff check tests\python\execution\test_live_readiness.py
py -3.12 -m ruff format --check tests\python\execution\test_live_readiness.py
```

Expected: all focused cases pass; lint and formatting pass. If a test exposes a production defect, do not adjust source in this task; report the case and request separate approval.

- [ ] **Step 6: Run the canonical full suite with temporary coverage**

Repeat Task 1 Step 3 with the disposable locked Python environment and a new temporary coverage database/XML path. Compare total and relevant-file coverage against the accepted baseline. Confirm that each added test asserts the fail-closed result rather than only line execution.

- [ ] **Step 7: Review and commit the test-only increment**

Inspect `git diff -- tests\python\execution\test_live_readiness.py` and `git status --short`. Verify no production file or user-owned data is modified by this task. Stage only the test file and commit:

```powershell
git add -- tests/python/execution/test_live_readiness.py
git commit -m "test: cover fail-closed live readiness edges" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: the commit contains only the approved test file.

## Task 3: Review the first measured increment

**Files:**
- No repository changes in this review checkpoint.

**Interfaces:**
- Consumes: successful full-suite coverage measurements from Tasks 1 and 2.
- Produces: a reviewed decision on whether the first safety-test increment is sufficient to plan a numerical CI floor.

- [ ] **Step 1: Compare the measurements**

Compare the unrounded total from Task 1 with Task 2 and calculate the candidate first floor as `floor(B + 5)`, where `B` is the accepted baseline percentage. Do not substitute the stale 36% snapshot or the previously reported rounded 59% if the fresh XML contains a different measured value.

- [ ] **Step 2: Report the measured result**

Report baseline `B`, Task 2 percentage, test counts, candidate floor, and any mismatch with the earlier measurements. Do not edit the CI workflow at this checkpoint.

- [ ] **Step 3: Decide whether more test batches are required**

If Task 2 coverage is below the candidate floor, do not enable a failing CI gate. Stop this plan after reporting the exact uncovered safety-relevant files and propose the next test-only batch based on that report. Each later batch requires its own reviewed plan and the same fake-only safety boundary.

- [ ] **Step 4: Define the gate only when evidence supports it**

Only after the full suite repeatedly passes at or above the candidate floor, present the numerical floor and evidence for user review. A separate approved change may then add `--cov-fail-under=<approved floor>` to the canonical pytest command and document the same value in `docs/CI_ENFORCEMENT.md`. Keep the CI workflow and documentation unchanged in this plan.

## Handoff checkpoint

After Tasks 1 and 2, stop and report the verified baseline and safety-test increment before starting another coverage batch. The next batch and any CI floor require a new coverage review; never extrapolate the 80% target from a single increment.
