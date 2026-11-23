<#
.SYNOPSIS
  Roda o ciclo mensal completo: coleta de precos em todos os concorrentes ativos + geracao do
  relatorio Excel. Pensado para ser chamado pelo Windows Task Scheduler (ver
  scripts/register_task_scheduler.ps1), mas pode ser executado manualmente tambem.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run_monthly_cycle.ps1
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "monthly_run_$Timestamp.log"

Set-Location $ProjectRoot

Write-Output "=== Monitor de Precos IDE Livraria - ciclo mensal ($Timestamp) ===" | Tee-Object -FilePath $LogFile -Append

try {
    Write-Output "Rodando coleta de precos..." | Tee-Object -FilePath $LogFile -Append
    & $Python -m id_monitor.cli collect 2>&1 | Tee-Object -FilePath $LogFile -Append

    Write-Output "Gerando relatorio..." | Tee-Object -FilePath $LogFile -Append
    & $Python -m id_monitor.cli report 2>&1 | Tee-Object -FilePath $LogFile -Append

    Write-Output "Ciclo concluido com sucesso." | Tee-Object -FilePath $LogFile -Append
} catch {
    Write-Output "ERRO no ciclo mensal: $_" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
