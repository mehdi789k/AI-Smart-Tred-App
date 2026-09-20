$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\Mehdi-karimiyan\AppData\Local\Programs\Python\Python312\python.exe"
$logDirectory = Join-Path $projectRoot "logs"
$startupLog = Join-Path $logDirectory "local_demo_startup.log"
$dashboardPidFile = Join-Path $logDirectory "dashboard_windows.pid"
$marketDataPidFile = Join-Path $logDirectory "market_watch_windows.pid"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Write-StartupLog {
    param([string]$Message)

    Add-Content -Path $startupLog -Value "$(Get-Date -Format o) $Message"
}

function Invoke-DashboardSmokeTest {
    Write-StartupLog "Running read-only dashboard smoke tests."
    & $python -m pytest `
        "tests/python/dashboard/test_dashboard_smoke.py" `
        "-q" `
        "--disable-warnings" 2>&1 |
        Tee-Object -FilePath (Join-Path $logDirectory "dashboard_smoke_test.log") -Append
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard smoke tests failed with exit code $LASTEXITCODE"
    }
    Write-StartupLog "Dashboard smoke tests passed."
}

function Start-MarketDataCollector {
    if ($env:MT5_DASHBOARD_DIRECT -eq "true") {
        Write-StartupLog "Skipping Market Watch collector because dashboard owns direct MT5 access."
        return
    }

    $lockPath = Join-Path $projectRoot "market_data\.market_watch.lock"
    if (Test-Path -LiteralPath $lockPath) {
        try {
            $ownerPid = [int](Get-Content -LiteralPath $lockPath -Raw).Trim()
            Get-Process -Id $ownerPid -ErrorAction Stop | Out-Null
            Set-Content -Path $marketDataPidFile -Value $ownerPid
            Write-StartupLog "Market Watch collector already running with PID $ownerPid."
            return
        } catch {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
        }

    }

    $collectorLog = Join-Path $logDirectory "market_watch_windows.out.log"
    $collectorErrorLog = Join-Path $logDirectory "market_watch_windows.err.log"
    $collector = Start-Process `
        -FilePath $python `
        -ArgumentList "src\python\mt5_account\mt5_market_watch.py" `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $collectorLog `
        -RedirectStandardError $collectorErrorLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $marketDataPidFile -Value $collector.Id
    Write-StartupLog "Market Watch collector started with PID $($collector.Id)."
}

function Stop-MarketDataCollectorForDirectDashboard {
    if ($env:MT5_DASHBOARD_DIRECT -ne "true") {
        return
    }

    $lockPath = Join-Path $projectRoot "market_data\.market_watch.lock"
    if (-not (Test-Path -LiteralPath $lockPath)) {
        return
    }

    try {
        $ownerPid = [int](Get-Content -LiteralPath $lockPath -Raw).Trim()
        $owner = Get-Process -Id $ownerPid -ErrorAction Stop
        Stop-Process -Id $owner.Id -Force -ErrorAction Stop
        Write-StartupLog "Stopped Market Watch collector PID $ownerPid for direct dashboard MT5 ownership."
    } catch {
        Write-StartupLog "Market Watch collector lock was stale or already stopped."
    } finally {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python 3.12 was not found at $python"
}

Set-Location -LiteralPath $projectRoot
Write-StartupLog "Starting local Demo infrastructure."

# Compose reads the parent process environment before the host dashboard
# settings below are applied. Force the local startup path to remain
# read-only even when a stale user-level environment override is present.
$env:MT5_AUTO_TRADING_ENABLED = "false"
$env:MT5_LEGACY_ORDER_PATH_ENABLED = "false"
$env:MT5_DEMO_ENABLED = "true"
$env:MT5_DASHBOARD_DIRECT = "true"
Stop-MarketDataCollectorForDirectDashboard

docker compose up -d timescaledb api prometheus alertmanager grafana
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed with exit code $LASTEXITCODE"
}

$apiReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $apiReady = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $apiReady) {
    throw "API health check did not become ready within 60 seconds."
}

$env:MT5_ENABLED = "true"
$env:STREAMLIT_SERVER_HEADLESS = "true"

$existingConnection = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($existingConnection) {
    try {
        $existingHealth = Invoke-WebRequest -UseBasicParsing `
            -Uri "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 3
        if ($existingHealth.StatusCode -eq 200) {
            $existingPid = (Get-NetTCPConnection -LocalPort 8501 -State Listen |
                Select-Object -First 1 -ExpandProperty OwningProcess)
            Set-Content -Path $dashboardPidFile -Value $existingPid
            Invoke-DashboardSmokeTest
            Start-MarketDataCollector
            Start-Process "http://127.0.0.1:8501"
            Write-StartupLog "Dashboard is already healthy; smoke test passed and browser opened."
            exit 0
        }
    } catch {
        throw "Port 8501 is occupied but dashboard health is unavailable."
    }
}

$dashboardLog = Join-Path $logDirectory "dashboard_windows.out.log"
$dashboardErrorLog = Join-Path $logDirectory "dashboard_windows.err.log"
$process = Start-Process `
    -FilePath $python `
    -ArgumentList "-m", "streamlit", "run", "src\python\dashboard\app.py",
        "--server.address=127.0.0.1", "--server.port=8501", "--server.headless=true",
        "--server.fileWatcherType=none", "--server.runOnSave=false" `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $dashboardLog `
    -RedirectStandardError $dashboardErrorLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -Path $dashboardPidFile -Value $process.Id
Write-StartupLog "Dashboard started independently with PID $($process.Id)."

$dashboardReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $dashboardReady = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $dashboardReady) {
    Write-StartupLog "Dashboard health check failed; stopping PID $($process.Id)."
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $dashboardPidFile -Force -ErrorAction SilentlyContinue
    throw "Dashboard health check did not become ready within 60 seconds."
}

Invoke-DashboardSmokeTest
Start-MarketDataCollector
Start-Process "http://127.0.0.1:8501"
Write-StartupLog "Local Demo infrastructure and read-only dashboard are healthy; browser opened."
exit 0
