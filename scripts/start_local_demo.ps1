param(
    [ValidateSet("Demo", "Live")]
    [string]$TradingMode = "Demo",
    [switch]$ConfirmLiveTrading,
    [switch]$SkipDashboardWatchdog
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\Mehdi-karimiyan\AppData\Local\Programs\Python\Python312\python.exe"
$logDirectory = Join-Path $projectRoot "logs"
$startupLog = Join-Path $logDirectory "local_demo_startup.log"
$dashboardPidFile = Join-Path $logDirectory "dashboard_windows.pid"
$watchdogPidFile = Join-Path $logDirectory "dashboard_watchdog_windows.pid"
$marketDataPidFile = Join-Path $logDirectory "market_watch_windows.pid"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Write-StartupLog {
    param([string]$Message)

    Add-Content -Path $startupLog -Value "$(Get-Date -Format o) $Message"
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

function Start-DashboardWatchdog {
    if ($SkipDashboardWatchdog) {
        return
    }

    if (Test-Path -LiteralPath $watchdogPidFile) {
        $watchdogPidText = (Get-Content -LiteralPath $watchdogPidFile -Raw).Trim()
        $watchdogPid = 0
        if (
            [int]::TryParse($watchdogPidText, [ref]$watchdogPid) -and
            (Get-Process -Id $watchdogPid -ErrorAction SilentlyContinue)
        ) {
            return
        }
        Remove-Item -LiteralPath $watchdogPidFile -Force -ErrorAction SilentlyContinue
    }

    $watchdogScript = Join-Path $projectRoot "scripts\run_dashboard_watchdog.ps1"
    $watchdogLog = Join-Path $logDirectory "dashboard_watchdog_windows.out.log"
    $watchdogErrorLog = Join-Path $logDirectory "dashboard_watchdog_windows.err.log"
    $watchdogArguments = (
        "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`""
    )
    $watchdog = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $watchdogArguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $watchdogLog `
        -RedirectStandardError $watchdogErrorLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $watchdogPidFile -Value $watchdog.Id
    Write-StartupLog "Dashboard watchdog started independently with PID $($watchdog.Id)."
}

if ($TradingMode -eq "Live") {
    if (-not $ConfirmLiveTrading) {
        throw "Live mode requires -ConfirmLiveTrading. No trading services were started."
    }

    if ($env:LIVE_TRADING_CONFIRMATION -ne "I_UNDERSTAND_LIVE_TRADING_RISK") {
        throw "Live mode requires LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_LIVE_TRADING_RISK."
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python 3.12 was not found at $python"
}

Set-Location -LiteralPath $projectRoot

$envFile = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envFile) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        if ($_ -match '^\s*(MT5_LOGIN|MT5_PASSWORD|MT5_SERVER|MT5_TERMINAL_PATH|MT5_LIVE_SYMBOLS)\s*=(.*)$') {
            $value = $matches[2].Trim()
            if (
                $value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'")))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable(
                $matches[1],
                $value,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
}

Write-StartupLog "Starting local $TradingMode infrastructure."

# Apply the non-secret host-process profile after loading credentials. Live
# remains available only for explicitly confirmed, manually operated startup.
$env:MT5_LEGACY_ORDER_PATH_ENABLED = "false"
if ($TradingMode -eq "Demo") {
    $env:MT5_ENABLED = "true"
    $env:MT5_DEMO_ENABLED = "true"
    $env:MT5_AUTO_TRADING_ENABLED = "true"
    $env:MT5_DEMO_MAX_TRADE_VOLUME = "0.01"
    $env:MT5_DEMO_MAX_TRADES_PER_SESSION = "3"
    $env:MT5_DEMO_MAX_DAILY_LOSS = "10"
    $env:MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION = "true"
    $env:MT5_DEMO_AUTO_STOP_ON_ERROR = "true"
} else {
    $env:MT5_ENABLED = "true"
    $env:MT5_DEMO_ENABLED = "false"
    $env:MT5_AUTO_TRADING_ENABLED = "true"
}
$env:MT5_DASHBOARD_DIRECT = "true"
Write-StartupLog (
    "Trading mode=$TradingMode; MT5 enabled=$($env:MT5_ENABLED); " +
    "auto trading=$($env:MT5_AUTO_TRADING_ENABLED); demo=$($env:MT5_DEMO_ENABLED); " +
    "legacy order path=$($env:MT5_LEGACY_ORDER_PATH_ENABLED); " +
    "demo limits volume=$($env:MT5_DEMO_MAX_TRADE_VOLUME), " +
    "trades/session=$($env:MT5_DEMO_MAX_TRADES_PER_SESSION), " +
    "daily loss=$($env:MT5_DEMO_MAX_DAILY_LOSS), " +
    "manual confirmation=$($env:MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION), " +
    "stop on error=$($env:MT5_DEMO_AUTO_STOP_ON_ERROR)."
)
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

foreach ($requiredVariable in @("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_TERMINAL_PATH")) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($requiredVariable))) {
        throw "$requiredVariable is required in .env or the process environment."
    }
}

$env:STREAMLIT_SERVER_HEADLESS = "true"

function Invoke-TradingReadinessValidation {
    if ($TradingMode -eq "Live") {
        $readinessArguments = @(
            "scripts\verify_live_readiness.py",
            "--base-url", "http://127.0.0.1:8000",
            "--timeout", "5"
        )
    } else {
        $readinessArguments = @(
            "scripts\verify_demo_readiness.py",
            "--base-url", "http://127.0.0.1:8000",
            "--timeout", "5",
            "--demo-symbol", "XAUUSD",
            "--max-daily-loss", "10"
        )
    }
    if ($env:MT5_DASHBOARD_DIRECT -eq "true") {
        $readinessArguments += "--allow-direct-dashboard"
    }
    & $python $readinessArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$TradingMode readiness validation failed with exit code $LASTEXITCODE."
    }
}

$existingConnection = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($existingConnection) {
    try {
        $existingHealth = Invoke-WebRequest -UseBasicParsing `
            -Uri "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 3
        if ($existingHealth.StatusCode -eq 200) {
            $existingPid = (Get-NetTCPConnection -LocalPort 8501 -State Listen |
                Select-Object -First 1 -ExpandProperty OwningProcess)
            Set-Content -Path $dashboardPidFile -Value $existingPid
            Invoke-TradingReadinessValidation
            Start-MarketDataCollector
            Start-DashboardWatchdog
            Write-StartupLog "Dashboard is already healthy; health check passed; browser was not reopened."
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

Invoke-TradingReadinessValidation
Start-MarketDataCollector
Start-DashboardWatchdog
Write-StartupLog (
    "Local $TradingMode infrastructure and dashboard are healthy; " +
    "health check passed; browser was not opened automatically."
)
exit 0
