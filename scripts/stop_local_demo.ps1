$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$dashboardPidFile = Join-Path $projectRoot "logs\dashboard_windows.pid"
$taskName = "SmartMT5 Local Demo ReadOnly"

& schtasks.exe /End /TN $taskName 2>$null

if (Test-Path -LiteralPath $dashboardPidFile) {
    $dashboardPid = Get-Content -LiteralPath $dashboardPidFile -Raw
    if ($dashboardPid -match "^\d+$") {
        Stop-Process -Id ([int]$dashboardPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $dashboardPidFile -Force -ErrorAction SilentlyContinue
}

Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Set-Location -LiteralPath $projectRoot
docker compose stop timescaledb api prometheus alertmanager grafana
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose stop failed with exit code $LASTEXITCODE"
}
