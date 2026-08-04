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
**Verify:** `git log --oneline -1` matches the newest commit on the dev machine
(`git log origin/master --oneline -1` there); `solaranalysis\web\__main__.py` and
`frontend\package.json` are present.

### 3. Backend venv + Playwright browser
```powershell
cd C:\apps\solar-analysis
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Every portal login drives a real Chromium (`solaranalysis.adapters._browser`), so
the browser binary must be installed **to a machine-wide path** — by default
Playwright installs under the *installing user's* `%LOCALAPPDATA%\ms-playwright`,
which a service running as `LocalSystem` cannot see:

```powershell
[Environment]::SetEnvironmentVariable("PLAYWRIGHT_BROWSERS_PATH", "C:\apps\playwright-browsers", "Machine")
$env:PLAYWRIGHT_BROWSERS_PATH = "C:\apps\playwright-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Setting it at `Machine` scope means both the NSSM service and the scheduled task
in step 11 inherit it. (Open a new shell after this for the variable to appear.)

**Verify:** `.\.venv\Scripts\python.exe -c "import fastapi, uvicorn, anthropic"`
exits silently, and `C:\apps\playwright-browsers` contains a `chromium-*` folder.

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

### 6. App data
The app creates `data\app.db` and `data\secret.key` on first start. **There is
no login** — the UI opens straight to the dashboard, so anyone who can reach
port 8010 on the internal network has full access. Plant credentials
(SolarEdge / Growatt / SMA) are entered through the UI and stored encrypted with
`data\secret.key`; back that file up and keep the firewall rule in step 9 scoped
to the internal network.
**Verify:** after step 7's smoke test, `data\app.db` and `data\secret.key` exist.

### 7. Smoke test (foreground)
```powershell
cd C:\apps\solar-analysis
.\.venv\Scripts\python.exe -m solaranalysis.web --data-dir .\data --port 8010
# second window:
Invoke-WebRequest http://localhost:8010/ | Select-Object StatusCode   # -> 200
```
There is **no `/api/health`** — root `/` (the SPA) is the health check.
**Verify:** 200 on `/`; the dashboard renders in a browser with no login prompt.
Ctrl+C to stop.

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
From a workstation open `http://192.168.30.84:8010`: the dashboard loads with no
login, add the three vendor plants, trigger a **manual snapshot run**, and
confirm:
- the run completes in the UI (report + dashboard written), and
- the dashboard email arrives at the `REPORT_RECIPIENTS` address.

Then create the daily schedule in the UI (Settings → Schedules, ~06:00, all days,
range `snapshot`). The scheduler runs **inside this service** — the service being
up is what makes scheduled report emails go out. §13 replaces this — do not
create the schedule row there. The service is then only the UI and run history;
scheduled reports come from the Scheduled Task.

### 11. Daily optimizer collector + anomaly report (scheduled task)

Superseded for scheduling by §13, which runs this collector as one of its
stages. The dry run and backfill below are still the reference, and the
backfill is still a prerequisite.

The per-optimizer collector is a **separate process**, not part of the web
service — it has its own entry point and its own email. Run it after the fleet
snapshot so both use the same day's data.

**Do step 10 first.** The collector reads the enabled SolarEdge plant's stored
credentials out of `app.db`, and those only exist once the plant has been added
through the web UI. Run before that and it exits **2** with `no enabled
SolarEdge plant configured in app.db`.

First a manual dry run (no email) to confirm credentials and site discovery:

```powershell
cd C:\apps\solar-analysis
.\.venv\Scripts\python.exe -m solaranalysis.optimizers `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --date 2026-07-25 --no-email
```
**Verify:** it prints one `site <id>: N optimizers, M energy rows over 1 day(s)`
line per site (four expected) and `analysis complete: … (email skipped)`. An
exit code of 3 (`no sites found`) means the sitelist call was unauthorized.

Then backfill history so the degradation trend has something to work with
(needs ≥14 days; this takes a while — one API round-trip per site per day):

```powershell
.\.venv\Scripts\python.exe -m solaranalysis.optimizers `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --backfill 90 --no-email
```

Register the daily task (06:30, after the 06:00 fleet run):

