#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Registers the daily AIOpHub BC -> Frontu batch sync in Windows Task Scheduler.
.DESCRIPTION
  Runs "<python.exe> -m src.cli sync --batch" daily under the SYSTEM account. A run that is
  still active when the next one is due is not doubled (MultipleInstances IgnoreNew). The sync
  mode follows SYNC_DRY_RUN in .env. Re-running this script updates the existing task.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File register_task.ps1 -PythonExe D:\python\python.exe
#>
param(
    [string]$PythonExe = (Join-Path (Split-Path $PSScriptRoot -Parent) 'python\python.exe'),
    [string]$AppDir = $PSScriptRoot,
    [string]$TaskName = 'AIOpHUB_BC_Frontu_Sync',
    [string]$At = '02:00'
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "python.exe not found at '$PythonExe'. Pass -PythonExe <path to portable python.exe>."
}
if (-not (Test-Path -LiteralPath (Join-Path $AppDir 'src\cli.py'))) {
    throw "src\cli.py not found under '$AppDir'. Run this script from the unpacked AIOpHub folder."
}
if (-not (Test-Path -LiteralPath (Join-Path $AppDir '.env'))) {
    Write-Warning ".env not found in '$AppDir': the scheduled sync will fail until it exists."
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument '-m src.cli sync --batch' -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'AIOpHub: BC VehicleCard -> Frontu workspaces batch sync' -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Registered '$TaskName': daily at $At as SYSTEM, MultipleInstances=IgnoreNew."
Write-Host "Next run: $($info.NextRunTime)"
Write-Host "Run now:  Start-ScheduledTask -TaskName '$TaskName'   (result: logs\app.log)"
