<#
.SYNOPSIS
  Instalador local do Monitor de Precos IDE Livraria. Deixa o projeto pronto para uso numa
  maquina Windows, mesmo que ela nao tenha Python instalado ainda.

  O que ele faz, nesta ordem:
    1. Verifica se ja existe um Python 3.11+ usavel na maquina.
    2. Se nao existir, baixa o instalador oficial do Python (python.org) e instala
       silenciosamente so para o usuario atual (sem precisar de permissao de administrador).
    3. Cria o ambiente virtual (.venv) dentro desta mesma pasta.
    4. Instala as dependencias do projeto (click, playwright, SQLAlchemy, pandas, openpyxl,
       rapidfuzz, pyyaml, anthropic, keyring) dentro do ambiente virtual.
    5. Baixa o navegador Chromium usado pelo Playwright para fazer as buscas nos concorrentes.
    6. Cria um atalho na Area de Trabalho para "Monitor de Precos.bat" (menu do dia a dia).

  Normalmente voce nao chama este arquivo direto: de dois cliques em "instalar.bat" na mesma pasta.
#>

param(
    [switch]$NoLaunch,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

$PythonVersionToInstall = "3.12.7"
$MinPythonMajor = 3
$MinPythonMinor = 11

# Write-Host (e nao Write-Output): mensagens de progresso NAO podem ir para o pipeline,
# senao misturam no valor de retorno das funcoes (ex.: caminho do python.exe).
function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ==="
}

function Get-ExpectedPythonPath {
    $versionParts = $PythonVersionToInstall.Split(".")
    $folderVersion = "$($versionParts[0])$($versionParts[1])"
    return (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$folderVersion\python.exe")
}

function Test-PythonVersionOk {
    param([string]$ExePath)
    try {
        if ($ExePath -eq "py -3") {
            $versionOutput = & py -3 --version 2>&1 | Out-String
        } else {
            if (-not (Test-Path -LiteralPath $ExePath)) { return $false }
            $versionOutput = & $ExePath --version 2>&1 | Out-String
        }
    } catch {
        return $false
    }
    if ($versionOutput -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        return ($major -gt $MinPythonMajor -or ($major -eq $MinPythonMajor -and $minor -ge $MinPythonMinor))
    }
    return $false
}

function Get-UsablePython {
    <#
    Procura um Python 3.11+ ja instalado na maquina (via py/python, ou no caminho padrao
    onde o instalador silencioso coloca). Evita o "stub" da Microsoft Store.
    Retorna SO o caminho do executavel (string), ou $null.
    #>
    $candidates = New-Object System.Collections.Generic.List[string]

    # Caminho padrao do instalador silencioso (PrependPath=0, entao nao entra no PATH).
    $expected = Get-ExpectedPythonPath
    if (Test-Path -LiteralPath $expected) {
        $candidates.Add($expected)
    }

    # Qualquer python.exe em %LOCALAPPDATA%\Programs\Python
    $pythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $pythonRoot) {
        Get-ChildItem -Path $pythonRoot -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates.Add("py -3")
    }

    $pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source -notmatch "WindowsApps") {
        $candidates.Add($pythonCmd.Source)
    }

    foreach ($candidate in $candidates) {
        if (-not (Test-PythonVersionOk $candidate)) { continue }
        if ($candidate -eq "py -3") {
            try {
                $exePath = (& py -3 -c "import sys; print(sys.executable)" 2>&1 | Out-String).Trim()
                if ($exePath -and (Test-Path -LiteralPath $exePath)) {
                    return $exePath
                }
            } catch {
                continue
            }
        } else {
            return $candidate
        }
    }

    return $null
}

function Install-PythonAutomatically {
    <#
    Baixa o instalador oficial do Python (python.org) e instala silenciosamente so para o
    usuario atual (nao exige permissao de administrador). Retorna SO o caminho do python.exe.
    #>
    Write-Step "Nao encontrei um Python 3.11+ nesta maquina. Baixando o instalador oficial..."

    $url = "https://www.python.org/ftp/python/$PythonVersionToInstall/python-$PythonVersionToInstall-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-$PythonVersionToInstall-installer.exe"

    Invoke-WebRequest -Uri $url -OutFile $installerPath -UseBasicParsing

    Write-Step "Instalando Python $PythonVersionToInstall (silencioso, apenas para o usuario atual)..."
    $process = Start-Process -FilePath $installerPath -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=0",
        "Include_test=0", "Include_doc=0"
    ) -Wait -PassThru

    if ($process.ExitCode -ne 0) {
        throw "A instalacao do Python terminou com codigo de erro $($process.ExitCode)."
    }

    $expectedPath = Get-ExpectedPythonPath
    if (Test-Path -LiteralPath $expectedPath) {
        Remove-Item $installerPath -ErrorAction SilentlyContinue
        return $expectedPath
    }

    # Local pode variar entre versoes do instalador; procura por garantia.
    $found = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python") -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $found) {
        throw "Python foi instalado, mas nao consegui localizar o python.exe em $expectedPath."
    }
    Remove-Item $installerPath -ErrorAction SilentlyContinue
    return $found.FullName
}

