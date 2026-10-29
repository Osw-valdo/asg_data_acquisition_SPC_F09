@echo off
rem Uso:  run_all.bat              -> pregunta que colector iniciar (o ninguno)
rem       run_all.bat T1XX-1       -> API + dashboard + colector del modelo T1XX-1
rem       run_all.bat T1XX-2       -> API + dashboard + colector del modelo T1XX-2
setlocal EnableDelayedExpansion
call "%~dp0setup_environment.bat"
if errorlevel 1 goto :failed

set "MODEL=%~1"
if not defined MODEL (
    echo.
    echo Que colector quieres iniciar?
    echo   1^) T1XX-1
    echo   2^) T1XX-2
    echo   3^) Ninguno ^(solo API + dashboard^)
    set "CHOICE="
    set /p "CHOICE=Opcion [3]: "
    if "!CHOICE!"=="1" set "MODEL=T1XX-1"
    if "!CHOICE!"=="2" set "MODEL=T1XX-2"
)
if defined MODEL (
    if /i not "%MODEL%"=="T1XX-1" if /i not "%MODEL%"=="T1XX-2" (
        echo Modelo invalido: %MODEL%  ^(usa T1XX-1 o T1XX-2^)
        goto :failed
    )
)

echo Iniciando API en http://localhost:8600 y dashboard en http://localhost:8501
start "API F09" cmd /k ""%~dp0.venv\Scripts\python.exe" -m uvicorn api.main:app --host 0.0.0.0 --port 8600"
start "Dashboard F09" cmd /k ""%~dp0.venv\Scripts\python.exe" -m streamlit run "%~dp0app.py" --server.port 8501"

if defined MODEL (
    echo Iniciando colector %MODEL%
    start "Colector %MODEL%" cmd /k ""%~dp0.venv\Scripts\python.exe" "%~dp0collector_multi_ip.py" --model %MODEL%"
)

ping -n 7 127.0.0.1 >nul
start "" http://localhost:8501
exit /b 0

:failed
pause
exit /b 1
