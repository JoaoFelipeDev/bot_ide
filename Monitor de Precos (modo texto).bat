@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set ID_MONITOR=.venv\Scripts\id-monitor.exe

if not exist "%ID_MONITOR%" (
    echo.
    echo O programa ainda nao foi instalado nesta pasta.
    echo De dois cliques em "instalar.bat" primeiro e espere terminar.
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ============================================================
echo    Monitor de Precos de Concorrentes - IDE Livraria
echo ============================================================
echo.
echo   1. Importar vendas (CSV) e selecionar os mais vendidos
echo   2. Adicionar um livro manualmente
echo   3. Listar livros monitorados
echo   4. Coletar precos em TODOS os concorrentes (pode demorar)
echo   5. Coletar precos de 1 livro especifico (avulso, mais rapido)
echo   6. Gerar relatorio (Excel)
echo   7. Smoke-test (checar rapidamente se os scrapers estao ok)
echo   8. Abrir a pasta de relatorios
echo   9. Sair
echo.
set /p OPCAO=Escolha uma opcao (1-9) e aperte Enter:

if "%OPCAO%"=="1" goto import_sales
if "%OPCAO%"=="2" goto add_book
if "%OPCAO%"=="3" goto list_books
if "%OPCAO%"=="4" goto collect_all
if "%OPCAO%"=="5" goto collect_one
if "%OPCAO%"=="6" goto report
if "%OPCAO%"=="7" goto smoke_test
if "%OPCAO%"=="8" goto open_reports
if "%OPCAO%"=="9" goto fim
goto menu

:import_sales
echo.
set /p CSVPATH=Caminho completo do arquivo CSV de vendas:
set /p TOPN=Quantos titulos selecionar (Enter para 30):
if "%TOPN%"=="" set TOPN=30
"%ID_MONITOR%" import-sales "%CSVPATH%" --top-n %TOPN%
goto fim_comando

:add_book
echo.
set /p TITULO=Titulo do livro:
set /p ISBN=ISBN (pode deixar em branco e apertar Enter):
set /p PRECO=Preco de venda na IDE Livraria, ex 39.90 (pode deixar em branco):
set ARGS=--title "%TITULO%"
if not "%ISBN%"=="" set ARGS=%ARGS% --isbn "%ISBN%"
if not "%PRECO%"=="" set ARGS=%ARGS% --price %PRECO%
"%ID_MONITOR%" add-book %ARGS%
goto fim_comando

:list_books
"%ID_MONITOR%" list-books
goto fim_comando

:collect_all
echo.
echo Coletando precos em todos os concorrentes ativos. Isso pode levar
echo varios minutos, dependendo da quantidade de livros e concorrentes...
echo.
"%ID_MONITOR%" collect
goto fim_comando

:collect_one
echo.
set /p TITULO=Titulo EXATO do livro (igual ao cadastrado):
"%ID_MONITOR%" collect --book "%TITULO%"
goto fim_comando

:report
"%ID_MONITOR%" report
goto fim_comando

:smoke_test
"%ID_MONITOR%" smoke-test
goto fim_comando

:open_reports
start "" "reports"
goto menu

:fim_comando
echo.
pause
goto menu

:fim
endlocal
exit /b 0