function New-DesktopShortcut {
    param([string]$TargetBat, [string]$ShortcutName)
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = Join-Path $desktop "$ShortcutName.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $TargetBat
        $shortcut.WorkingDirectory = Split-Path $TargetBat
        $shortcut.IconLocation = "shell32.dll,41"
        $shortcut.Description = "Monitor de Precos de Concorrentes - IDE Livraria"
        $shortcut.Save()
        Write-Host "Atalho criado na Area de Trabalho: $shortcutPath"
    } catch {
        Write-Host "Nao consegui criar o atalho na Area de Trabalho (nao e critico): $_"
    }
}

function Resolve-PythonExePath {
    param([object]$Value)
    # Defesa: se algum Write-Output antigo misturou no valor, pega a ultima linha que for um .exe.
    # O @() e obrigatorio: com 1 linha o PowerShell transforma em string e .Count vira nro de letras.
    if ($null -eq $Value) { return $null }
    $text = if ($Value -is [System.Array]) { ($Value | ForEach-Object { "$_" }) -join "`n" } else { "$Value" }
    $lines = @($text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    for ($i = $lines.Length - 1; $i -ge 0; $i--) {
        $line = $lines[$i]
        if ($line -like "*.exe" -and (Test-Path -LiteralPath $line)) {
            return $line
        }
    }
    if ($lines.Length -eq 1 -and $lines[0] -eq "py -3") { return "py -3" }
    return $null
}

# --- Inicio da instalacao ---

Write-Step "Verificando Python"
$pythonExe = Resolve-PythonExePath (Get-UsablePython)
if ($pythonExe) {
    Write-Host "Python adequado encontrado: $pythonExe"
} else {
    $pythonExe = Resolve-PythonExePath (Install-PythonAutomatically)
    if (-not $pythonExe) {
        throw "Python foi instalado, mas nao consegui obter o caminho do python.exe."
    }
    Write-Host "Python instalado em: $pythonExe"
}

Write-Step "Criando ambiente virtual (.venv)"
$venvDir = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    Write-Host "Ambiente virtual ja existe, reaproveitando."
} else {
    if ($pythonExe -eq "py -3") {
        & py -3 -m venv $venvDir
    } else {
        & $pythonExe -m venv $venvDir
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Falha ao criar o ambiente virtual em $venvDir."
}

Write-Step "Instalando dependencias do projeto (pode levar alguns minutos)"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e $ProjectRoot

Write-Step "Baixando o navegador Chromium (usado para acessar os sites concorrentes)"

function Test-PlaywrightChromiumReady {
    $browsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
    if ([string]::IsNullOrWhiteSpace($browsersPath)) {
        $browsersPath = Join-Path $env:LOCALAPPDATA "ms-playwright"
    }
    if (-not (Test-Path $browsersPath)) { return $false }
    $chrome = Get-ChildItem -Path $browsersPath -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    $headlessShell = Get-ChildItem -Path $browsersPath -Recurse -Filter "chrome-headless-shell.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    return ($chrome -ne $null -and $headlessShell -ne $null)
}

& $venvPython -m playwright install chromium

if (-not (Test-PlaywrightChromiumReady)) {
    Write-Step "Download do navegador ficou incompleto, tentando novamente..."
    & $venvPython -m playwright install chromium --force
}

if (-not (Test-PlaywrightChromiumReady)) {
    Write-Warning "Nao foi possivel confirmar a instalacao completa do navegador Chromium. Se o programa mostrar um erro de 'Executable doesn't exist', abra um terminal nesta pasta e rode: .\.venv\Scripts\playwright.exe install chromium --force"
}

Write-Step "Preparando pastas de dados"
foreach ($dir in @("data", "reports", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $dir) | Out-Null
}

Write-Step "Criando atalho de uso do dia a dia"
$menuBat = Join-Path $ProjectRoot "Monitor de Precos.bat"
if ((Test-Path $menuBat) -and -not $NoShortcut) {
    New-DesktopShortcut -TargetBat $menuBat -ShortcutName "Monitor de Precos IDE Livraria"
}

Write-Step "Instalacao concluida!"
Write-Host "Use o atalho 'Monitor de Precos IDE Livraria' na Area de Trabalho (ou o arquivo"
Write-Host "'Monitor de Precos.bat' nesta pasta) para abrir o programa no dia a dia."
Write-Host "Os concorrentes ja vem configurados. Para a IA reparar buscas quebradas ou mapear sites novos, cole a chave da API na aba 'Configuracoes de IA' (opcional; veja INSTALACAO.md)."

$guiExe = Join-Path $venvDir "Scripts\id-monitor-gui.exe"
if ((Test-Path $guiExe) -and -not $NoLaunch) {
    Write-Step "Abrindo o programa..."
    Start-Process -FilePath $guiExe -WorkingDirectory $ProjectRoot
}
