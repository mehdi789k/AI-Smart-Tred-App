$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$startupPath = Join-Path $projectRoot "scripts\start_local_demo.ps1"

Describe "Local trading startup safety" {
    It "declares an explicit trading mode with Demo as the default" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match '\[ValidateSet\("Demo",\s*"Live"\)\]'
        $startup | Should Match '\$TradingMode\s*=\s*"Demo"'
    }

    It "requires an explicit live confirmation before enabling live trading" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match '\$ConfirmLiveTrading'
        $startup | Should Match 'TradingMode -eq "Live"'
        $startup | Should Match 'LIVE_TRADING_CONFIRMATION'
        $startup | Should Match 'MT5_DEMO_ENABLED\s*=\s*"false"'
    }

    It "enables the selected mode while keeping legacy routing disabled" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match 'MT5_LEGACY_ORDER_PATH_ENABLED\s*=\s*"false"'
        $startup | Should Match 'MT5_LIVE_SYMBOLS'
        $startup | Should Match 'verify_demo_readiness.py'
        $startup | Should Match '\$env:MT5_AUTO_TRADING_ENABLED\s*=\s*"true"'
        $startup | Should Match 'TradingMode -eq "Demo"'
    }

    It "has valid PowerShell syntax" {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $startupPath,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null

        $parseErrors | Should BeNullOrEmpty
    }
}
