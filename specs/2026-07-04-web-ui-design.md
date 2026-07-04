# Solar Analysis Web UI — Design

Date: 2026-07-04
Status: draft — pending user review

## 1. Purpose

A web application for managing solar-analysis configuration and running the
analysis pipeline, replacing hand-edited `config.yaml`/`.env` as the day-to-day
interface. Deployed on a LAN server for the owner and colleagues.

Capabilities:

- Edit plants (name, platform, auth), portal credentials, and global settings.
- Trigger pipeline runs with a chosen time range and watch live progress.
- Browse run history and view generated HTML reports in the browser.
- Schedule recurring runs.
- Test a plant's portal login (a real portal round-trip) without a full run.

### Non-goals

- Managing `ANTHROPIC_API_KEY` from the UI (stays in `.env`/environment on the
  server).
- Per-user accounts or roles (single shared app password).
- HTTPS termination (plain HTTP on trusted LAN; can be fronted by a reverse
  proxy later without app changes).
- Parallel pipeline runs (one run at a time, globally).
- Back-filling schedule runs missed while the service was down.
- Editing or deleting completed run records (history is append-only; report
  files can be cleaned manually on disk).
- A "test all plants" bulk action (per-plant test only).

## 2. Architecture

One deployable process: a **FastAPI** server (uvicorn) that serves the JSON
API under `/api` and the built **React** frontend as static files. App state
lives in **SQLite** at `<data>/app.db` (WAL mode). Report HTML files stay on
disk under `<data>/output/`; the DB indexes them.

### Path resolution (no cwd dependence)

