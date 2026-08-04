# Scheduled daily pipeline orchestrator — design

**Date:** 2026-08-04
**Status:** approved, not yet implemented
**Target:** llmadmin (192.168.30.84), `C:\apps\solar-analysis`, Windows Task Scheduler

## Problem

Everything this application produces is delivered by email, and nothing is
scheduled yet. Three separate schedules were planned (`NextTODO.md` line 203):

| What | Mechanism | Time |
|---|---|---|
| Fleet comparison run | APScheduler *inside* the NSSM `SolarAnalysis` service (`web/scheduler.py`) | 06:00 |
| SolarEdge optimizer collector + report | Task Scheduler, `-m solaranalysis.optimizers` (§11) | 06:30 |
| Growatt string collector + report | Task Scheduler, `-m solaranalysis.strings` (§12) | 06:45 |

Three problems with that arrangement:

1. **The fleet report depends on the web service being up.** The scheduler runs
   in-process, so a stopped or crashed service silently stops the daily report —
   with no signal anywhere.
2. **Three places to configure, three places to check.** Two Task Scheduler
   entries plus a row in the app's own database.
3. **A failed fleet run emails nothing at all.** Per `runner.py`, only
   `success`/`partial` mail; `failed` is silent. An unattended failure is
   indistinguishable from a quiet morning.

The web UI is a debug surface — the emails are the product. Scheduling should
not route through it.

## Goals

One Python entry point that Task Scheduler invokes once daily and that runs all
three subsystems, isolates their failures from each other, leaves a diagnosable
log, reports which stage failed through its exit code, and emails an alert when
something breaks.

## Non-goals

- Starting or supervising the web server. NSSM already owns that (§9); Task
  Scheduler is the wrong tool for a long-lived process.
- Replacing `web/scheduler.py`. The code stays; §13 says not to create a
  schedule row that would drive it.
- Consolidating the three report emails into one. Each subsystem keeps its own
  report, its own subject, and its own recipient override.
- Any change to the fetch/normalize/analyze/report pipeline itself.

## Architecture

| File | Role |
|---|---|
| `solaranalysis/orchestrator.py` | All logic. `python -m solaranalysis.orchestrator --data-dir DIR --app-dir DIR` |
| `daily_pipeline.py` (repo root) | Thin venv-relaunch wrapper mirroring `app.py`; defaults `--data-dir`/`--app-dir` from its own location |
| `tests/test_orchestrator.py` | Unit tests over the pure parts |
| `DEPLOYMENT.md` §13 | Dry run → register → verify, and what it supersedes |

The registered task invokes `-m solaranalysis.orchestrator`, consistent with
§11/§12. `daily_pipeline.py` exists for running it by hand — `python
daily_pipeline.py` from any shell, no venv activation.

**Why the venv wrapper matters.** Under a non-venv interpreter the installed
`anthropic` SDK is old enough that the Claude narrative calls fail *silently*:
the run still exits 0 and still emails, but the report has no narrative. The
wrapper reuses `app.py`'s proven `_relaunch_in_venv` pattern, and because the
stage subprocesses are spawned with `sys.executable`, re-launching the parent
correctly is what fixes the children too.

### Stage table

Sequential, continue-on-failure, each stage in its own process so a hung
Chromium or a native crash cannot take the other two down with it.

| Order | Stage | Bit | Mechanism | Default timeout |
|---|---|---|---|---|
| 1 | `fleet` | 1 | `RunManager.start_run("scheduled", range)` | 45 min |
| 2 | `optimizers` | 2 | `subprocess.run([sys.executable, "-m", "solaranalysis.optimizers", …])` | 60 min |
| 3 | `strings` | 4 | `subprocess.run([sys.executable, "-m", "solaranalysis.strings", …])` | 30 min |

Ordering matches §11's note that the collectors should follow the fleet snapshot
so all three describe the same day's data.

### Stage 1 — fleet, via `RunManager`

`RunManager` is usable outside the web app: it has no FastAPI dependency, it
spawns `-m solaranalysis.web.runner` as a subprocess, pumps its stdout into
`<data-dir>/logs/run-<id>.log` with secret redaction applied, and finalizes the
`runs` row. Driving the fleet stage through it means a scheduled run is
indistinguishable from a UI-triggered one, so the debug surface keeps showing
run history, progress, and report paths.

```
db.init_db(conn)                      # create_run precedes runner's own init_db
if repo.running_runs(conn):           # service scheduler, or a UI user
    outcome = skipped                 # do not double-run
else:
    rid = rm.start_run("scheduled", time_range)
    rm.join(rid, timeout=stage_timeout)
    act = rm.active()
    if act and act["id"] == rid:      # still going -> timed out
        rm.cancel(rid)                # kills the process tree (psutil)
        rm.join(rid, timeout=30)      # let _finish() write "cancelled"
    run = repo.get_run(conn, rid)     # status, log_path, error
```

