param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [switch]$CreateBackup,
    [switch]$ConfirmRestore,
    [ValidateRange(1, 1440)]
    [int]$MaxRpoMinutes = 15,
    [ValidateRange(1, 1440)]
    [int]$MaxRtoMinutes = 60,
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 90
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backupScript = Join-Path $PSScriptRoot "backup_db.ps1"
$restoreScript = Join-Path $PSScriptRoot "restore_db.ps1"

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

if (-not $env:PGHOST) { $env:PGHOST = "localhost" }
if (-not $env:PGPORT) { $env:PGPORT = "5432" }
if (-not $env:PGDATABASE) { $env:PGDATABASE = "mt5_data" }
if (-not $env:PGUSER) { $env:PGUSER = "mt5_app" }
$resolvedBackup = [System.IO.Path]::GetFullPath($BackupFile)
if ($CreateBackup) {
    if (-not $env:PGPASSWORD) {
        throw "PGPASSWORD must be provided through the environment, never as a command argument."
    }
    & $backupScript -OutputFile $resolvedBackup -RetentionDays $RetentionDays -ApplyRetention
    if ($LASTEXITCODE -ne 0) { throw "Backup creation failed." }
}
if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) {
    throw "Backup file not found: $resolvedBackup"
}
if (-not $ConfirmRestore) {
    throw "Restore validation is destructive. Re-run with -ConfirmRestore against a disposable staging database."
}
if (-not $env:PGPASSWORD) {
    throw "PGPASSWORD must be provided through the environment, never as a command argument."
}

$backupAgeMinutes = ((Get-Date).ToUniversalTime() - (Get-Item -LiteralPath $resolvedBackup).LastWriteTimeUtc).TotalMinutes
if ($backupAgeMinutes -gt $MaxRpoMinutes) {
    throw ("Backup is {0:N1} minutes old; RPO limit is {1} minutes." -f $backupAgeMinutes, $MaxRpoMinutes)
}

$started = [System.Diagnostics.Stopwatch]::StartNew()
& $restoreScript -InputFile $resolvedBackup -Confirm
if ($LASTEXITCODE -ne 0) { throw "Restore failed." }

Invoke-CheckedNative -Command "pg_isready" -Arguments @("-h", $env:PGHOST, "-p", $env:PGPORT, "-d", $env:PGDATABASE, "-U", $env:PGUSER)
$schemaVersion = (& psql "--dbname=$env:PGDATABASE" "-Atqc" "SELECT version_num FROM alembic_version LIMIT 1").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($schemaVersion)) {
    throw "Post-restore schema verification failed."
}

$started.Stop()
$elapsedSeconds = $started.Elapsed.TotalSeconds
if ($elapsedSeconds -gt ($MaxRtoMinutes * 60)) {
    throw ("Recovery took {0:N1} seconds; RTO limit is {1} minutes." -f $elapsedSeconds, $MaxRtoMinutes)
}

[pscustomobject]@{
    backup_file = [System.IO.Path]::GetFileName($resolvedBackup)
    backup_age_minutes = [math]::Round($backupAgeMinutes, 2)
    schema_version = $schemaVersion
    recovery_seconds = [math]::Round($elapsedSeconds, 2)
    max_rpo_minutes = $MaxRpoMinutes
    max_rto_minutes = $MaxRtoMinutes
    retention_days = $RetentionDays
} | ConvertTo-Json -Compress