The server takes a `--data-dir` (default `./data`, resolved to an absolute path
at startup) and an `--app-dir` for the existing project root (default: the
package's parent, resolved absolutely). **All** filesystem paths are derived
from these absolute roots, never from the process cwd:

- `app.db`, `secret.key`, `logs/`, `output/`, `session_cache/` → under `data-dir`.
- `config.yaml` / `.env` (for one-time import and for `ANTHROPIC_API_KEY`) →
  under `app-dir`.

The runner subprocess receives these as explicit CLI arguments; it never
relies on its own cwd.

Pipeline runs execute in a **subprocess** (`python -m solaranalysis.web.runner
--run-id N --db <path> --data-dir <path> --app-dir <path>`), keeping Playwright
crashes/hangs out of the server process and making cancellation a process kill.
The runner:

1. Calls `load_dotenv(<app-dir>/.env)` so `ANTHROPIC_API_KEY` is present
   (the server's DB-driven path bypasses `config.load_config`, which is the
   only place the CLI loads `.env` — see §12).
2. Loads plants + settings from the DB, decrypts credentials, and builds the
   existing `AppConfig`/`PlantConfig` objects in memory.
3. Constructs a `SessionStore(<data-dir>/session_cache)` and calls the existing
   `run_pipeline()` with a progress callback and a per-plant session key
   (see §6).
4. Emits structured JSON-lines events on stdout (protocol in §6).
5. Writes the report via `render_html`/`write_report` into `<data-dir>/output/`.

The server captures runner stdout line-by-line, redacts secrets from every
line (§4), appends to `<data>/logs/run-<id>.log`, broadcasts to browsers via
SSE, and updates the run row on exit.

The existing CLI (`solaranalysis/cli.py` + `config.yaml`) remains unchanged for
file-based runs, but the DB is the source of truth for the web app. A one-time
import (§9) migrates existing `config.yaml`/`.env` contents into the DB.

### Repository layout

```
solaranalysis/
  web/                  # new Python package
    __init__.py
    __main__.py         # python -m solaranalysis.web -> uvicorn server
    app.py              # FastAPI app factory, static + report serving
    auth.py             # password hashing, session signing, CSRF, middleware
    crypto.py           # Fernet key management, encrypt/decrypt
    db.py               # schema, migrations, connection helpers
    routes/             # plants.py, settings.py, runs.py, schedules.py,
                        #   auth.py, imports.py
    runner.py           # subprocess entry point (python -m solaranalysis.web.runner)
    run_manager.py      # single active operation lock; spawn/track/cancel; SSE fan-out
    scheduler.py        # APScheduler wiring from schedules table
    importer.py         # one-time config.yaml/.env -> DB import
  adapters/base.py      # + verify_login(); per-plant session key (see §6)
frontend/               # new Vite + React + TypeScript app
  src/ ...
  dist/                 # built assets, served by FastAPI (gitignored)
data/                   # gitignored: app.db, secret.key, logs/, output/, session_cache/
```

## 3. Data model (SQLite)

```sql
plants(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL CHECK (platform IN ('solaredge','growatt','sma')),
  auth_mode TEXT NOT NULL DEFAULT 'password' CHECK (auth_mode IN ('password','token')),
  username TEXT,
  password_enc BLOB,          -- Fernet ciphertext, NULL if not set
  token_enc BLOB,             -- Fernet ciphertext (Growatt token mode)
  tariff_per_kwh REAL,
  currency TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_test_at TEXT,          -- ISO UTC of last connection test
  last_test_ok INTEGER,       -- NULL until first test
  last_test_error TEXT
)

settings(key TEXT PRIMARY KEY, value TEXT)
-- keys: model (nullable), max_input_tokens, output_language,
--       password_hash (PBKDF2), session_epoch (int, bumped on password change),
--       setup_token (cleared once setup completes), schema_version

schedules(
  id INTEGER PRIMARY KEY,
  time_of_day TEXT NOT NULL,      -- 'HH:MM' server-local time
  days_of_week TEXT NOT NULL,     -- CSV of 0-6 (Mon=0), e.g. '0,1,2,3,4'
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  enabled INTEGER NOT NULL DEFAULT 1
)

runs(
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('running','success','partial','failed','cancelled','interrupted')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduled')),
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  runner_pid INTEGER,             -- for orphan detection on restart
  started_at TEXT NOT NULL,       -- ISO UTC
  finished_at TEXT,
  report_path TEXT,               -- relative to data-dir, e.g. output/20260704-.../report.html
  log_path TEXT NOT NULL,         -- relative to data-dir
  plants_summary TEXT,            -- JSON [{name, ok, reason?}] final per-plant states
  skipped_plants TEXT,            -- JSON [{name, reason}]
  notes TEXT,                     -- JSON: {verify_missing_count, series_missing, ...}
  error TEXT                      -- terminal error summary for failed runs
)
```

Status semantics (aligned to actual pipeline behavior — a report file is
written even when every plant is skipped, because `run_pipeline` returns a
placeholder report and the runner always writes it):

- `success` — report written, 0 plants skipped.
- `partial` — report written, ≥1 plant skipped (**including all skipped**).
- `failed` — **no report file written**: the runner crashed or errored before
  `report_written`, or exited without a `run_complete` event.
- `cancelled` — user killed it (see cancel/exit ordering in §6).
- `interrupted` — server found the row still `running` at startup and the
  recorded `runner_pid` is not alive (§10).

Disabled plants are excluded from runs, keep their credentials, and cannot be
connection-tested (the test endpoint 409s on a disabled plant).

## 4. Credential security

- Secrets (portal passwords, Growatt tokens) are encrypted at rest with
  **Fernet** (`cryptography`). Key auto-generated on first start at
  `<data>/secret.key`; on creation the file is ACL-restricted to the owner
  (`icacls` on Windows).
- Threat model (in README): protects the DB against casual copying/backup
  exposure. An attacker with filesystem access to both `app.db` and
  `secret.key` can decrypt — equivalent to today's plaintext `.env`, not worse.
  DPAPI was considered and rejected (ties DB to one machine, adds pywin32).
- **The API never returns secret values.** Plant responses include only
  `has_password: bool` / `has_token: bool`. Edit requests send a secret only to
  overwrite it; an omitted or empty field means "keep existing".
- **Secret redaction is stream-wide, not exception-only.** The set of live
  secret values (all configured passwords/tokens) is collected before a run.
  A single redaction filter is applied to **every** line of runner
  stdout/stderr — the pipeline's own `print("[warn] ...")` lines, Playwright
  noise, and tracebacks alike — before that line is written to the log file,
  pushed to SSE, or stored in any DB column (`error`, `skipped_plants`,
  `plants_summary`). Each secret is replaced with `***`.
- App password stored as PBKDF2-HMAC-SHA256 (stdlib `hashlib`), 600k
  iterations, per-install salt.

## 5. Authentication & request protection

### First-boot setup (race-closed)

On first start (no `password_hash`), the server generates a random
`setup_token`, writes its hash to settings, and **prints the token to the
server console/log**. Until a password is set, all routes redirect to a setup
screen; `POST /api/auth/setup {token, password}` succeeds only if the token
matches. This closes the window where any LAN client could otherwise claim the
app. `auth/setup` returns 409 once a password already exists.

### Sessions

- `POST /api/auth/login {password}` verifies against `password_hash`, then sets
  a signed, HttpOnly, SameSite=Lax session cookie. The cookie payload includes
  the current `session_epoch`.
- The cookie signing secret is derived as `HKDF(secret.key, salt=session_epoch)`
  — i.e. bound to the rotating epoch, **not** to `secret.key` alone.
- `PUT /api/auth/password {old, new}` (authenticated) updates the hash and
  **increments `session_epoch`**, so every previously issued cookie fails
  verification (real invalidation, consistent with stateless cookies).
- `POST /api/auth/logout` clears the cookie (stateless logout).
- `GET /api/auth/status` reports `{setup_required, authenticated}` (unauth-safe).

### Authorization boundary

Every route under `/api/*` requires a valid session cookie **except**
`auth/status`, `auth/login`, and `auth/setup`. This explicitly includes the
report-serving, log, and SSE endpoints (§6), which live under `/api` precisely
so the one rule covers them. The built frontend static assets (JS/CSS/index)
are public; they contain no secrets and gate themselves on `auth/status`.

### CSRF

SameSite=Lax alone does not distinguish ports, so another service on the same
LAN host could forge requests. Therefore every **state-changing** request
(POST/PUT/DELETE) must carry a custom header `X-Solar-CSRF: 1`; the SPA sends
it on all mutations via fetch. Cross-origin HTML forms cannot set custom
headers without a CORS preflight (which the server never grants), so this
blocks CSRF. Requests missing the header on a mutating route get 403.

### Login rate limit

In-process, keyed by client IP: after 5 failed logins within 60 s, further
attempts from that IP get 429 with `Retry-After` until the window clears.
Successful login resets the counter.

## 6. Run execution

### Single active operation

`run_manager` holds one global lock covering **both** pipeline runs and
connection tests (they share portal sessions and a browser is heavy). While
any operation holds the lock:

- `POST /api/runs` → 409 `{active: {kind, id}}`.
- `POST /api/plants/{id}/test` → 409 `{active: {kind, id}}`.

Scheduled and manual runs go through the identical run-creation path.

### Lifecycle

1. `POST /api/runs {time_range}` → 409 if the lock is held; else insert `runs`
   row (`running`), spawn the runner, store its `runner_pid`, return `201 {id}`.
2. Server reads runner stdout line-by-line: each line is redacted (§4), appended
   verbatim to the run log, and forwarded to SSE subscribers. Lines that parse
   as JSON events additionally update in-memory run progress (served by
   `GET /api/runs/{id}` and used for live step indicators).
3. **Exit handling (cancel/fail ordering).** A cancel sets an in-memory
   `cancelled` intent before killing the process. On process exit the server
   resolves status in this order: intent==cancelled → `cancelled`; else a
   `run_complete` event was seen → its status (`success`/`partial`); else →
   `failed` (with `error` = last 500 redacted chars of the log). Then it writes
   `finished_at`, `report_path`, `plants_summary`, `skipped_plants`, `notes`.
4. `POST /api/runs/{id}/cancel` → set cancelled intent, kill the process tree
   (Playwright children included, via `psutil`), release the lock.

### Runner event protocol (JSON lines on stdout)

```json
{"event":"run_start","plants":["North Roof","South Field"],"time_range":"30d"}
{"event":"plant_start","plant":"North Roof"}
{"event":"plant_step","plant":"North Roof","step":"login"}      // login|fetch
{"event":"plant_done","plant":"North Roof","ok":true}
{"event":"plant_done","plant":"South Field","ok":false,"reason":"..."}
{"event":"analyze_start"}                                        // Claude call
{"event":"report_written","path":"output/.../report.html"}
{"event":"run_complete","status":"partial",
 "skipped":[{"name":"South Field","reason":"..."}],
 "plants_summary":[{"name":"North Roof","ok":true},{"name":"South Field","ok":false,"reason":"..."}],
 "notes":{"verify_missing_count":2,"series_missing":false}}
```

Non-JSON lines (pipeline `[warn]` prints, Playwright noise) pass through to the
log/SSE as plain text (after redaction). `notes.verify_missing_count` carries
the `verify_numbers` result that `run_pipeline` returns, which the CLI prints
today and the web path would otherwise drop.

### Pipeline & adapter changes (targeted, backward-compatible)

1. **`run_pipeline()` gains** an optional `progress: Callable[[dict], None] =
   None` (per-plant events above; default `None` → CLI byte-identical) and an
   optional per-plant **session key** derivation so the session cache is keyed
   per account, not per platform (next point).

2. **Per-plant session cache key.** Today `base.py` keys the browser session
   store by `self.platform` alone, so two plants on the same platform (which
   the UI actively invites) share — and corrupt — each other's cached login.
   Fix: the session key becomes `f"{platform}:{stable_account_id}"` where the
   account id is a hash of the username (or plant id). `SessionStore` paths are
   sanitized for filesystem safety. CLI behavior is preserved (a single plant
   per platform still gets a stable, if differently-named, cache file).

3. **Real `login()` for connection tests.** Every adapter's current `login()`
   only *validates config* — the actual portal authentication happens inside
   `fetch()`. So a "test that calls login()" would contact nothing. Fix: extract
   each adapter's browser login steps into a private `_do_login(bs)` helper (no
   behavior change to `fetch`, which now calls it), and add
   `verify_login() -> None` to the adapter base + each adapter. `verify_login`
   opens a `BrowserSession`, runs `_do_login`, confirms the post-login URL/state
   is reached, and returns (raising `AdapterError` on failure) **without**
   fetching plant data. Token-mode Growatt verifies by a single lightweight
   authenticated call.

