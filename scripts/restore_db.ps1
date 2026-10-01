param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,
    [switch]$Confirm
)

$ErrorActionPreference = "Stop"
if (-not $Confirm) { throw "Restore is destructive. Re-run with -Confirm after verifying the backup." }
if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { throw "Backup file not found: $InputFile" }
if (-not $env:PGHOST) { $env:PGHOST = "localhost" }
if (-not $env:PGPORT) { $env:PGPORT = "5432" }
if (-not $env:PGDATABASE) { $env:PGDATABASE = "mt5_data" }
if (-not $env:PGUSER) { $env:PGUSER = "mt5_app" }
if (-not $env:PGPASSWORD) { throw "PGPASSWORD must be provided through the environment, never as a command argument." }

& pg_restore `
    --clean `
    --if-exists `
    --exit-on-error `
    --no-owner `
    --no-privileges `
    --dbname=$env:PGDATABASE `
    $InputFile
if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE" }
