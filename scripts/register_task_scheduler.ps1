<#
.SYNOPSIS
  Registra (ou re-registra) uma tarefa no Windows Task Scheduler que roda o ciclo mensal de
  coleta + relatorio automaticamente, sem precisar que Joao execute nada manualmente.

  Por padrao, agenda para rodar no dia 1 de cada mes, as 08:00. Ajuste -DayOfMonth/-Time se
  quiser outro dia/horario.

.PARAMETER DayOfMonth
  Dia do mes em que a tarefa deve rodar (1-28, para evitar problemas em meses mais curtos).

.PARAMETER Time
  Horario de execucao, formato HH:mm (24h).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register_task_scheduler.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register_task_scheduler.ps1 -DayOfMonth 5 -Time "09:30"
#>

param(
    [int]$DayOfMonth = 1,
    [string]$Time = "08:00"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $ProjectRoot "scripts\run_monthly_cycle.ps1"
$TaskName = "IDMonitorPrecosMensal"

if (-not (Test-Path $ScriptPath)) {
    throw "Nao encontrei $ScriptPath. Rode este script a partir da raiz do projeto clonado."
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

# "-Monthly -DaysOfMonth X -At HH:mm" agenda para rodar todo dia X de cada mes, no horario informado.
$Trigger = New-ScheduledTaskTrigger -Monthly -At $Time -DaysOfMonth $DayOfMonth

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Monitor de Precos IDE Livraria: coleta mensal + relatorio comparativo." -Force

Write-Output "Tarefa '$TaskName' registrada: roda todo dia $DayOfMonth de cada mes, as $Time."
Write-Output "Para testar agora mesmo: Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Para remover: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
