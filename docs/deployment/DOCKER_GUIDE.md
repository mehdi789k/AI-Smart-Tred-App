# Docker and Compose guide

The checked-in `docker-compose.yml` defines these services:

| Service | Profile | Purpose |
|---|---|---|
| `timescaledb` | default | PostgreSQL 15 with TimescaleDB |
| `api` | default | FastAPI HTTP boundary on port 8000 |
| `ml-training` | `training` | Offline model training and backtesting |
| `dashboard` | `dashboard` | Streamlit dashboard; execution remains disabled unless explicitly configured |

There are no `redis` or `pgadmin` services. The dashboard is available through the optional `dashboard` profile. The
profile defaults to non-live operation: `MT5_AUTO_TRADING_ENABLED`,
`MT5_LEGACY_ORDER_PATH_ENABLED`, and `MT5_DEMO_ENABLED` are all false unless
the operator explicitly sets them in the environment.

## Prerequisites

Create a local `.env` from `.env.example` and set at least:

```text
POSTGRES_PASSWORD=<local secret>
API_AUTH_TOKEN=<local secret>
MODEL_VERSION=model-<release-id>
```

The API container uses `host.docker.internal:5555` as its default ZeroMQ
endpoint so it can reach an EA running on the host. Set
`ZMQ_EXECUTION_ENDPOINT` explicitly when the EA is elsewhere. Live execution
remains disabled by default in the EA and must only be enabled on a demo
account after the safety gates have been verified.

The `MetaTrader5` Python package is intentionally excluded from the Docker
requirements because it is a Windows-only host integration. For direct MT5
data collection, install the host-only requirements in the same Python
environment used to run the collector or dashboard:

```powershell
py -3.12 -m pip install -r requirements/lock/mt5.txt
```

`requirements/api.in` and `requirements/dashboard.in` contain the smaller
runtime dependency sets used by the API and Dashboard images. The Dashboard
image is intentionally MT5-free and must consume host-collected data through
the API boundary. If a Docker package mirror reports a wheel hash mismatch,
do not bypass the hash check; verify the mirror/cache and rebuild from a
trusted package source.

The checked-in `requirements/lock/full.txt`, `requirements/lock/api.txt`,
`requirements/lock/dashboard.txt`, and `requirements/lock/mt5.txt` files are
generated with `pip-tools` from the corresponding input files. Runtime images install
from the lock files; regenerate the matching lock file whenever an input
requirement changes.

The ML image uses the dedicated CPU lock
`requirements/lock/ml.txt` and defaults to the repository's RandomForest
training path. XGBoost remains available in the full development lock, but is
not included in the production training image because its Linux wheel is very
large and may pull GPU runtime packages.

Database schema changes are applied by Alembic. The API image runs
`scripts/migrate_db.py` before Uvicorn starts. Local operators can run:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://mt5_app:<password>@localhost:5432/mt5_data"
py -3.12 scripts/migrate_db.py
```

Back up PostgreSQL with
`scripts\backup_db.ps1 -OutputFile backups\mt5.dump`. Restore is destructive
and requires explicit confirmation:
`scripts\restore_db.ps1 -InputFile backups\mt5.dump -Confirm`.

The operational retention defaults are 90 days for order audit records, broker
responses, operational logs, and database backup artifacts. Set
`ORDER_AUDIT_RETENTION_DAYS`, `BROKER_RESPONSE_RETENTION_DAYS`,
`LOG_RETENTION_DAYS`, and `BACKUP_RETENTION_DAYS` in the deployment secret
store or `.env`; do not place credentials in these settings. Backup cleanup is
opt-in and limited to `*.dump` files in the managed backup directory:

```powershell
scripts\backup_db.ps1 -OutputFile backups\mt5.dump `
  -RetentionDays 90 -ApplyRetention
```

Before accepting a staging recovery drill, run the fail-closed validator
against a disposable database. It requires a fresh backup, explicit restore
confirmation, schema verification, and measures the recovery duration:

```powershell
$env:PGPASSWORD = "<staging-secret>"
scripts\validate_recovery.ps1 -BackupFile backups\mt5.dump `
  -ConfirmRestore
```

The validator's local result is evidence for the staging environment only.
It must not be presented as production RPO/RTO evidence without the managed
backup schedule, WAL/archive policy, encrypted storage, and an approved
restore target.

Keep the Docker dashboard in its default `MT5_ENABLED=false` mode. Docker
cannot connect directly to a Windows MT5 terminal; use the host collector/API
boundary instead.

### Optional controlled demo mode

The dashboard profile is the only supported operator surface for the local
demo workflow, but enabling the profile does not enable trading. Configure the
required safety values explicitly and review them before starting:

```powershell
$env:MT5_DEMO_ENABLED = "true"
$env:MT5_AUTO_TRADING_ENABLED = "true"
$env:MT5_LEGACY_ORDER_PATH_ENABLED = "false"
$env:MT5_LIVE_SYMBOLS = "XAUUSD"
$env:MT5_LIVE_MAGIC = "26090901"
$env:MT5_MAX_POSITION_VOLUME = "0.10"
$env:MT5_MAX_DAILY_LOSS = "100"
docker compose --profile dashboard up -d
```

The EA must still be attached to a demo account with
`InpAllowLiveTrading=false` until the operator has completed the separate
activation checklist. The Python and MQL5 defaults are defined in
[policy.py](../../src/python/execution/policy.py) and
[ExecutionPolicy.mqh](../../src/mql5/ExecutionPolicy.mqh); keep these values
aligned.

## Start the API and database

```powershell
docker compose up -d
docker compose ps
docker compose logs -f api
```

API health is available at <http://localhost:8000/health>. The Compose API
healthcheck validates this endpoint, while the database healthcheck uses
`pg_isready`.

## Train a model

```powershell
docker compose --profile training run --rm ml-training `
  python scripts/train_model.py --source database --symbol XAUUSD --timeframe M5
```

Training output is mounted from `models/`; backtest output is mounted from
`backtest_results/`.

## Stop and reset

```powershell
docker compose down
docker compose down -v  # destructive: removes the TimescaleDB volume
```

Use `down -v` only for disposable local data. A stateful database major
upgrade is not equivalent to changing an image tag; production upgrades
require a tested backup, compatibility/FCV procedure, and rollback image.

## Runtime policy

All checked-in Python Docker images and CI use Python 3.12. The repository
pins this choice in `.python-version`; dependency lock headers are generated
with the same interpreter. Dependency versions come from `requirements/full.in`.
CI validates the Python sources, tests, and the
Dashboard Compose profile with non-secret placeholders. The checked-in
requirements file is the dependency source of truth until a platform-specific
lock process is introduced; production deployment must pin and review the
resolved transitive set before release.
