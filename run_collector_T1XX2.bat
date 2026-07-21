@echo off
cd /d %~dp0
call .venv\Scripts\activate
python collector_multi_ip.py --model T1XX-2
pause
