# Local Demo Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Start the Windows-local Demo staging stack, validate its health, and expose the dashboard without enabling Live Trading or receiving credentials.

**Architecture:** Use the existing Docker Compose topology for TimescaleDB, API, Prometheus, Alertmanager, Grafana, and the optional Streamlit dashboard. Keep MT5 outside Docker on Windows, and connect it only after local validation and operator approval.

**Tech Stack:** Windows PowerShell, Docker Desktop, Docker Compose, PostgreSQL 15/TimescaleDB, Python 3.12, FastAPI, Streamlit, MetaTrader 5 Demo, ZeroMQ.

**Spec:** `docs/superpowers/specs/2026-09-19-demo-local-staging-design.md`

## Global Constraints

- Demo only; no Live account and no Live order submission.
- `InpAllowLiveTrading=false` is mandatory.
- `MT5_AUTO_TRADING_ENABLED=false` remains the default until an explicitly approved, operator-watched Demo canary.
- Credentials are entered by the operator and never sent in chat, committed, logged, or included in evidence.
- Bind dashboard and observability UIs to localhost only.
- Unknown or timed-out broker outcomes remain unresolved until reconciliation.
- Stop on symbol mismatch, unexpected order, duplicate submission, missing protective level, failed health check, or protocol error.

---

### Task 1: Validate the local workstation

**Files:**
- Read: `docker-compose.yml`
- Read: `.env.example`
- Read: `ops/mt5/litefinance-demo-runbook.md`

**Interfaces:**
- Consumes: Windows PowerShell, Docker Desktop, MetaTrader 5 installation.
- Produces: A redacted workstation readiness result; no code or secrets are changed.

- [ ] **Step 1: Check required tools and ports**

Run:

```powershell
docker version
docker compose version
py -3 --version
Get-Process -Name terminal64 -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 5432,8000,8501,9090,9093,3000 -ErrorAction SilentlyContinue
```

Expected: Docker responds, Python reports 3.12.x, and any occupied ports are recorded before startup.

- [ ] **Step 2: Validate the Compose file without printing secrets**

Run:

```powershell
docker compose --profile dashboard config --quiet
```

Expected: exit code 0. If it fails because required `.env` values are missing, stop and populate only the local `.env` interactively; never print it.

- [ ] **Step 3: Confirm safety defaults**

Run:

```powershell
Select-String -Path docker-compose.yml -Pattern "MT5_AUTO_TRADING_ENABLED|MT5_LEGACY_ORDER_PATH_ENABLED|MT5_DEMO_ENABLED|MT5_DASHBOARD_DIRECT"
```

Expected: defaults are fail-closed (`false`) and no Live flag is introduced.

- [ ] **Step 4: Stop if a prerequisite is missing**

Do not start services if Docker is unavailable, a required port conflicts with an unrelated service, or MT5 is not available for the later Demo phase.

### Task 2: Start database and API staging services

**Files:**
- Modify only local ignored file: `.env`
- Read: `docker-compose.yml`

**Interfaces:**
- Consumes: Task 1 readiness result.
- Produces: Running `timescaledb` and `api` containers with local-only access.

- [ ] **Step 1: Ensure local required variables exist without exposing values**

Required names:

```text
POSTGRES_PASSWORD
API_AUTH_TOKEN
GRAFANA_ADMIN_PASSWORD
```

Use locally generated values or a local secret manager. Do not paste values into chat or commit them.

- [ ] **Step 2: Start only database and API**

Run:

```powershell
docker compose up -d timescaledb api
docker compose ps timescaledb api
```

Expected: both services become healthy or running; otherwise collect redacted logs and stop.

- [ ] **Step 3: Run migrations**

Run:

```powershell
docker compose exec api python -m alembic upgrade head
```

Expected: migration head reaches `0010` without downgrade or destructive reset.

- [ ] **Step 4: Verify API health**

Run:

```powershell
Invoke-WebRequest http://localhost:8000/health
docker compose logs --tail 100 api
```

Expected: HTTP health succeeds and logs contain no credential values.

### Task 3: Start observability and dashboard

**Files:**
- Read: `ops/prometheus/prometheus.yml`
- Read: `ops/alertmanager/alertmanager.yml`
- Read: `ops/grafana/provisioning/`
- Read: `ops/grafana/dashboards/`

**Interfaces:**
- Consumes: Healthy API and database from Task 2.
- Produces: Local dashboard at `http://localhost:8501`, Grafana at `http://localhost:3000`, Prometheus at `http://localhost:9090`, and Alertmanager at `http://localhost:9093`.

- [ ] **Step 1: Start observability and dashboard**

Run:

```powershell
docker compose --profile dashboard up -d prometheus alertmanager grafana dashboard
docker compose ps
```

Expected: all services are running and dashboard health becomes healthy.

- [ ] **Step 2: Verify dashboard health**

Run:

```powershell
Invoke-WebRequest http://localhost:8501/_stcore/health
Invoke-WebRequest http://localhost:3000/api/health
Invoke-WebRequest http://localhost:9090/-/healthy
Invoke-WebRequest http://localhost:9093/-/healthy
```

Expected: each endpoint returns success. Open the dashboard only on localhost.

