@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "DEPENDENCY_MARKER=%~dp0.venv\requirements-installed"
if not exist "%VENV_PYTHON%" goto :create_venv
if exist "%DEPENDENCY_MARKER%" exit /b 0
goto :install_dependencies

:create_venv
where py >nul 2>nul
if errorlevel 1 goto :python_missing

py -3 --version >nul 2>nul
if errorlevel 1 goto :python_missing

echo No se encontro el entorno virtual. Creando .venv...
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto :setup_failed
if not exist "%VENV_PYTHON%" goto :setup_failed

:install_dependencies
echo Instalando dependencias desde requirements.txt...
"%VENV_PYTHON%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :setup_failed

> "%DEPENDENCY_MARKER%" echo Instaladas desde requirements.txt
exit /b 0

:python_missing
echo ERROR: No se encontro el Python Launcher "py" ni una instalacion Python 3 utilizable.
echo Instala Python 3.10 o superior y habilita el Python Launcher; despues vuelve a ejecutar este archivo.
exit /b 1

:setup_failed
echo ERROR: No se pudo preparar el entorno virtual o instalar las dependencias.
echo Revisa que Python y pip funcionen y vuelve a ejecutar el archivo.
exit /b 1
