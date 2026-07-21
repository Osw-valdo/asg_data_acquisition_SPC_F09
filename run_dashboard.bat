@echo off
cd /d %~dp0
call .venv\Scripts\activate
streamlit run app.py --server.port 8502
pause