```powershell
$app = "C:\apps\solar-analysis"
$action = New-ScheduledTaskAction -Execute "$app\.venv\Scripts\python.exe" `
  -Argument "-m solaranalysis.optimizers --data-dir $app\data --app-dir $app" `
  -WorkingDirectory $app
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "SolarAnalysis-Optimizers" -Action $action `
  -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest
```

`-WorkingDirectory` must be the repo root — `.env` resolves relative to it, and
that is where `OPTIMIZER_RECIPIENTS` / `ANTHROPIC_API_KEY` / `GRAPH_*` come from.
Recipients fall back to `REPORT_RECIPIENTS` when `OPTIMIZER_RECIPIENTS` is unset.

**Verify:** `Start-ScheduledTask -TaskName "SolarAnalysis-Optimizers"`, then
`(Get-ScheduledTaskInfo "SolarAnalysis-Optimizers").LastTaskResult` → `0`, and an
anomaly email arrives. Spot-check one flagged optimizer against the site's
Digital Twin panel in the SolarEdge portal before trusting the run.

On an all-clear day the report still emails, but the narrative model call is
skipped — a tables-only email is expected, not a failure.

### 12. Daily Growatt string collector (scheduled task)

Superseded for scheduling by §13, which runs this collector as one of its
stages. The dry run and backfill below are still the reference, and the
backfill is still a prerequisite.

Like the optimizer collector, this is a **separate process** with its own entry
point. It collects per-MPPT-input daily energy plus the full 5-minute
per-channel series for the Growatt inverter, then **analyzes the accumulated
series and emails an anomaly report** (Phase C2).

**This code must be on the server first.** Step 2 pulls from origin, so confirm
`git log origin/master..master` is empty before deploying — otherwise the server
will not have `solaranalysis/strings/` at all.

**Do step 10 first.** Credentials come from the enabled Growatt plant's stored
row in `app.db`, which only exists once the plant is added through the web UI.
Run before that and it exits **2** (`no enabled Growatt plant configured in
app.db`).

Exit codes are unchanged by C2: **0** clean · **2** no enabled Growatt plant ·
**3** the plant or device list came back empty (almost always a failed or
unauthorized fetch, not a genuinely deviceless plant) · **4** one or more days
failed to collect. **Analysis and email failures deliberately do not change the
exit code** — they print `narrative skipped:` or `email failed:` and the run
still reports on the collection, which is the part worth retrying. As with §11,
**a 0 exit code does not by itself mean mail went out**; check the final line.

**Recipients:** `STRING_RECIPIENTS` in `.env` if set (comma-separated),
otherwise the app's configured `REPORT_RECIPIENTS`. It is independent of
`OPTIMIZER_RECIPIENTS` — the two reports can go to different people. Pass
`--no-email` to collect and analyze without sending, which is the right flag for
the first manual run.

First a dry run for a single day:

```powershell
cd C:\apps\solar-analysis
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m solaranalysis.strings `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --backfill 1 --no-email
```
**Verify:** one line per day reading
`<serial> <date>: 18 channels, 6 energy rows, ~5184 samples, 4/4 pages`, then
`analysis complete: N finding(s) (email skipped)`, and exit code 0.
**`18 channels` and `4/4 pages` are the two numbers that confirm the whole
chain** — 6 MPPT inputs plus 12 individual strings, and all four pages of the
day walked. A `PARTIAL` suffix means pages 1–3 failed; the day's energy still
landed but its intraday series is incomplete, and the analyzer will report the
day as incomplete rather than judging it.

Then backfill the available history:

```powershell
.\.venv\Scripts\python.exe -m solaranalysis.strings `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --backfill 90
```

**Expect most days to print `no data (pre-install or out of range)`, and that is
not an error.** The current inverter (`MZHRF6K002`) was commissioned 2026-07-10,
and the portal hard-rejects queries beyond roughly 90 days, so a 90-day backfill
legitimately finds only the days since commissioning. As of 2026-07-28 that is
18 days. `ERROR` lines are the real failure signal — re-run those dates
individually with `--date`, which is safe because every write is an idempotent
upsert.

Register the daily task (06:45, after the 06:30 optimizer run):

```powershell
$app = "C:\apps\solar-analysis"
$action = New-ScheduledTaskAction -Execute "$app\.venv\Scripts\python.exe" `
  -Argument "-m solaranalysis.strings --data-dir $app\data --app-dir $app" `
  -WorkingDirectory $app
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:45am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "SolarAnalysis-Strings" -Action $action `
  -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest
Set-ScheduledTask -TaskName "SolarAnalysis-Strings" -Settings $settings
```

