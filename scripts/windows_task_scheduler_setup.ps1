# Registers a Windows Scheduled Task that starts Cosmic Punk (in WSL) at logon.
#
# Prerequisite: run scripts/autostart_setup.sh INSIDE WSL first — it creates
# ~/.cosmic_punk_startup.sh, which this task invokes.
#
# Usage (PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\windows_task_scheduler_setup.ps1
# Pass -WslDistro to target a non-default distro:
#   ... -File scripts\windows_task_scheduler_setup.ps1 -WslDistro Ubuntu-22.04

param(
    [string]$WslDistro = "Ubuntu",
    [string]$TaskName  = "CosmicPunkTrader"
)

$ErrorActionPreference = "Stop"

$argument = "-d $WslDistro -e bash -c ""bash ~/.cosmic_punk_startup.sh"""
$action   = New-ScheduledTaskAction -Execute "wsl.exe" -Argument $argument
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Starts the Cosmic Punk trading system in WSL at logon."

Write-Host "Registered scheduled task '$TaskName'."
Write-Host "It runs: wsl $argument"
Write-Host ""
Write-Host "If you haven't yet, run this inside WSL first:"
Write-Host "    bash scripts/autostart_setup.sh"
Write-Host "Remove the task later with:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
