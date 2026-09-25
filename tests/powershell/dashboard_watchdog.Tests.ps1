$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$watchdogPath = Join-Path $projectRoot "scripts\run_dashboard_watchdog.ps1"
$startupPath = Join-Path $projectRoot "scripts\start_local_demo.ps1"

Describe "Dashboard watchdog scripts" {
    It "has valid PowerShell syntax" {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $watchdogPath,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null

        $parseErrors | Should BeNullOrEmpty
    }

    It "checks both the process and Streamlit health endpoint" {
        $watchdog = Get-Content -LiteralPath $watchdogPath -Raw

        $watchdog | Should Match "Get-DashboardProcess"
        $watchdog | Should Match "Test-DashboardHealthy"
        $watchdog | Should Match "_stcore/health"
        $watchdog | Should Match "MaxConsecutiveRestarts"
        $watchdog | Should Match "Get-NetTCPConnection"
        $watchdog | Should Match "Select-Object -Unique"
    }

    It "uses the health endpoint as the authoritative liveness signal" {
        $watchdog = Get-Content -LiteralPath $watchdogPath -Raw
        $healthFunction = [regex]::Match(
            $watchdog,
            '(?s)function Test-DashboardHealthy \{.*?\n\}'
        ).Value

        $healthFunction | Should Match "Invoke-WebRequest"
        $healthFunction | Should Not Match "Get-DashboardProcess"
        $watchdog | Should Match "_stcore/health"
    }

    It "keeps retrying after the restart threshold instead of exiting permanently" {
        $watchdog = Get-Content -LiteralPath $watchdogPath -Raw

        $watchdog | Should Match "Restart threshold reached"
        $watchdog | Should Match "Start-Sleep"
        $watchdog | Should Not Match "Restart limit reached; watchdog stopped"
    }

    It "does not run the unstable AppTest suite during startup" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Not Match "Invoke-DashboardSmokeTest"
        $startup | Should Not Match "tests/python/dashboard/test_dashboard_smoke.py"
    }

    It "starts the watchdog with the dashboard and prevents recursive watchdog startup" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match "Start-DashboardWatchdog"
        $startup | Should Match "SkipDashboardWatchdog"
        $watchdog = Get-Content -LiteralPath $watchdogPath -Raw
        $watchdog | Should Match "-SkipDashboardWatchdog"
        $watchdog | Should Match "Start-Process"
        $watchdog | Should Not Match "-Wait"
    }

    It "serializes watchdog instances and suppresses browser launch during recovery" {
        $watchdog = Get-Content -LiteralPath $watchdogPath -Raw
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $watchdog | Should Match "Global\\SmartMT5DashboardWatchdog"
        $watchdog | Should Match "-SkipDashboardWatchdog"
        $startup | Should Not Match 'Start-Process "http://127\.0\.0\.1:8501"'
        $startup | Should Match 'browser was not opened automatically'
    }

    It "does not open a new browser tab when an existing dashboard is healthy" {
        $startup = Get-Content -LiteralPath $startupPath -Raw
        $healthyPath = [regex]::Match(
            $startup,
            '(?s)\$existingConnection.*?if \(-not \$dashboardReady\)'
        ).Value

        $healthyPath | Should Not Match 'Start-Process "http://127\.0\.0\.1:8501"'
    }
}
