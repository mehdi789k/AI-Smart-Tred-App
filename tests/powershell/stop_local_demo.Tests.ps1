$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$shutdownPath = Join-Path $projectRoot "scripts\stop_local_demo.ps1"

Describe "Local trading shutdown safety" {
    It "tracks and stops the Market Watch collector by PID" {
        $shutdown = Get-Content -LiteralPath $shutdownPath -Raw

        $shutdown | Should Match 'market_watch_windows\.pid'
        $shutdown | Should Match 'Stop-Process -Id'
        $shutdown | Should Match 'market_data\\\.market_watch\.lock'
    }

    It "has valid PowerShell syntax" {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $shutdownPath,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null

        $parseErrors | Should BeNullOrEmpty
    }
}
