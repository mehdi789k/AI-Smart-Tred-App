# Dependency layout

Dependency inputs are grouped by runtime surface:

- `base.in`: shared Python runtime dependencies.
- `api.in`: FastAPI, database, ZeroMQ, and API observability dependencies.
- `dashboard.in`: dashboard and API-boundary dependencies.
- `full.in`: repository-wide development, ML, dashboard, API, and test inputs.
- `ml.in`: CPU ML training runtime inputs used by the training Docker image.
- `mt5.in`: Windows-only MetaTrader 5 host integration.
- `test.in`: repository validation input layered on `full.in`.

Resolved, reproducible installs are kept in `lock/`:

- `lock/api.txt`
- `lock/dashboard.txt`
- `lock/full.txt`
- `lock/ml.txt`
- `lock/mt5.txt`

The MT5 lock must remain separate because `MetaTrader5` is a Windows terminal
integration and must not be installed in Linux Docker images. When an input file
changes, regenerate its corresponding lock file with `pip-compile` and review the
result before updating Docker or CI.

The repository-wide validation environment also pins `ruff` and `mypy` in
`lock/full.txt`. CI applies formatting, lint, and type checks to changed Python
files only, so existing technical-debt findings do not hide new regressions.
The complete test, compile, migration, and Compose checks still run against the
whole repository.

CI keeps these concerns explicit: the stable `test` job runs the repository
validation suite under Python 3.12, while the separate
`runtime-lock-alignment` job invokes the canonical disposable-environment
validator below. Do not add a second hand-written lock/runtime command to the
workflow; update the validator when its validation policy changes.

## Clean runtime and lock validation

The canonical runtime is Python 3.12. To validate the full lockfile in a
disposable environment without reading `.env` or connecting to MT5, run from
the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\validate_runtime_lock_alignment.ps1
```

The script creates and removes a temporary virtual environment outside the
repository, installs `requirements\lock\full.txt`, and runs compilation,
Ruff, Mypy, the non-MT5 pytest suite, Alembic, and Docker Compose configuration
checks. MT5-host tests remain covered by the separate `mt5.txt` lock because
the MetaTrader5 package is Windows-terminal-only.
