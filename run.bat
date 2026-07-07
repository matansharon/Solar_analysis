@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m solaranalysis.web --data-dir ./data
pause
