# Deploying solar-analysis to llmadmin (192.168.30.84)

First-time deployment plan. Commands run in **PowerShell on the server**
(`llmadmin`, 192.168.30.84). Internal network only — no public internet, no TLS.

**Shape:** FastAPI + uvicorn (ASGI) under an **NSSM** service. Unlike the usual
Flask/Waitress recipe, this app's production entrypoint is the module itself —
`python -m solaranalysis.web` starts uvicorn with no reload/debugger. Waitress is
**not** used (it is WSGI-only). The backend serves the built SPA from
`frontend\dist`, so there is no separate frontend server.

Values used below (from `.deploy.yml` — confirm before starting):
service `SolarAnalysis`, install path `C:\apps\solar-analysis`, port `8010`.

## First-time vs. update

`nssm status SolarAnalysis` — if it prints a service state, use the **update**
section at the bottom; if it errors (unknown service), do the first-time steps.

## First-time deploy

### 1. Prerequisites
```powershell
python --version   # 3.10+
node --version     # 18+
npm --version
git --version
nssm               # prints usage
```
**Verify:** all five print without error.

### 2. Get the code onto the server
```powershell
New-Item -ItemType Directory -Force C:\apps | Out-Null
cd C:\apps
git clone https://github.com/matansharon/Solar_analysis.git solar-analysis
cd C:\apps\solar-analysis
git checkout master; git pull
```
**Verify:** `git log --oneline -1` shows the latest commit (0a64482 or newer);
`solaranalysis\web\__main__.py` and `frontend\package.json` are present.

### 3. Backend venv
```powershell
cd C:\apps\solar-analysis
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
**Verify:** `.\.venv\Scripts\python.exe -c "import fastapi, uvicorn, anthropic"`
exits silently.

### 4. Build the frontend
```powershell
cd C:\apps\solar-analysis\frontend
npm install; npm run build
cd C:\apps\solar-analysis
```
**Verify:** `frontend\dist\index.html` exists (the backend serves it).

### 5. Configuration — `.env` + `config.yaml` (gitignored — copy manually)
Copy from the dev machine (`C:\Users\Matan\python\solar-analysis`) to
`C:\apps\solar-analysis\`:
- `.env` — must contain `ANTHROPIC_API_KEY`, `GRAPH_TENANT_ID`,
  `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`,
  `REPORT_RECIPIENTS` (comma-separated).
- `config.yaml` — start from `config.example.yaml` if a fresh config is wanted.

Confirm the port is free: `Test-NetConnection localhost -Port 8010` should
**fail** to connect before deploy.
**Verify:** both files exist; `.env` has all six keys.

### 6. App data / first login
The app creates `data\app.db` and `data\secret.key` on first start; there is no
seed user — the web UI's setup screen sets the login password, and plant
credentials (SolarEdge / Growatt / SMA) are entered through the UI (stored
encrypted with `data\secret.key`).
**Verify:** after step 7's smoke test, `data\app.db` exists and login works.

### 7. Smoke test (foreground)
```powershell
cd C:\apps\solar-analysis
.\.venv\Scripts\python.exe -m solaranalysis.web --data-dir .\data --port 8010
# second window:
Invoke-WebRequest http://localhost:8010/ | Select-Object StatusCode   # -> 200
```
There is **no `/api/health`** — root `/` (the SPA) is the health check.
**Verify:** 200 on `/`; setup/login screen renders in a browser. Ctrl+C to stop.

### 8. Register the NSSM service
```powershell
New-Item -ItemType Directory -Force C:\apps\solar-analysis\data\logs | Out-Null
nssm install SolarAnalysis "C:\apps\solar-analysis\.venv\Scripts\python.exe" "-m solaranalysis.web --data-dir C:\apps\solar-analysis\data --port 8010"
nssm set SolarAnalysis AppDirectory "C:\apps\solar-analysis"
nssm set SolarAnalysis AppStdout "C:\apps\solar-analysis\data\logs\service.out.log"
nssm set SolarAnalysis AppStderr "C:\apps\solar-analysis\data\logs\service.err.log"
nssm set SolarAnalysis Start SERVICE_AUTO_START
nssm set SolarAnalysis AppExit Default Restart
nssm start SolarAnalysis
```
`AppDirectory` must be the repo root — `.env`/`config.yaml` resolve relative to
the app dir.
**Verify:** `nssm status SolarAnalysis` → `SERVICE_RUNNING`;
`Invoke-WebRequest http://localhost:8010/` → 200. If not, read
`data\logs\service.err.log`.

### 9. Firewall (internal network)
```powershell
New-NetFirewallRule -DisplayName "solar-analysis 8010" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8010
```
**Verify:** from a workstation, `http://192.168.30.84:8010/` loads.

### 10. Acceptance
From a workstation open `http://192.168.30.84:8010`: complete setup/login, add
the three vendor plants, trigger a **manual snapshot run**, and confirm:
- the run completes in the UI (report + dashboard written), and
- the dashboard email arrives at the `REPORT_RECIPIENTS` address.

Then create the daily schedule in the UI. The scheduler runs **inside this
service** — the service being up is what makes scheduled report emails go out.

## Update an existing deployment
```powershell
nssm stop SolarAnalysis
cd C:\apps\solar-analysis; git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..
nssm start SolarAnalysis
Invoke-WebRequest http://localhost:8010/ | Select-Object StatusCode   # -> 200
```
`.env`, `config.yaml`, and `data\` (DB, key, outputs) are not in git and survive
`git pull`. **Back up `data\app.db` and `data\secret.key` before risky changes**
(stop service → copy → start). Losing `secret.key` makes the stored plant
credentials undecryptable.

## Security checklist
- [ ] `.env` and `data\` are gitignored, never committed.
- [ ] Runs via `python -m solaranalysis.web` under NSSM (uvicorn, no reload) — not `run_dev.bat`.
- [ ] A real login password was set at first run (app's own auth; no seed users).
- [ ] Firewall exposes 8010 to the internal network only.
- [ ] `data\secret.key` + `data\app.db` are included in server backups.
