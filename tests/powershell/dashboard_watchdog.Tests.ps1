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
    }

    It "does not run the unstable AppTest suite during startup" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Not Match "Invoke-DashboardSmokeTest"
        $startup | Should Not Match "tests/python/dashboard/test_dashboard_smoke.py"
    }
}