Outcome mapping: `success`/`partial` → ok. `failed`/`cancelled`/`interrupted` →
failure. **`partial` is deliberately not a failure** — some plants being
unavailable is documented normal behaviour, and that report emails with its own
"Unavailable Plants" section, so the operator already sees it.

A `skipped` fleet stage sets **no** failure bit — a run is happening, just not
ours — but it logs a prominent warning and appears in the run summary. Under the
§13 configuration (no schedule row) it should never happen; if it does, someone
is running the UI or the schedule row came back.

`RunManager`'s `_lock` is per-instance, so it provides no cross-process
mutual exclusion against the NSSM service. The `repo.running_runs()` check is
what covers that, and the lock file below covers overlap with a previous
orchestrator run.

### Stages 2 & 3 — the collectors

Plain subprocesses. Child stdout is streamed line-by-line — not captured whole —
so a stage that hangs still leaves a partial log to diagnose instead of nothing.
Every line is passed through `events.Redactor` before it is written; these two
CLIs do not redact today and their output is about to land in a file.

Exit-code mapping for both: `0` → ok, anything else → failure.

Concretely that means optimizer/string exit `2` ("no enabled plant configured in
app.db") counts as a **failure**. *Accepted decision:* on a server past §10 that
is a regression, not a steady state, and the deliberate case has an explicit
remedy — drop the stage from `--only`. Silent-skip-forever is how a month of
optimizer reports disappears unnoticed. Note that §12 already guarantees this
maps cleanly: analysis and email failures deliberately do **not** change those
CLIs' exit codes, so a non-zero from them is always collection-level.

### Exit codes

A bitmask, so `LastTaskResult` names the failing stage:

| Code | Meaning |
|---|---|
| 0 | every attempted stage ok |
| 1 | fleet failed |
| 2 | optimizers failed |
| 4 | strings failed |
| 3, 5, 6, 7 | the corresponding combinations |
| 8 | the orchestrator itself could not run — lock held, bad arguments, or an error before staging |

`argparse` exits `2` on a usage error, which would decode as "optimizers
failed". Argument parsing is therefore wrapped so a usage error exits `8`
instead, removing the ambiguity.

### Overlap lock

`<data-dir>/pipeline.lock` holds `{"pid": …, "started_at": …}` as JSON. A live
pid (checked with psutil, already a dependency) means exit `8` with a
"previous run still active" line; a dead pid is reclaimed with a log line noting
the stale holder. Removed in a `finally`. This matters because
`-StartWhenAvailable` plus a slow run can otherwise re-enter.

### Logging

- One file per run: `<data-dir>/logs/pipeline-YYYYMMDD-HHMMSS.log`, UTC stamp,
  matching the report output-directory convention.
- Every line is tee'd to both stdout and the file. Task Scheduler discards
  stdout, so the file is the only durable record.
- `sys.stdout.reconfigure(encoding="utf-8")` at start. §12 documents that
  without `PYTHONIOENCODING=utf-8` a Hebrew or `·` print dies with a
  `UnicodeEncodeError` that reads like a collection failure. Setting it in
  process removes the prerequisite from the task registration.
- `pipeline-*.log` files older than `--log-retention-days` (default 30) are
  pruned at start. Bounded growth, no separate cleanup job. Nothing else in
  `logs/` is touched — `run-<id>.log` files belong to the web app.

The pipeline log holds stage boundaries, outcomes, timings, and the collectors'
full output. The fleet stage's detail is **not** in it: `RunManager` pumps that
into `logs/run-<id>.log`. The pipeline log records the run id and that path so
the two are always one hop apart.

### Alert email

Fires only when at least one failure bit is set. Silent otherwise, so a clean
day is just the three normal reports.

- **Not** on `partial` (that report already went out and names its own gaps).
- **Not** on a lock-held `8` — the wedged run raises its own timeout alert.
- **Yes** on an unhandled orchestrator error, best-effort from the outermost
  handler.

Recipients: `PIPELINE_RECIPIENTS` if set, else `mailer.recipients()`
(`REPORT_RECIPIENTS`) — the same override pattern as `OPTIMIZER_RECIPIENTS` and
`STRING_RECIPIENTS`.

Subject: `Solar pipeline · FAILED · <stages> · YYYY-MM-DD HH:MM UTC`

Body: a small inline-styled HTML table — stage, outcome, exit code, duration —
plus, for each failed stage, the evidence needed to act on it without an RDP
session. That evidence differs by stage, because the fleet stage's output never
passes through the orchestrator:

- **fleet** — the `runs` row's `error` field, which `RunManager._finish` already
  populates with either the runner's traceback or the last 500 characters of its
  output, plus the path to its own `logs/run-<id>.log`.
- **optimizers / strings** — the last ~20 lines the orchestrator tee'd for that
  stage, already redacted.

Both cases also carry the pipeline log path. Inline styles because Outlook and
Gmail do not support the on-disk report's CSS variables (the same constraint
`render_email_html` works around).