Set `PYTHONIOENCODING=utf-8` for the task as well — the CLI prints a Unicode em
dash and the report subject line contains `·`, and with `output_language: he`
the narrative itself is Hebrew. Either add it as a machine-scope environment
variable or wrap the call in a one-line `.cmd`; without it the run can die on a
`UnicodeEncodeError` when it prints, which would look like a collection failure.

**The rules need history, and say so rather than guessing.** Every threshold
compares a channel against its own trailing median and needs
`MIN_HISTORY_DAYS = 7` usable days (14 for the degradation rule); below that a
rule is **skipped, not fired**. So run the 90-day backfill *before* enabling the
schedule — on a database with no history the first week of reports will be
all-clear by construction, which is correct but uninformative. Days captured
incompletely (under 600 producing minutes) are excluded from every history
comparison and reported as `day incomplete` instead of being judged.

**Verify:** `Start-ScheduledTask -TaskName "SolarAnalysis-Strings"`, then
`(Get-ScheduledTaskInfo "SolarAnalysis-Strings").LastTaskResult` → `0`. A `4`
means at least one day failed — re-run that date manually. Confirm the report
arrived, and that its findings read as **channel-relative** (`PV3 … below its
own median`, `strings 9+10 … off this pair's own median`) — an absolute-sounding
finding would mean the wrong analyzer is deployed. Confirm the data landed:

```powershell
.\.venv\Scripts\python.exe -c "from solaranalysis.web import db; c=db.connect(r'C:\apps\solar-analysis\data\app.db'); print('days', c.execute('SELECT COUNT(DISTINCT day) FROM channel_day_energy').fetchone()[0]); print('channels', c.execute('SELECT COUNT(*) FROM inverter_channels').fetchone()[0]); print('samples', c.execute('SELECT COUNT(*) FROM channel_samples').fetchone()[0])"
```

**Reading the report.** Findings are worst-first: `dead` (a channel stopped
reporting or produced nothing) → `fault` (the inverter's own
`StrBreak`/`StrUnblance`/`StrUnmatch`/fault codes — authoritative, no threshold
involved) → `underperforming` → `imbalance` (one string of a parallel pair) →
`degrading` → `watch` (intraday shading/soiling, temperature).

The one thing an operator must know: **the six MPPT inputs are not peers and the
six string pairs are not peers.** PV1–3 each carry ~0.195 of plant DC energy
against PV4–6's ~0.135 because PV4–6 are physically smaller arrays, and pair
3+4 sits permanently at ~24% current imbalance while pair 5+6 sits at ~14%.
None of that is a fault. Every rule therefore compares a channel only against
its *own* history, and the report states each finding's own baseline next to the
measured value so the comparison is visible. Thresholds and the measurements
behind them are in
`docs/superpowers/plans/2026-08-02-growatt-string-c2-calibration.md`.

**Disk:** this table grows about **0.73 MB/day** (~260 MB/year) unpruned, in the
same `app.db` as the web app's data. Survivable for a single plant, but the DB
will dominate backups within a year — a retention prune for `channel_samples` /
`inverter_samples` is a tracked backlog item. Watch free space on the data drive
if you also enable §11's optimizer history.

**First run migrates the database.** `db.init_db()` additively adds the four v7
tables (`inverter_channels`, `channel_day_energy`, `channel_samples`,
`inverter_samples`) on first execution. It is `CREATE TABLE IF NOT EXISTS` only
— no existing table is altered or dropped — and this path has been exercised
against a real populated database. Still, take a copy of `app.db` before the
first run if the deployment already holds data you care about.

