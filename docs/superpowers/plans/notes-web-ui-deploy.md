# Deploying the Solar Analysis web UI as a Windows service (NSSM)

This note describes running `python -m solaranalysis.web` continuously as a
Windows service via NSSM, instead of in an interactive terminal. It assumes
the app already runs correctly by hand (see README.md → "Web UI" for install
and manual run instructions) — **verify that first**. For general NSSM usage
(install/remove/start/stop/logs/troubleshooting), use the
`nssm-service-manager` skill; this note only covers the parameters specific
to this app.

## Prerequisites

- Backend deps installed and Playwright's Chromium fetched, for the same
  Python interpreter the service will use:
  `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`
- Frontend built once: `cd frontend && npm install && npm run build` — the
  service serves the static files from `frontend/dist/`, it does not run
  `vite`/`npm` itself. Rebuild and (if the service is already running) restart
  the service after pulling frontend changes.
- `<app-dir>/.env` contains `ANTHROPIC_API_KEY` (same file the CLI already
  uses), **or** the key is set in the service's own environment (see
  "Environment variables" below).
- Decide the two absolute paths the service will use:
  - `<data-dir>` — persistent storage: `app.db`, `secret.key`, `logs/`,
    `session_cache/`, `output/`. Must be writable by the service account and
    should be backed up/restricted like a credentials file (see README →
    "Credential threat model").
  - `<app-dir>` — the project root (contains the `solaranalysis` package,
    `config.yaml`, `.env`, `frontend/dist`). Usually
    `C:\Users\Matan\python\solar-analysis`.

## Service parameters

| NSSM parameter | Value |
|---|---|
| Application (path) | `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe` |
| Arguments | `-m solaranalysis.web --data-dir <abs data-dir> --app-dir <abs app-dir>` |
| AppDirectory | `<abs app-dir>` (the project root — **required**: `python -m` resolves the `solaranalysis` package relative to the current working directory, so AppDirectory must be the repo root, not `<data-dir>` or anything else) |

Add `--host 127.0.0.1` to the arguments if the service should only be
reachable from the same machine; the app's own default (`0.0.0.0`) exposes it
to the LAN, which is appropriate only if that's intended and the host firewall
is configured accordingly.

Example install (adjust paths; run in an elevated PowerShell; `$nssm` is
whatever NSSM binary the `nssm-service-manager` skill resolves to on this
machine):

```powershell
$python = "C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe"
$appDir = "C:\Users\Matan\python\solar-analysis"
$dataDir = "C:\Users\Matan\solar-analysis-data"   # example — pick a real persistent path

& $nssm install SolarAnalysisWeb $python "-m solaranalysis.web --data-dir `"$dataDir`" --app-dir `"$appDir`""
& $nssm set SolarAnalysisWeb AppDirectory $appDir
& $nssm set SolarAnalysisWeb Description "Solar Analysis web UI (FastAPI + React)"
& $nssm set SolarAnalysisWeb Start SERVICE_AUTO_START
& $nssm set SolarAnalysisWeb AppExit Default Restart
& $nssm set SolarAnalysisWeb AppRestartDelay 5000
& $nssm set SolarAnalysisWeb AppStdout "$appDir\logs\solar-web.out.log"
& $nssm set SolarAnalysisWeb AppStderr "$appDir\logs\solar-web.err.log"
& $nssm set SolarAnalysisWeb AppStdoutCreationDisposition 4
& $nssm set SolarAnalysisWeb AppStderrCreationDisposition 4
& $nssm set SolarAnalysisWeb AppRotateFiles 1
& $nssm set SolarAnalysisWeb AppRotateBytes 10485760

net start SolarAnalysisWeb
```

(Create the `logs\` directory first if it doesn't exist, or point AppStdout/
AppStderr somewhere that does.)

## Where the SETUP TOKEN appears

On first start (no app password created yet), the server logs a line like:

```
SETUP TOKEN (enter in the web setup screen): 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
```

This is emitted via Python's standard `logging` module (`logging.warning(...)`
on the `solar.web` logger) and the app does not install a custom logging
handler, so it goes through Python's default handler for unconfigured
loggers — **which writes to stderr**, not stdout. When running interactively
this is invisible (stdout and stderr both go to the console), but under NSSM
they're captured separately:

- Check the **`AppStderr`** log file first (`solar-web.err.log` in the example
  above) — that's where the SETUP TOKEN line and any other warnings/errors
  land. Uvicorn's own startup/access lines go to `AppStdout`.
- If you'd rather not hunt across two files, point both `AppStdout` and
  `AppStderr` at the same log file path — NSSM supports this and interleaves
  both streams into one file, which is simpler for a single-purpose service
  like this one.

After first setup, retrieve the token by:
```powershell
Get-Content "$appDir\logs\solar-web.err.log" -Tail 50 | Select-String "SETUP TOKEN"
```
or tail the combined log if stdout/stderr were merged as above. The token is
single-use — once a password is set, it's cleared from the database and
`/api/auth/setup` always 409s regardless of what token is supplied.

If the service is reinstalled against a **fresh** `<data-dir>` (no existing
`app.db`), a new setup token is generated and printed the same way on that
first startup.

## Environment variables

`ANTHROPIC_API_KEY` is loaded from `<app-dir>/.env` at the start of each
analysis run (same mechanism the CLI uses). If you'd rather not keep a `.env`
file next to the service, set it directly on the service process instead:

```powershell
& $nssm set SolarAnalysisWeb AppEnvironmentExtra "ANTHROPIC_API_KEY=sk-ant-..."
```

Either place works; `.env` takes precedence only in the sense that it's
loaded — if the variable is already set in the service environment,
`load_dotenv` (used with default settings) does not override it.

## Firewall

If the service should be reachable from other machines on the LAN (i.e. you
did not restrict `--host` to `127.0.0.1`), open the port:

```powershell
New-NetFirewallRule -DisplayName "Solar Analysis Web (port 8000)" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

## Operating the service

Use the `nssm-service-manager` skill for day-to-day start/stop/restart/log
commands. A few app-specific notes:

- **Restarting** the service does not lose data — plants, runs, and settings
  live in `<data-dir>\app.db`; only in-memory state (an in-progress run) is
  lost, and the app already handles that case (a run still marked `running`
  at startup with a dead `runner_pid` is reconciled to `interrupted` — see
  README/spec §10).
- **Updating the frontend**: rebuild (`cd frontend && npm run build`) then
  restart the service so the new `frontend/dist` is picked up (static files
  aren't hot-reloaded).
- **Moving `<data-dir>`**: stop the service, move the directory, update the
  `--data-dir` argument (`nssm set SolarAnalysisWeb AppParameters ...` or
  reinstall), start the service.
