@echo off
cd /d "%~dp0"
echo API de datos F09 en http://0.0.0.0:8600  (documentacion: /docs)
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 0.0.0.0 --port 8600
pause
