param(
    [Parameter(Mandatory = $true)]
    [string]$OutputFile,
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 90,
    [switch]$ApplyRetention
)

$ErrorActionPreference = "Stop"
if (-not $env:PGHOST) { $env:PGHOST = "localhost" }
if (-not $env:PGPORT) { $env:PGPORT = "5432" }
if (-not $env:PGDATABASE) { $env:PGDATABASE = "mt5_data" }
if (-not $env:PGUSER) { $env:PGUSER = "mt5_app" }
if (-not $env:PGPASSWORD) { throw "PGPASSWORD must be provided through the environment, never as a command argument." }

$parent = Split-Path -Parent $OutputFile
if (-not $parent) { $parent = (Get-Location).Path }
if ($parent -and -not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}
& pg_dump `
    --format=custom `
    --file=$OutputFile `
    --no-owner `
    --no-privileges `
    --exclude-schema=_timescaledb_catalog `
    --exclude-schema=_timescaledb_internal `
    --exclude-schema=_timescaledb_config
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE" }

if ($ApplyRetention) {
    $cutoff = (Get-Date).ToUniversalTime().AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $parent -File -Filter "*.dump" |
        Where-Object {
            $_.FullName -ne (Resolve-Path -LiteralPath $OutputFile).Path -and
            $_.LastWriteTimeUtc -lt $cutoff
        } |
        Remove-Item -Force
}