- [ ] **Step 3: Open the dashboard for operator testing**

Open:

```text
http://localhost:8501
```

Expected: the dashboard loads an explicit connected or error state; it must not present a success-shaped fallback when the API is unavailable.

### Task 4: Run local safety and contract gates

**Files:**
- Read: `tests/e2e/test_litefinance_demo_contract.py`
- Read: `tests/python/execution/`
- Read: `tests/python/api/`

**Interfaces:**
- Consumes: Running local stack from Tasks 2-3.
- Produces: Redacted test output and a pass/fail gate for Demo preparation.

- [ ] **Step 1: Run offline Demo contract tests**

Run:

```powershell
py -3 -m pytest tests\e2e\test_litefinance_demo_contract.py -q --import-mode=importlib
```

Expected: PASS. This validates the local contract only, not broker behavior.

- [ ] **Step 2: Run the full quality gates**

Run:

```powershell
py -3 -m pytest -q --import-mode=importlib
py -3 -m ruff check src tests
py -3 -m ruff format --check src tests
py -3 -m mypy --follow-imports=skip src\python
```

Expected: all configured gates pass. Existing skipped PostgreSQL tests remain explicitly recorded if staging test credentials are not configured.

- [ ] **Step 3: Stop on any failed gate**

Do not connect MT5 or enable ZMQ if any gate fails. Preserve the error output without secrets and fix the root cause before continuing.

### Task 5: Prepare MT5 Demo without order submission

**Files:**
- Read: `ops/mt5/litefinance-demo-runbook.md`
- Read: `ops/mt5/SmartTraderEA.demo.set`

**Interfaces:**
- Consumes: Passed Tasks 1-4.
- Produces: Operator-verified Demo terminal, strict EA compile result, and exact Market Watch symbol list.

- [ ] **Step 1: Operator logs into LiteFinance Demo**

The operator enters credentials directly in MT5. Do not share them in chat, screenshots, logs, or files.

- [ ] **Step 2: Record exact Market Watch symbols**

Use only symbols visible in Market Watch. Do not assume `XAUUSD` or a suffix. Remove unapproved symbols from scope.

- [ ] **Step 3: Compile EA in strict mode**

Compile in MetaEditor and stop on any warning or error.

- [ ] **Step 4: Verify safe EA inputs**

Required values:

```text
InpAllowLiveTrading=false
InpEnableZmq=false
InpMaxLotSize=0.01
InpDailyLossLimitAccount=100.0
InpMagicNumber=26090901
```

Expected: no automated order can be sent during this preparation task.

### Task 6: Run operator-watched Demo canary

**Files:**
- Read: `ops/mt5/litefinance-demo-runbook.md`
- Read: `src/python/execution/live_order_workflow.py`
- Read: `src/python/execution/broker_reconciliation.py`

**Interfaces:**
- Consumes: Tasks 1-5 and explicit owner plus second-approver authorization.
- Produces: One minimal Demo result, reconciliation evidence, and a stopped/rolled-back environment.

- [ ] **Step 1: Obtain explicit Demo activation approval**

Approval must include owner approval, independent second approval, exact symbol scope, maximum volume, daily loss limit, expiry, and configuration hash. No approval means no canary.

- [ ] **Step 2: Enable only the Demo seam**

Set `InpEnableZmq=true` only for the approved local Demo window while keeping `InpAllowLiveTrading=false`. Keep an operator watching MT5 and the dashboard.

- [ ] **Step 3: Exercise non-order scenarios first**

Run heartbeat, read-only data, invalid symbol, oversized volume, invalid magic number, and circuit-breaker scenarios. Expected result: safe rejection without broker order creation.

- [ ] **Step 4: Submit one minimal Demo order**

Use the smallest permitted Demo volume, approved symbol, protective levels, correlation ID, and idempotency key. Do not retry automatically.

- [ ] **Step 5: Reconcile and verify audit**

Match client request, broker ticket, symbol, side, volume, protective levels, order state, and timestamps. A timeout or unknown result stays unresolved until read-only broker history confirms the outcome.

- [ ] **Step 6: Stop and rollback**

Disable ZMQ, keep Live trading false, stop the EA, and preserve redacted evidence. Stop immediately on any unexpected order, duplicate, symbol mismatch, missing protective level, or uncertain account state.

### Task 7: Final local handoff

**Files:**
- Read: `docs/superpowers/specs/2026-09-19-demo-local-staging-design.md`
- Read: `ops/mt5/litefinance-demo-runbook.md`

**Interfaces:**
- Consumes: All prior task evidence.
- Produces: A user-facing local URL and a clear Demo readiness status.

- [ ] **Step 1: Verify services remain healthy**

Run:

```powershell
docker compose ps
Invoke-WebRequest http://localhost:8501/_stcore/health
Invoke-WebRequest http://localhost:8000/health
```

- [ ] **Step 2: Confirm no secret or generated artifact was added to the repository**

Inspect only tracked/status metadata and known task paths; do not print `.env`.

- [ ] **Step 3: Report the result**

Report dashboard URL, service status, tests, and any blocked gates. Never report credentials, account numbers, or unredacted broker evidence.
