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

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$watchdogMutex = New-Object System.Threading.Mutex($false, "Global\SmartMT5DashboardWatchdog")
try {
    if (-not $watchdogMutex.WaitOne(0)) {
        Add-Content -Path $watchdogLog -Value "$(Get-Date -Format o) Another dashboard watchdog is already running."
        exit 0
    }
} catch {
    $watchdogMutex.Dispose()
    throw "Unable to acquire dashboard watchdog single-instance lock: $($_.Exception.Message)"
}

if ($IntervalSeconds -lt 5) {
    throw "IntervalSeconds must be at least 5."
}
if ($StartupTimeoutSeconds -lt 10) {
    throw "StartupTimeoutSeconds must be at least 10."
}
if ($MaxConsecutiveRestarts -lt 1) {
    throw "MaxConsecutiveRestarts must be positive."
}

function Write-WatchdogLog {
    param([string]$Message)

    Add-Content -Path $watchdogLog -Value "$(Get-Date -Format o) $Message"
}

function Get-DashboardProcess {
    $candidatePids = @()
    if (Test-Path -LiteralPath $dashboardPidFile) {
        $pidText = (Get-Content -LiteralPath $dashboardPidFile -Raw).Trim()
        $dashboardPid = 0
        if ([int]::TryParse($pidText, [ref]$dashboardPid)) {
            $candidatePids += $dashboardPid
        } else {
            Write-WatchdogLog "Ignoring malformed dashboard PID file."
        }
    }

    $candidatePids += @(
        Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess
    )

    foreach ($candidatePid in ($candidatePids | Select-Object -Unique)) {
        $process = Get-Process -Id $candidatePid -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $candidatePid" `
            -ErrorAction SilentlyContinue).CommandLine
        if ($commandLine -match "streamlit|dashboard[\\/]app\.py") {
            Set-Content -Path $dashboardPidFile -Value $candidatePid
            return $process
        }
    }

    return $null
}

function Test-DashboardHealthy {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUri -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-DashboardRecovery {
    Write-WatchdogLog "Dashboard unhealthy; invoking controlled startup."
    $startupArguments = (
        "-NoProfile -ExecutionPolicy Bypass -File `"$startupScript`" " +
        "-SkipDashboardWatchdog"
    )
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $startupArguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru | Out-Null
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
        Write-WatchdogLog (
            "Restart threshold reached; continuing with controlled retry " +
            "after the next interval."
        )
        Start-Sleep -Seconds ([Math]::Min($IntervalSeconds * 4, 120))
        $consecutiveRestarts = 0
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
