@echo off
setlocal
cd /d "%~dp0"
call "%~dp0setup_environment.bat"
if errorlevel 1 goto :failed

"%~dp0.venv\Scripts\python.exe" "%~dp0collector_multi_ip.py" --model T1XX-1
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%

:failed
pause
exit /b 1
