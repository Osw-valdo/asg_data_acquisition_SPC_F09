@echo off
setlocal
cd /d "%~dp0"
call "%~dp0setup_environment.bat"
if errorlevel 1 goto :failed

"%~dp0.venv\Scripts\python.exe" "%~dp0asg_launcher.py"
if errorlevel 1 goto :failed
exit /b 0

:failed
pause
exit /b 1