### Connection test

`POST /api/plants/{id}/test` (takes the global lock; 409 if held; 409 if the
plant is disabled) spawns a short-lived subprocess
(`python -m solaranalysis.web.runner --test --plant-id N ...`) that builds the
adapter and calls `verify_login()` with a 90 s timeout. Credentials reach the
subprocess **only** via the DB (it decrypts them itself) — never via argv or
environment. Result (`ok`, `error`) is stored in `last_test_at/ok/error` and
returned. The UI shows a spinner during the request (long-poll; no SSE).

## 7. Scheduling

- APScheduler `BackgroundScheduler` in the server process. On startup and after
  any schedule CRUD, jobs are rebuilt from the `schedules` table (cron trigger:
  `day_of_week`, `hour`, `minute`, server-local timezone).
- A firing job calls the same run-creation path as `POST /api/runs` with
  `trigger='scheduled'`. If the global lock is held, the firing is skipped and
  recorded as a line in the server log (no run row).
- Missed firings while the service was down are not back-filled
  (`misfire_grace_time` small, coalescing on).

## 8. API surface (summary)

All under `/api`; all require the session cookie except the three auth
endpoints noted; all mutations require `X-Solar-CSRF: 1`.

```
GET    /api/auth/status
POST   /api/auth/setup            {token, password}
POST   /api/auth/login            {password}
POST   /api/auth/logout
PUT    /api/auth/password         {old, new}

GET    /api/plants                -> [{id,name,platform,auth_mode,username,
                                        has_password,has_token,tariff_per_kwh,
                                        currency,enabled,last_test_*}]
POST   /api/plants                create (validation §11)
GET    /api/plants/{id}
PUT    /api/plants/{id}           update (validation §11)
DELETE /api/plants/{id}
POST   /api/plants/{id}/test      connection test (§6)

GET    /api/settings             -> {model,max_input_tokens,output_language}
PUT    /api/settings

GET    /api/schedules            CRUD (list/create/update/delete)
POST   /api/schedules
PUT    /api/schedules/{id}
DELETE /api/schedules/{id}

GET    /api/runs                 -> history list (paged)
POST   /api/runs                 {time_range} -> start (§6)
GET    /api/runs/{id}            -> row + live progress (in-memory if running)
POST   /api/runs/{id}/cancel
GET    /api/runs/{id}/log        -> full accumulated log text (resync)
GET    /api/runs/{id}/stream     -> SSE: {type:"log",line} / {type:"progress",event}
GET    /api/runs/{id}/report     -> the report HTML (headers per §10)

POST   /api/import               one-time config.yaml/.env import (§9)
```

