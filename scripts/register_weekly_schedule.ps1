<#
.SYNOPSIS
  Registra tarefa semanal no Windows Task Scheduler (ex.: Seg/Qua/Sex as 10:00).

.PARAMETER Weekdays
  Dias em ingles curtos: mon,tue,wed,thu,fri,sat,sun (separados por virgula).

.PARAMETER Time
  Horario HH:mm (24h).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register_weekly_schedule.ps1 -Weekdays "mon,wed,fri" -Time "10:00"
#>
param(
    [string]$Weekdays = "mon,wed,fri",
    [string]$Time = "10:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $ProjectRoot "scripts\run_scheduled_cycle.ps1"
$TaskName = "IDMonitorPrecosAgenda"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Tarefa '$TaskName' removida (se existia)."
    exit 0
}

if (-not (Test-Path $ScriptPath)) {
    throw "Nao encontrei $ScriptPath."
}

$map = @{
    mon = "Monday"; tue = "Tuesday"; wed = "Wednesday"; thu = "Thursday"
    fri = "Friday"; sat = "Saturday"; sun = "Sunday"
}
$days = @()
foreach ($part in ($Weekdays.ToLower() -split ",")) {
    $key = $part.Trim()
    if ($map.ContainsKey($key)) { $days += $map[$key] }
}
if ($days.Count -eq 0) {
    throw "Nenhum dia valido em -Weekdays. Use mon,tue,wed,thu,fri,sat,sun."
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$Triggers = @()
foreach ($day in $days) {
    $Triggers += (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $Time)
}

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings `
    -Description "Monitor de Precos IDE: coleta + relatorio + e-mail nos dias agendados." -Force | Out-Null

Write-Output "Tarefa '$TaskName' registrada: $($days -join ', ') as $Time."
Write-Output "Testar agora: Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Remover: powershell -ExecutionPolicy Bypass -File scripts\register_weekly_schedule.ps1 -Unregister"
