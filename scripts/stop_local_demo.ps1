$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$dashboardPidFile = Join-Path $projectRoot "logs\dashboard_windows.pid"
$watchdogPidFile = Join-Path $projectRoot "logs\dashboard_watchdog_windows.pid"
$marketDataPidFile = Join-Path $projectRoot "logs\market_watch_windows.pid"
$marketDataLockFile = Join-Path $projectRoot "market_data\.market_watch.lock"
$taskName = "SmartMT5 Local Demo"

try {
    & schtasks.exe /End /TN $taskName 2>$null
} catch {
    # The scheduled task is optional; continue stopping local processes.
}

if (Test-Path -LiteralPath $dashboardPidFile) {
    $dashboardPid = Get-Content -LiteralPath $dashboardPidFile -Raw
    if ($dashboardPid -match "^\d+$") {
        Stop-Process -Id ([int]$dashboardPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $dashboardPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $watchdogPidFile) {
    $watchdogPid = Get-Content -LiteralPath $watchdogPidFile -Raw
    if ($watchdogPid -match "^\d+$") {
        Stop-Process -Id ([int]$watchdogPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $watchdogPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $marketDataPidFile) {
    $marketDataPid = Get-Content -LiteralPath $marketDataPidFile -Raw
    if ($marketDataPid -match "^\d+$") {
        Stop-Process -Id ([int]$marketDataPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $marketDataPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $marketDataLockFile) {
    Remove-Item -LiteralPath $marketDataLockFile -Force -ErrorAction SilentlyContinue
}

Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Set-Location -LiteralPath $projectRoot
docker compose stop timescaledb api prometheus alertmanager grafana
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose stop failed with exit code $LASTEXITCODE"
}
