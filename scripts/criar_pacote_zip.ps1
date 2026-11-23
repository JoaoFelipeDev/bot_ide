<#
.SYNOPSIS
  Empacota o projeto num arquivo .zip pronto para copiar para outra maquina (pendrive, email,
  OneDrive, etc.). O .zip NAO inclui o ambiente virtual, banco de dados, relatorios ou logs
  (esses sao gerados pelo instalador/uso na maquina de destino).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\criar_pacote_zip.ps1
  # gera algo como: MonitorPrecosIDELivraria_2026-11-17.zip, na pasta pai do projeto
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Date = Get-Date -Format "yyyy-MM-dd"
$StagingDir = Join-Path $env:TEMP "MonitorPrecosIDELivraria_pacote_$Date"
$ZipName = "MonitorPrecosIDELivraria_$Date.zip"
$ZipPath = Join-Path (Split-Path -Parent $ProjectRoot) $ZipName

# Pastas/arquivos que NAO devem ir no pacote (sao gerados/locais de cada maquina).
$ExcludeDirs = @(".venv", "data", "reports", "logs", "__pycache__", ".pytest_cache", ".git", "src/id_monitor.egg-info")
$ExcludeFilePatterns = @("*.pyc", "*.db")

if (Test-Path $StagingDir) {
    Remove-Item -Recurse -Force $StagingDir
}
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

Write-Output "Copiando arquivos do projeto (ignorando .venv, data, reports, logs, caches)..."

Get-ChildItem -Path $ProjectRoot -Force | Where-Object {
    $_.Name -notin @(".venv", "data", "reports", "logs", "__pycache__", ".pytest_cache", ".git", ".cursor")
} | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $StagingDir $_.Name) -Recurse -Force
}

# Segunda passada: remove recursivamente qualquer __pycache__/.pytest_cache/egg-info que tenha
# sido copiado dentro de subpastas (ex.: src/id_monitor/__pycache__).
Get-ChildItem -Path $StagingDir -Recurse -Directory -Include "__pycache__", ".pytest_cache", "*.egg-info" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Get-ChildItem -Path $StagingDir -Recurse -File -Include "*.pyc", "*.db" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Backups de receita dos concorrentes (config/backups) sao locais de cada maquina.
$backupsDir = Join-Path $StagingDir "config\backups"
if (Test-Path $backupsDir) {
    Remove-Item -Recurse -Force $backupsDir -ErrorAction SilentlyContinue
}

# Garante pastas vazias esperadas na maquina de destino (ja cobertas pelo install.ps1, mas nao
# custa deixar explicito).
foreach ($dir in @("data", "reports", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $StagingDir $dir) | Out-Null
}

if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}

Write-Output "Gerando $ZipPath ..."
Compress-Archive -Path (Join-Path $StagingDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

Remove-Item -Recurse -Force $StagingDir

Write-Output ""
Write-Output "Pacote criado: $ZipPath"
Write-Output "Copie esse .zip para a outra maquina, extraia em qualquer pasta e de dois cliques em 'instalar.bat'."
