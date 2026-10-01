# Staged Test Coverage Uplift

## Goal

Increase trustworthy test coverage without weakening financial safeguards or
turning coverage into a proxy for correctness. Establish a reproducible,
lock-aligned baseline first, add tests for fail-closed risk and execution
boundaries, then raise the CI coverage floor in measured increments.

## Current Evidence

- The prior full test run was reported as 544 passed and 4 skipped, with 59%
  total coverage.
- The saved coverage database available for this follow-up reports 36%.
  Because it does not agree with the full-run result, it is not a reliable
  baseline for selecting or enforcing a threshold.
- CI currently collects coverage but does not fail on a repository-wide
  minimum.
- The runtime-lock validator exercises a reduced test set. Coverage used to
  set a repository gate must therefore come from the canonical full CI suite,
  not from the reduced validator suite.

## Design

### Baseline

Run the full canonical pytest suite on Python 3.12 with the repository's locked
runtime dependencies. Match the CI pytest options, including `--import-mode`
and coverage source (`src/python`). Write the coverage data and XML report to
the system temporary directory, not the repository root. Record the exact
command, dependency lock, result counts, total coverage, and per-file coverage
for relevant safety modules.

The baseline is accepted only if the full suite exits successfully and the
coverage report is produced. A failed or reduced run must not be used to set a
CI threshold.

### Test increments

Use the accepted baseline to choose a small first batch of tests for
fail-closed risk and order-admission behavior. Prefer deterministic unit tests
and the existing fake/stub boundaries. Tests must not initialize a real MT5
terminal, contact a broker, or submit real or demo orders.

Initially add or adjust tests and directly related test documentation only.
Do not change order-submission behavior, risk policies, or production modules
owned by another project agent. If a test exposes a production defect, stop
that branch of work, document the evidence and affected behavior, and request
separate approval before modifying production logic.

After each test batch, run the focused tests and the full canonical suite,
then compare coverage by file and total. Coverage increases are accepted only
when the tests assert meaningful behavior, including rejection and failure
paths where relevant; executing lines alone is not sufficient.

### Incremental CI floor

Do not change the CI threshold during baseline collection or the first test
batch. Once the measured total is reproducible, propose a floor five
percentage points above the accepted baseline, rounded down to a whole
percentage point. Raise the floor only after the full canonical suite
repeatedly passes at or above it. Continue in similarly measured increments
toward 80%; do not jump directly to 80% or lower the floor to accommodate a
regression.

Each threshold change updates the CI workflow and its documentation together.
The GitHub coverage gate must use the same source selection and test command as
the canonical suite.

## Safety and failure handling

- Keep live trading, demo execution, MT5 connectivity, and broker I/O outside
  the coverage test path.
- Preserve the existing test environment guards and isolated dashboard
  settings path.
- Use a unique temporary coverage location and remove only artifacts created
  by the validation run.
- If a test can reach an external financial side effect, treat that as a
  safety failure and exclude it until the boundary is isolated.
- If baseline results differ from CI or from a repeated run, investigate the
  environment and test selection before drawing coverage conclusions.

## Acceptance criteria

1. A fresh full-suite baseline runs with the Python 3.12 locked dependencies,
   exits successfully, and produces a coverage report outside the repository.
2. The report reconciles the prior 59% and 36% measurements or explicitly
   supersedes both with the reproducible full-suite result.
3. The first test-only increment verifies selected risk/order-admission
   rejection paths without real MT5 or broker access.
4. Focused and full-suite tests pass after the test increment.
5. No CI threshold changes before the baseline and test increment are
   reviewed; later threshold changes are incremental and match the canonical
   coverage command.
6. No unrelated user-owned data, logs, models, or market-data files are
   changed by coverage validation.

## Out of scope

- Increasing coverage by adding superficial line-execution tests.
- Rewriting production trading or risk logic as part of a coverage-only task.
- Activating a global 80% threshold before the measured increments support it.
- Changing GitHub branch protection or external repository settings.