## 9. Frontend

Vite + React + TypeScript SPA in `frontend/`, built to `frontend/dist`, served
by FastAPI at `/` (catch-all to `index.html` for client routing). Dev mode:
`vite dev` proxying to `localhost:8000`. Data fetching via TanStack Query; no
global state library; routing via React Router. Visual design is decided at
implementation time with the frontend-design workflow — not specified here.

Views (client-routed, behind login):

1. **Dashboard** — active-operation banner with live progress (SSE) or last-run
   summary; "Run now" + time-range select (`snapshot|30d|12mo|all`, default
   `30d`); next scheduled run; per-plant health chips (enabled + last test).
2. **Plants** — table (name, platform badge, auth mode, enabled toggle, last
   test status/time, actions). Add/edit form: name, platform, auth mode (token
   offered only for Growatt), username, password/token masked with "leave blank
   to keep current", tariff, currency. Client + server validation per §11.
3. **Runs** — history table (id, status chip, trigger, range, started, duration,
   report link). Detail page: per-plant step indicators — **live** from
   in-memory progress while running, and for historical runs reconstructed from
   the persisted `plants_summary`; auto-scrolling live log (SSE) while running,
   `GET /log` for completed runs; cancel button while running; completed runs
   embed `GET /api/runs/{id}/report` in a sandboxed iframe
   (`sandbox="allow-same-origin"`, no `allow-scripts`) with an "open report"
   link that also hits the same hardened endpoint (§10).
