@echo off
cd /d %~dp0
call .venv\Scripts\activate
python asg_launcher.py