@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   Instalando o Monitor de Precos de Concorrentes - IDE Livraria
echo ============================================================
echo.

set "FROM_ARCHIVE=0"

REM WinRAR / 7-Zip / extracoes temporarias: pasta tipica em AppData\Local\Temp\Rar$...
echo %~dp0 | findstr /I "Rar$DI Rar$EX 7zO 7zE WiseInstall" >nul && set "FROM_ARCHIVE=1"

REM Se estiver em Temp e a pasta do projeto estiver incompleta, tambem e sinal de zip aberto.
if "%FROM_ARCHIVE%"=="0" (
  echo %~dp0 | findstr /I "\\AppData\\Local\\Temp\\" >nul
  if not errorlevel 1 (
    if not exist "%~dp0src\id_monitor\__init__.py" set "FROM_ARCHIVE=1"
  )
)

if "%FROM_ARCHIVE%"=="1" (
  echo ERRO: Voce abriu o instalador DE DENTRO do arquivo zip/rar.
  echo.
  echo O Windows/WinRAR so extrai um arquivo temporario e o resto some.
  echo Por isso o install.ps1 "nao existe".
  echo.
  echo COMO FAZER CERTO:
  echo   1. Feche esta janela.
  echo   2. Clique com o botao direito no arquivo .zip
  echo   3. Escolha "Extrair para ..." / "Extract to ..."
  echo   4. Abra a PASTA que foi criada
  echo   5. De dois cliques em instalar.bat DENTRO dessa pasta
  echo.
  echo Nao abra o instalar.bat direto pelo WinRAR / pela janela do zip.
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0install.ps1" (
  echo ERRO: Nao achei o arquivo install.ps1 nesta pasta:
  echo   %~dp0
  echo.
  echo Extraia o zip COMPLETO para uma pasta ^(ex.: C:\MonitorPrecos^)
  echo e rode o instalar.bat de la. Nao rode de dentro do zip.
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0src\id_monitor\__init__.py" (
  echo ERRO: A pasta do programa esta incompleta ^(falta a pasta src^).
  echo Extraia o zip inteiro de novo para uma pasta vazia e tente outra vez.
  echo.
  pause
  exit /b 1
)

echo Pasta do programa:
echo   %~dp0
echo.
echo Isso vai, se necessario, instalar o Python, criar o ambiente do
echo programa e baixar o navegador usado para acessar os concorrentes.
echo Pode levar alguns minutos (depende da internet). Nao feche esta
echo janela durante o processo.
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set INSTALL_RESULT=%ERRORLEVEL%

echo.
if "%INSTALL_RESULT%"=="0" (
    echo Instalacao concluida com sucesso.
    echo O programa deve abrir automaticamente em alguns instantes.
    echo Depois, use o atalho na Area de Trabalho para abrir de novo quando quiser.
) else (
    echo Algo deu errado durante a instalacao ^(codigo %INSTALL_RESULT%^).
    echo Copie a mensagem de erro acima e peca ajuda.
)

echo.
pause
endlocal