4. **Schedules** — list with enable toggles; editor for time, weekday
   checkboxes, time range.
5. **Settings** — model (empty = auto), max_input_tokens, output_language
   (en/he); change password; **Import** button (shown only when
   `config.yaml`/`.env` exist under app-dir) that calls `POST /api/import` and
   displays the returned summary of created/updated plants and
   resolved/missing secrets.

## 10. Report serving & error handling

### Report serving (XSS-hardened)

Report HTML embeds Claude's narrative and portal-derived text, so it is treated
as untrusted. `GET /api/runs/{id}/report`:

- requires the session cookie (it is under `/api`);
- resolves `report_path` **against `data-dir` only** and verifies the resolved
  path stays within `<data>/output/` (path-traversal guard); 404 otherwise;
- responds with `Content-Security-Policy: sandbox; default-src 'none'` and
  `X-Content-Type-Options: nosniff`, so no script in the report can execute or
  reach the app origin/API — whether embedded in the sandboxed iframe or opened
  directly. This neutralizes stored-XSS via a malicious plant name or prompt
  injection into the narrative.

### Other error handling

- Per-plant fetch failures keep the existing pipeline isolation; they surface as
  `plant_done ok:false` events, `skipped_plants`/`plants_summary`, and the
  existing "Unavailable Plants" report section. That append logic (currently in
  `cli.py`) is factored into a shared helper so CLI and runner don't duplicate
  it.
- Runner exits non-zero / without `run_complete` and no report → `failed`,
  `error` = last 500 redacted chars of the log.
- **Startup reconciliation.** For every `running` row: if `runner_pid` is a live
  process, adopt is not attempted — the server kills it (a run cannot be
  re-attached to a fresh server) and marks the row `interrupted`; if the pid is
  dead/absent, mark `interrupted` directly. Then release the lock.
