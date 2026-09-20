$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$watchdogScript = Join-Path $projectRoot "scripts\run_dashboard_watchdog.ps1"
$taskName = "SmartMT5 Local Demo"
$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`""

if (-not (Get-Command schtasks.exe -ErrorAction SilentlyContinue)) {
    throw "schtasks.exe was not found."
}

& schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONLOGON /RL LIMITED /F
if ($LASTEXITCODE -eq 0) {
    Write-Output "Registered '$taskName' to start at user logon."
    Write-Output "Demo auto-trading gate enabled; legacy order path remains disabled."
    exit 0
}

if ($LASTEXITCODE -ne 1) {
    throw "Failed to register scheduled task with exit code $LASTEXITCODE"
}

$startupFolder = [Environment]::GetFolderPath("Startup")
if ([string]::IsNullOrWhiteSpace($startupFolder)) {
    throw "The current user's Startup folder could not be resolved."
}

$shortcutPath = Join-Path $startupFolder "SmartMT5 Local Demo.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Starts the Smart MT5 local Demo dashboard with safety-gated auto-trading."
$shortcut.Save()

Write-Output "Task Scheduler registration was denied; registered a current-user Startup shortcut instead."
Write-Output "Startup shortcut: $shortcutPath"
Write-Output "Demo auto-trading gate enabled; legacy order path remains disabled."
