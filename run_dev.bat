@echo off
cd /d "%~dp0"

start "solar-analysis backend" cmd /k .venv\Scripts\python.exe -m solaranalysis.web --data-dir ./data

start "solar-analysis frontend" cmd /k "cd frontend && npm run dev"