A send failure is logged as `alert email failed:` and never changes the exit
code, matching the house rule that mail problems do not mask the work.

### CLI surface

```
python -m solaranalysis.orchestrator
    --data-dir DIR --app-dir DIR             required, as in §11/§12
    [--range snapshot|30d|12mo|all]          default snapshot
    [--only fleet,optimizers,strings]        comma list; default all three
    [--no-email]                             suppress reports and the alert
    [--timeout-fleet MINUTES]                default 45
    [--timeout-optimizers MINUTES]           default 60
    [--timeout-strings MINUTES]              default 30
    [--log-retention-days N]                 default 30
```

`--only` is the single stage-selection mechanism; there are no `--skip-*` flags.
An unknown stage name is a usage error and exits `8`.

## Shared-code touches

Two, both small and guarded.

1. **`web/runner.py` — honour `--no-email`.** The runner mails whenever Graph is
   configured, so without this the orchestrator's `--no-email` would be a
   half-truth and §13's dry run would fire a real fleet email. A two-line guard
   in the existing mail block skips sending when `SOLAR_NO_EMAIL=1`, an
   environment variable only the orchestrator sets. The web UI passes nothing
   and is unaffected.
2. **Secret collection.** The orchestrator needs the same redaction seed
   `RunManager._secrets` builds (stored plant passwords/tokens) plus
   `GRAPH_CLIENT_SECRET` as `runner.collect_secrets` adds. It calls the existing
   `repo.list_plants` / `repo.load_plant_auth` / `crypto.load_or_create_key`
   directly rather than reaching into a private method.

`load_dotenv(paths.env_file)` runs at orchestrator start so `GRAPH_*` and
`PIPELINE_RECIPIENTS` are available for the alert. Each child loads it again
itself.

## Testing

`tests/test_orchestrator.py`, using fake stage callables and a fake spawn. No
portal, Playwright, or Graph calls — matching the existing posture where live
glue is validated by real runs and unit tests cover the pure layers.

- exit-code aggregation: all-ok, each single failure, every combination, and
  stages that were skipped or not selected
- `--only` selection and rejection of an unknown stage name (exit 8)
- alert trigger rules: `partial` → quiet, failure → alert, `--no-email` → quiet,
  lock-held → quiet
- alert body composition: per-stage rows, and the failed stage's log tail
- lock: acquire, refuse on a live pid, reclaim a stale one, release on failure
- log retention pruning, and that `run-<id>.log` files survive it
- collector exit 2/3/4 all mapping to failure
- the fleet timeout path, asserting `cancel` was called and the outcome recorded
- the fleet skip path when `repo.running_runs()` is non-empty

## Documentation changes

**`DEPLOYMENT.md` §13 (new).** Dry run (`--only optimizers,strings --no-email`),
then the first full manual run, then registration at 06:00 with a 3h
`ExecutionTimeLimit` and `-WorkingDirectory` at the repo root — `.env` resolves
relative to it, which is where `ANTHROPIC_API_KEY`, `GRAPH_*`, and the recipient
lists come from. Each step gets a verify, as §11/§12 do. Includes the exit-code
decode table and the note that, as in §11/§12, a 0 exit code does not by itself
prove mail went out.

**What §13 supersedes.** *Accepted decision:* leaving the old scheduling live
means duplicate runs and duplicate mail, so §13 states plainly:

- Do **not** create the Settings → Schedules row; disable it if one exists.
- Do **not** register §11/§12's tasks, with `Unregister-ScheduledTask` lines for
  a box where they already exist.
- §11 and §12 stay in the document as the manual-run and backfill reference —
  the 90-day backfills are still prerequisites — marked as superseded *for
  scheduling only*.

**`README.md`.** A short "Scheduled daily pipeline" subsection.

**`NextTODO.md`.** Line 203's three-schedule item collapses to the single task.

## Risks

- **`RunManager` is a web-layer class used from a top-level script.** Its public
  surface (`start_run`, `join`, `active`, `cancel`) is what gets used; no private
  attributes. The alternative was duplicating `_pump`/`_finish`, which is worse.
- **Total runtime is now serial.** Three stages back to back under one
  `ExecutionTimeLimit`; the 3h limit against 45+60+30 minutes of stage timeouts
  leaves headroom, and the per-stage timeouts are the real guard.
- **First run migrates the database** (§12's v7 tables), unchanged by this work
  but still worth the copy-`app.db`-first note in §13.