### 13. Daily pipeline — one scheduled task for all three subsystems

This supersedes the three separate schedules for *scheduling only*. §11 and §12
remain the reference for manual runs and the backfills, which are still
prerequisites — **run their 90-day backfills before enabling this task**, or the
first week of string reports will be all-clear by construction.

**Do not also:**
- create a row in Settings → Schedules (disable any that exists) — it would run
  a second fleet report, from inside the web service, and email it;
- register `SolarAnalysis-Optimizers` or `SolarAnalysis-Strings`. On a server
  where they already exist:
  ```powershell
  Unregister-ScheduledTask -TaskName "SolarAnalysis-Optimizers" -Confirm:$false
  Unregister-ScheduledTask -TaskName "SolarAnalysis-Strings" -Confirm:$false
  ```

**Do step 10 first.** Both collectors read their plant's stored credentials from
`app.db`, which only exist once the plant is added through the web UI.

**Take a copy of `app.db` before the first run** if the deployment already holds
data you care about — the first run applies §12's additive v7 migration.

Dry run the two collectors, no email:

```powershell
cd C:\apps\solar-analysis
.\.venv\Scripts\python.exe -m solaranalysis.orchestrator `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --only optimizers,strings --no-email
```

**Verify:** `data\logs\pipeline-<stamp>.log` exists and ends with a `=== summary
===` block naming both stages, and `echo $LASTEXITCODE` is `0`. A `2` means the
optimizer plant is missing from `app.db`, `4` the Growatt one.

Then the whole pipeline, mail included:

```powershell
.\.venv\Scripts\python.exe -m solaranalysis.orchestrator `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis
```

**Verify:** three emails arrive (fleet dashboard, optimizer anomalies, string
anomalies), the run appears in the web UI's Runs history with trigger
`scheduled`, and the exit code is `0`. As in §11/§12, **a 0 exit code does not by
itself prove mail went out** — check the log's final lines.

Register the daily task at 06:00:

```powershell
$app = "C:\apps\solar-analysis"
$action = New-ScheduledTaskAction -Execute "$app\.venv\Scripts\python.exe" `
  -Argument "-m solaranalysis.orchestrator --data-dir $app\data --app-dir $app" `
  -WorkingDirectory $app
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SolarAnalysis-Pipeline" -Action $action `
  -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest
