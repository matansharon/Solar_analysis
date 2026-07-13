@echo off
cd /d "%~dp0"

start "SolarAnalysisBackend" cmd /k "echo SolarAnalysisBackend && .venv\Scripts\python.exe -m solaranalysis.web --data-dir ./data"

start "SolarAnalysisFrontend" cmd /k "echo SolarAnalysisFrontend && cd frontend && npm run dev"
