param(
    [int]$IntervalSeconds = 15,
    [int]$StartupTimeoutSeconds = 90,
    [int]$MaxConsecutiveRestarts = 3,
    [switch]$Once
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$startupScript = Join-Path $projectRoot "scripts\start_local_demo.ps1"
$logDirectory = Join-Path $projectRoot "logs"
$watchdogLog = Join-Path $logDirectory "dashboard_watchdog.log"
$dashboardPidFile = Join-Path $logDirectory "dashboard_windows.pid"
$healthUri = "http://127.0.0.1:8501/_stcore/health"

if ($IntervalSeconds -lt 5) {
    throw "IntervalSeconds must be at least 5."
}
if ($StartupTimeoutSeconds -lt 10) {
    throw "StartupTimeoutSeconds must be at least 10."
}
if ($MaxConsecutiveRestarts -lt 1) {
    throw "MaxConsecutiveRestarts must be positive."
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Write-WatchdogLog {
    param([string]$Message)

    Add-Content -Path $watchdogLog -Value "$(Get-Date -Format o) $Message"
}

function Get-DashboardProcess {
    if (-not (Test-Path -LiteralPath $dashboardPidFile)) {
        return $null
    }

    $pidText = (Get-Content -LiteralPath $dashboardPidFile -Raw).Trim()
    $dashboardPid = 0
    if (-not [int]::TryParse($pidText, [ref]$dashboardPid)) {
        Write-WatchdogLog "Ignoring malformed dashboard PID file."
        return $null
    }

    $process = Get-Process -Id $dashboardPid -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }

    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $dashboardPid" `
        -ErrorAction SilentlyContinue).CommandLine
    if ($commandLine -notmatch "streamlit|dashboard[\\/]app\.py") {
        Write-WatchdogLog "PID $dashboardPid is not the expected Dashboard process."
        return $null
    }

    return $process
}

function Test-DashboardHealthy {
    $process = Get-DashboardProcess
    if (-not $process) {
        return $false
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUri -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-DashboardRecovery {
    Write-WatchdogLog "Dashboard unhealthy; invoking controlled startup."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startupScript
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard startup failed with exit code $LASTEXITCODE"
    }
}

Write-WatchdogLog "Dashboard watchdog started."
$consecutiveRestarts = 0
while ($true) {
    if (Test-DashboardHealthy) {
        if ($consecutiveRestarts -gt 0) {
            Write-WatchdogLog "Dashboard recovered and health check is passing."
        }
        $consecutiveRestarts = 0
        if ($Once) {
            exit 0
        }
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $consecutiveRestarts++
    if ($consecutiveRestarts -gt $MaxConsecutiveRestarts) {
        Write-WatchdogLog "Restart limit reached; watchdog stopped fail-closed."
        exit 1
    }

    try {
        Start-DashboardRecovery
        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        do {
            Start-Sleep -Seconds 2
        } until ((Test-DashboardHealthy) -or ((Get-Date) -ge $deadline))

        if (-not (Test-DashboardHealthy)) {
            Write-WatchdogLog "Dashboard did not become healthy before timeout."
        }
        if ($Once) {
            exit [int](-not (Test-DashboardHealthy))
        }
    } catch {
        Write-WatchdogLog "Dashboard recovery failed: $($_.Exception.Message)"
        if ($Once) {
            exit 1
        }
    }
}
