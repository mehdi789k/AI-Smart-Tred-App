param(
    [string]$TaskName = "SmartMT5Collector",
    [double]$TimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"
$healthCommand = "py -3.12 -m src.python.data.main --health --watchdog --watchdog-timeout $TimeoutSeconds"

try {
    & $env:ComSpec /c $healthCommand *> $null
    if ($LASTEXITCODE -eq 0) {
        exit 0
    }
}
catch {
    # A missing or malformed health file is a failed health check.
}

Start-ScheduledTask -TaskName $TaskName
exit 1
