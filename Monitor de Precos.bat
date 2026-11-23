@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\id-monitor-gui.exe" (
    echo.
    echo O programa ainda nao foi instalado nesta pasta.
    echo De dois cliques em "instalar.bat" primeiro e espere terminar.
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\id-monitor-gui.exe"