```

`-WorkingDirectory` must be the repo root: `.env` resolves relative to it, and
that is where `ANTHROPIC_API_KEY`, `GRAPH_*`, and the recipient lists come from.
Unlike §11/§12, `PYTHONIOENCODING` is **not** required — the orchestrator forces
UTF-8 on its own stdout and on every stage it spawns.

**Verify:** `Start-ScheduledTask -TaskName "SolarAnalysis-Pipeline"`, then
`(Get-ScheduledTaskInfo "SolarAnalysis-Pipeline").LastTaskResult` → `0`.

**Exit codes.** A bitmask, so the code names the stage:

| Code | Meaning |
|------|---------|
| 0 | every attempted stage ok |
| 1 | fleet failed |
| 2 | optimizers failed |
| 4 | strings failed |
| 3 / 5 / 6 / 7 | the combinations (e.g. 5 = fleet + strings) |
| 8 | the orchestrator itself could not run — a previous run still holds `data\pipeline.lock`, bad arguments, or an error before any stage |

A `partial` fleet run — some plants unavailable — is **not** a failure; that
report emails with its own "Unavailable Plants" section.

**When something breaks** you get one alert email listing every stage's outcome
plus the failed stage's own evidence. Recipients: `PIPELINE_RECIPIENTS` if set,
otherwise `REPORT_RECIPIENTS`. Clean days send no alert. Note the alert is the
*only* signal for a failed fleet run, which emails no report at all.

**Where to look:**
- `data\logs\pipeline-<stamp>.log` — stage boundaries, outcomes, timings, and the
  collectors' full output. Pruned after 30 days.
- `data\logs\run-<id>.log` — the fleet stage's own detail; the pipeline log names
  the id and path.
- `data\pipeline.lock` — present only while a run is in flight. A stale one from
  a killed run is reclaimed automatically on the next run: immediately if its pid
  is gone, and after 12 hours regardless, which covers a pid Windows has recycled
  to some other live process. You should never need to delete it by hand.

**Task Scheduler discards this task's stdout and stderr.** Almost everything the
orchestrator reports goes to `pipeline-<stamp>.log` above, so that is mostly
harmless — but there is one path it does not cover. If the orchestrator fails
*before* it can create that log file (the data volume is full, or its ACLs deny
writes — exactly what the pre-stage guard exists to catch), stderr is the only
channel it has, and Task Scheduler throws it away. The only remaining signal is
`LastTaskResult = 8`. If a run reports `8` with no `pipeline-<stamp>.log` written
at all, the cause is almost certainly the data directory being unwritable —
re-run the same command by hand in a PowerShell window to see the actual error.
You can also make the task capture it by wrapping the call in a one-line `.cmd`
that redirects both streams to a file — the same wrapper trick §12 already
suggests for `PYTHONIOENCODING`, so if you already made one there, that is where
to add the redirect.

**Self-healing after a killed run.** A run killed mid-fleet-stage — Ctrl-C, the
3h limit, a reboot — leaves its `runs` row at `running`. The next run detects
that the holder is gone, logs `[note] reclaimed a stranded runs row <id>`, marks
it `interrupted`, and proceeds. A row younger than 15 minutes, or one whose pid
is still alive, is treated as a genuine run and yielded to instead: you will see
`SKIPPED` and no fleet report that morning, which is correct if someone is
running one from the UI. Two `SKIPPED` days in a row with nobody using the UI is
worth investigating.

**Tuning:** `--only fleet` to re-run a single stage by hand,
`--timeout-fleet N` / `--timeout-optimizers N` / `--timeout-strings N` in minutes
(defaults 45/60/30), `--range 30d` for a different window,
`--log-retention-days N`.

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

## Troubleshooting

- **Service won't start** → read `data\logs\service.err.log`. Usual causes: port
  8010 busy, `.env` missing (it resolves relative to `AppDirectory`, so that must
  be the repo root), or a dependency missing from the venv.
- **A portal run fails with a Playwright/browser error** (`Executable doesn't
  exist`, `BrowserType.launch`) → the Chromium from step 3 isn't visible to the
  account the service runs as. Confirm `PLAYWRIGHT_BROWSERS_PATH` is set at
  **Machine** scope and that `C:\apps\playwright-browsers` holds a `chromium-*`
  folder, then restart the service so it picks the variable up.
- **Login to a portal suddenly fails / sitelist unauthorized** → the cached
  browser session expired. Delete `data\session_cache` and re-run; the adapter
  re-authenticates and rewrites it.
- **Optimizer run exits 2** (`no enabled SolarEdge plant configured`) → the
  SolarEdge plant has not been added in the web UI yet. See §11.
- **Optimizer run exits 3** (`no sites found`) → the sitelist call returned empty
  or unauthorized. Almost always the session cache above, not an empty account.
- **Email never arrives** → check the `GRAPH_*` values and that
  `REPORT_RECIPIENTS` (or `OPTIMIZER_RECIPIENTS`) is set; the run prints
  `email not configured` when they are missing and `email failed: …` when Graph
  rejects the send. Both are printed, not raised — an exit code of 0 does not by
  itself mean mail went out.
- **Report arrives with tables but no prose** → the narrative call failed or was
  skipped; the run prints `narrative skipped: …`. Check `ANTHROPIC_API_KEY`. On a
  day with nothing flagged this is normal and intentional.
- **Port 8010 already in use** → pick a free port, then update it in three
  places: the `nssm install` arguments, the firewall rule, and `.deploy.yml`.

## Security checklist
- [ ] `.env` and `data\` are gitignored, never committed.
- [ ] Runs via `python -m solaranalysis.web` under NSSM (uvicorn, no reload) — not `run_dev.bat`.
- [ ] Understood that the app has **no authentication** — reaching port 8010 is
      full access. The firewall rule is the only access control.
- [ ] Firewall exposes 8010 to the internal network only.
- [ ] `data\secret.key` + `data\app.db` are included in server backups.