- SSE disconnects: client auto-reconnects and, on reconnect, re-fetches
  `GET /api/runs/{id}` + `GET /api/runs/{id}/log` to resync (events are not
  replayed over SSE).
- SQLite in WAL mode; the runner only reads the DB (config at startup); all
  writes go through the server, avoiding writer contention.

## 11. Validation rules (plants)

Enforced on both create and update (not "at creation" only):

- `platform` ∈ {solaredge, growatt, sma}. `name` unique, non-empty.
- `auth_mode == 'token'` is allowed **only** for `platform == 'growatt'`.
  Switching a plant's platform away from Growatt forces `auth_mode='password'`
  and clears `token_enc`.
- On **create**: password mode ⇒ username + password required; token mode ⇒
  token required.
- On **update**: switching *into* password mode requires a username and either
  an existing stored password or a new one; switching *into* token mode
  requires an existing or new token. A blank secret field means "keep current"
  and is invalid if no current secret exists for the (new) mode.

## 12. Import (one-time migration)

`POST /api/import` runs the existing `load_config(<app-dir>/config.yaml,
<app-dir>/.env)` (resolving `${VAR}` refs from `.env`), then upserts plants by
name and writes settings. Secrets are encrypted on insert. Import is idempotent
(re-importing overwrites matching plant names). It returns a JSON summary
(plants created/updated, which secrets resolved vs. missing, settings applied);
errors (missing env var, malformed YAML) are returned verbatim for display. The
files are left untouched. (No separate dry-run/preview endpoint — the single
call returns enough for the UI to show what happened.)

## 13. Testing

- **Backend (pytest, extends existing suite):**
  - crypto: encrypt/decrypt round-trip; key creation idempotency; secret-key
    file ACL applied.
  - auth: setup-token flow (wrong token rejected, second setup 409), login/
    logout, wrong password, rate limit (429 after 5), cookie required on
    protected routes incl. report/log/stream, CSRF header required on mutations,
    password change bumps `session_epoch` and invalidates old cookies.
  - plants CRUD + §11 validation (create and update, mode/platform switches),
    secret-preserving updates (blank = keep), responses never contain secrets.
  - runs API with a **fake runner** (test double emitting scripted event
    lines): lifecycle statuses incl. `partial` when all plants skipped and
    `failed` only when no report; 409 on concurrent start; cancel resolves to
    `cancelled` not `failed`; interrupted recovery incl. live-pid kill; log
    capture; redaction applied to stored columns and log.
  - report endpoint: path-traversal rejected; CSP/nosniff headers present;
    auth required.
  - runner: builds correct `AppConfig` from a seeded DB (monkeypatched
    `run_pipeline`); loads `.env` for the API key; event emission from a
    scripted pipeline; stream-wide secret redaction.
  - adapters: `verify_login` success/failure paths (monkeypatched
    `BrowserSession`/`_do_login`); per-plant session key keeps two same-platform
    plants isolated; `fetch` unchanged after `_do_login` extraction (existing
    adapter tests pass untouched).
  - scheduler: schedule rows → expected jobs; skip-when-locked.
  - importer: fixture config.yaml/.env → expected DB rows + summary.
  - pipeline `progress` callback fires in order; `None` default changes nothing.
- **Frontend:** TypeScript strict; `npm run build` must pass. Component tests
  out of scope for v1.

## 14. Dependencies added

Backend: `fastapi`, `uvicorn[standard]`, `apscheduler`, `cryptography`,
`itsdangerous` (or stdlib HMAC signing), `psutil`. Frontend: Vite + React +
TypeScript, TanStack Query, React Router.

## 15. Deployment

- Build once: `cd frontend && npm install && npm run build`.
- Run: `python -m solaranalysis.web` (defaults: host `0.0.0.0`, port `8000`,
  `--data-dir ./data`, `--app-dir` = project root; all resolved absolutely).
- `ANTHROPIC_API_KEY` comes from `<app-dir>/.env` (loaded by the runner) or the
  server's environment.
- Windows service via NSSM: documented follow-up, not part of this spec.
