$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$startupScript = Join-Path $projectRoot "scripts\start_local_demo.ps1"
$taskName = "SmartMT5 Local Demo ReadOnly"
$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$startupScript`""

if (-not (Get-Command schtasks.exe -ErrorAction SilentlyContinue)) {
    throw "schtasks.exe was not found."
}

& schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONLOGON /RL LIMITED /F
if ($LASTEXITCODE -eq 0) {
    Write-Output "Registered '$taskName' to start at user logon."
    Write-Output "Trading remains disabled: MT5_AUTO_TRADING_ENABLED=false and MT5_LEGACY_ORDER_PATH_ENABLED=false."
    exit 0
}

if ($LASTEXITCODE -ne 1) {
    throw "Failed to register scheduled task with exit code $LASTEXITCODE"
}

$startupFolder = [Environment]::GetFolderPath("Startup")
if ([string]::IsNullOrWhiteSpace($startupFolder)) {
    throw "The current user's Startup folder could not be resolved."
}

$shortcutPath = Join-Path $startupFolder "SmartMT5 Local Demo ReadOnly.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startupScript`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Starts the Smart MT5 local Demo dashboard in read-only mode."
$shortcut.Save()

Write-Output "Task Scheduler registration was denied; registered a current-user Startup shortcut instead."
Write-Output "Startup shortcut: $shortcutPath"
Write-Output "Trading remains disabled: MT5_AUTO_TRADING_ENABLED=false and MT5_LEGACY_ORDER_PATH_ENABLED=false."
