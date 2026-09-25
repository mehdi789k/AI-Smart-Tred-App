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
        $startup | Should Match '(?s)TradingMode -eq "Live".*?\$env:MT5_AUTO_TRADING_ENABLED\s*=\s*"true"'
    }

    It "enables the selected mode while keeping legacy routing disabled" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match 'MT5_LEGACY_ORDER_PATH_ENABLED\s*=\s*"false"'
        $startup | Should Match 'MT5_LIVE_SYMBOLS'
        $startup | Should Match 'verify_demo_readiness.py'
        $startup | Should Match '\$env:MT5_AUTO_TRADING_ENABLED\s*=\s*"true"'
        $startup | Should Match 'TradingMode -eq "Demo"'
    }

    It "applies the bounded Demo profile without enabling Live mode" {
        $startup = Get-Content -LiteralPath $startupPath -Raw

        $startup | Should Match '\$env:MT5_ENABLED\s*=\s*"true"'
        $startup | Should Match '\$env:MT5_DEMO_ENABLED\s*=\s*"true"'
        $startup | Should Match '\$env:MT5_AUTO_TRADING_ENABLED\s*=\s*"true"'
        $startup | Should Match '\$env:MT5_LEGACY_ORDER_PATH_ENABLED\s*=\s*"false"'
        $startup | Should Match '\$env:MT5_DEMO_MAX_TRADE_VOLUME\s*=\s*"0\.01"'
        $startup | Should Match '\$env:MT5_DEMO_MAX_TRADES_PER_SESSION\s*=\s*"3"'
        $startup | Should Match '\$env:MT5_DEMO_MAX_DAILY_LOSS\s*=\s*"10"'
        $startup | Should Match '\$env:MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION\s*=\s*"true"'
        $startup | Should Match '\$env:MT5_DEMO_AUTO_STOP_ON_ERROR\s*=\s*"true"'
        $startup | Should Match 'TradingMode -eq "Live"'
        $startup | Should Match 'MT5_DEMO_ENABLED\s*=\s*"false"'
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
