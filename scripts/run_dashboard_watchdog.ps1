# Backward-compatible entry point. The local Demo watcher is fail-closed.
$safeStartup = Join-Path $PSScriptRoot "start_local_demo.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $safeStartup
exit $LASTEXITCODE
