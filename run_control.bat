@echo off
setlocal
cd /d "%~dp0"
call "%~dp0setup_environment.bat"
if errorlevel 1 goto :failed

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0asg_control.py"
exit /b 0

:failed
pause
exit /b 1
