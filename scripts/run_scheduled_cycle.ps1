<#
.SYNOPSIS
  Ciclo agendado: coleta + relatorio + sync Firestore + e-mail (Resend).
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "scheduled_run_$stamp.log"

if (-not (Test-Path $Python)) {
    throw "Python do ambiente nao encontrado: $Python. Rode instalar.bat primeiro."
}

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line
    Write-Output $line
}

Set-Location $ProjectRoot
Write-Log "Iniciando ciclo agendado..."
& $Python -m id_monitor.cli run-scheduled 2>&1 | ForEach-Object {
    Add-Content -Path $LogFile -Value $_
    $_
}
$exit = $LASTEXITCODE
Write-Log "Ciclo finalizado com codigo $exit (log: $LogFile)"
exit $exit
