# Solar Analysis

Compare solar plants across **SolarEdge, Growatt, and SMA Sunny Portal**,
normalize the data into one schema, and generate an AI-powered HTML comparison
report.

## Purpose

This tool logs into each solar-monitoring portal with the site owner's
username/password (via a headless browser), fetches plant metrics (energy,
power, device status, alerts, CO₂, financials), normalizes them to a common
schema, and uses Claude to synthesize a comparison report with insights and
recommendations. The analysis is grounded: **all numeric values come from live
portal data and are computed in Python; Claude only writes the narrative**,
eliminating hallucinated figures.

## Authentication model

All three portals authenticate the same way — a **headless browser login with
the owner's username and password**. No API key or token is required.

| Portal | Login | Data source |
|--------|-------|-------------|
| SolarEdge | `monitoring.solaredge.com` OAuth form | internal dashboard JSON (`/services/sitelist/*`, `/services/dashboard/*`) |
| Growatt | `server.growatt.com` form | internal dashboard JSON (`/index/*`, `/panel/*`) |
| SMA Sunny Portal | SMA ID / Keycloak SSO | server-rendered PV System List table |

Growatt also still supports `mode: token` (OpenAPI v1) as an alternative if you
prefer a token to the browser login.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure credentials

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Put your secrets in `.env` (git-ignored):

```
ANTHROPIC_API_KEY=sk-ant-...

SOLAREDGE_USERNAME=...
SOLAREDGE_PASSWORD=...

GROWATT_USERNAME=...
GROWATT_PASSWORD=...

SMA_USERNAME=...
SMA_PASSWORD=...
```

`config.yaml` lists the plants; each references credentials via `${VAR}`
substitution. See `config.example.yaml` for the full three-portal layout. To
analyze fewer portals, just delete those plant entries.

## Running the analysis

```bash
python -m solaranalysis.cli --range snapshot
```

**Arguments**
- `--config config.yaml` (default) — config file path
- `--range {snapshot|30d|12mo|all}` (default `30d`) — time range. The portals
  currently expose counters only (today/month/year/lifetime) with no historical
  series, so every range produces a counters-based report; ranged runs state
  this explicitly in the data block and on stderr.
- `--out output/<timestamp>` (default) — output directory
- `--cache-dir .session_cache` (default) — browser session cache. Portal logins
  are persisted and reused for up to 6 hours, so back-to-back runs skip the
  password login; expired sessions fall back to a fresh login automatically.

The report is written to `output/<YYYYMMDD-HHMMSS>/report.html`.

**Environment toggles**
- `SOLAR_HEADLESS=0` — run with a visible browser window (useful to watch a run
  or solve an unexpected login challenge). Default is headless.
- On Windows, run with `PYTHONUTF8=1` so Hebrew/RTL plant names print cleanly.

## How it works

1. **Fetch** — each adapter logs in with a headless browser and reads the
   portal's data (internal JSON for SolarEdge/Growatt; the PV System List table
   for SMA). A per-plant failure is isolated and listed under "Unavailable
   Plants" rather than failing the whole run.
2. **Normalize** — data is mapped to a common `PlantData` schema (energy, power,
   devices, alerts, CO₂, financials) and validated (data-quality flags).
3. **Analyze** — Python computes every figure, including age-fair
   same-period specific yield (energy ÷ kWp). Claude receives the numbers plus a
   grounding prompt and writes the four report sections.
4. **Report** — the narrative is rendered into a self-contained styled HTML file.

**Key guarantee:** Python computes all numbers; Claude writes only the
narrative, so figures cannot be hallucinated. A post-generation `verify_numbers`
check flags any figure in the narrative not traceable to the data block
(rounded/derived deltas may legitimately appear).

**System status overview (Hebrew):** Above everything else, a second Opus 4.8
call (also at "xhigh" reasoning) reads the finished report and judges each
system's health from the facts already in it — device statuses, alert
severities, production figures, data-quality flags — and writes a short Hebrew
overview under the heading "סטטוס מערכות": a one-line fleet headline plus a
per-system traffic light: ✅ תקין (working correctly), ⚠️ דורשת תשומת לב (needs
attention), or ❌ תקלה (problem). A system that could not be fetched is always
shown as ❌. It is prepended above the executive summary in both the on-disk
`report.html` and the emailed dashboard (its headline also becomes the emailed
report's inbox preview text). Like the executive summary, if this call fails
it is skipped non-fatally — the run still delivers the report without it.

**Executive summary (Hebrew):** After the report is generated, a Claude
call (**Opus 4.8** at "xhigh" reasoning — `effort: xhigh` + adaptive thinking)
distills it into a concise Hebrew executive summary ("סיכום מנהלים"), prepended
to the top of the report (below the system status overview, above the detailed
analysis). It appears in both the on-disk `report.html` and the emailed body,
for both the CLI and the web app, and it summarizes only what the report
already states (no new figures). `verify_numbers` still runs on the detailed
report only. If the summary call fails it is skipped non-fatally — the run
still delivers the detailed report, with a note.

## Data coverage notes

Each portal exposes a different subset; missing metrics are marked
`not_exposed` and shown as "not reported" in the report rather than guessed.

- **SolarEdge** — energy today/month/year/lifetime (kWh), CO₂ & trees, inverter
  count (online-status inferred from the site's ACTIVE state), alert count,
  current power. No per-inverter serials or revenue at fleet level.
- **Growatt** — energy today & lifetime (kWh), lifetime revenue, CO₂, trees,
  inverter status (decoded best-effort). Monthly/yearly energy and current
  power are not exposed by the dashboard endpoints used.
- **SMA** — energy today/this-month/lifetime and specific yield from the PV
  System List. No device inventory, alerts, CO₂, or revenue in that view.

## Future enhancements

- **Time series** — per-plant daily/monthly energy series (for trend and
  worst-day anomaly detection) beyond the current summary metrics. SolarEdge
  exposes a range energy endpoint; Growatt/SMA need their history endpoints
  wired.
- **SMA depth** — device inventory and per-plant detail via the CSV download /
  per-plant pages, beyond the fleet list.

## Troubleshooting

- **A plant is "unavailable"** — check its username/password in `.env` and that
  the account can see the plant. Re-run with `SOLAR_HEADLESS=0` to watch the
  login and spot a challenge (CAPTCHA/OTP).
- **"[note] N report figures not found verbatim"** — the anti-hallucination
  check; usually rounded or derived values (deltas, sums) and plant IDs, not
  errors. Investigate only if a headline figure looks wrong.
- **Blank/short report** — if all plants failed to fetch, the report says "No
  plant data available"; fix credentials/connectivity and re-run.

## Project structure

```
solaranalysis/
├── cli.py                 # Entry point
├── config.py              # Config loading & ${ENV} substitution
├── pipeline.py            # fetch → normalize → analyze → report
├── core/
│   ├── schema.py          # Common data model (PlantData, Metric, Device, …)
│   ├── analyze.py         # Data block, model pick, verify_numbers, Claude call
│   ├── rollup.py          # Time-series rollups
│   ├── report.py          # HTML rendering
│   ├── units.py           # W→kW, Wh→kWh, specific yield, …
│   └── session_store.py   # Session caching
├── adapters/
│   ├── base.py            # SolarPortalAdapter interface + registry
│   ├── _browser.py        # Shared headless Playwright session helper
│   ├── solaredge.py       # SolarEdge (browser + internal JSON)
│   ├── growatt.py         # Growatt (browser + internal JSON; token alt)
│   ├── _growatt_v1.py     # Growatt OpenAPI v1 client (token mode)
│   └── sma.py             # SMA Sunny Portal (table read)
└── prompts/system.txt     # Grounding contract for Claude
tests/
└── (unit tests: pure mappers, analyze, pipeline, …)
```

## Web UI

A local web app (FastAPI + React) wraps the same fetch/normalize/analyze/report
pipeline behind a browser UI: manage plants, run/schedule comparisons, and
review reports and run history without editing YAML. The file-based CLI above
still works unchanged and does not require the web app.

### Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
cd frontend
npm install
npm run build
cd ..
```

`npm run build` produces `frontend/dist/`, which the server serves as static
files. Rebuild after pulling frontend changes.

### Run

```bash
python -m solaranalysis.web
```

**Arguments** (all optional; paths are resolved to absolute at startup)
- `--host` (default `0.0.0.0`) — bind address.
- `--port` (default `8000`) — bind port.
- `--data-dir` (default `./data`) — where the server keeps its database
  (`app.db`), encryption key (`secret.key`), logs, session cache, and report
  output. Point this at a persistent, private location in production.
- `--app-dir` (default: the project root) — where the server looks for the
  existing `config.yaml` (for one-time import, see below) and `.env` (for
  `ANTHROPIC_API_KEY`).

Open `http://<host>:<port>/` in a browser once the server is running.

### First boot: the setup token

On first start (no app password set yet), the server generates a random setup
token and **prints it to the console/log**, e.g.:

```
SETUP TOKEN (enter in the web setup screen): 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
```

The web UI shows a setup screen until a password is created. Copy the token
from the server log into that screen along with your chosen app password —
this closes the window where another client on the same network could
otherwise claim the app first. The token is single-use: once a password
exists, `POST /api/auth/setup` always returns 409 and the token no longer
matters.

If you're running the server as a background/Windows service rather than in
an interactive terminal, see
`docs/superpowers/plans/notes-web-ui-deploy.md` for where to find this token
in the service's log file.

### Credential threat model

Plant portal passwords/tokens are encrypted at rest with **Fernet**
(`cryptography`). The encryption key is auto-generated on first start at
`<data-dir>/secret.key`. Together, `<data-dir>/secret.key` and
`<data-dir>/app.db` are **exactly as sensitive as the old plaintext `.env`
file** — anyone who can read both files can decrypt every stored credential.
This is not a stronger guarantee than the CLI's `.env`; it protects against
casual exposure (e.g. a backup or DB copy without the key) rather than
against an attacker who already has filesystem access to the data directory.
Back up and restrict `<data-dir>` accordingly.

The app password itself is hashed (PBKDF2-HMAC-SHA256, 600k iterations, random
per-install salt) — it is never stored or transmitted in plaintext after
setup. The API never returns stored secret values; plant responses only ever
include `has_password`/`has_token` booleans.

**Changing the app password logs everyone out.** `PUT /api/auth/password`
rotates the session epoch, which immediately invalidates every previously
issued session cookie (yours included) — you'll need to log in again with the
new password.

### Importing an existing `config.yaml`

If `<app-dir>/config.yaml` exists, use Settings → Import in the web UI (or
`POST /api/import`) to one-time-copy its plants and their `.env`-resolved
credentials into the encrypted database. This does not delete or modify
`config.yaml`/`.env` — the CLI keeps working against them exactly as before,
independently of anything managed through the web UI.

### Email delivery

Every web-app run (manual or scheduled) that produces a report — status
`success` or `partial` — emails the report as an inline-HTML message via
Microsoft Graph (app-only `sendMail`). `failed` runs send nothing.
The email body is rendered in an email-optimized light theme with inline
styles (Outlook/Gmail don't support the on-disk report's CSS variables); the
`report.html` saved on disk keeps its full styling.

Configure it in `.env`:

- `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` — the Azure AD
  app registration, which must be granted the **Mail.Send application
  permission** (admin-consented) on `GRAPH_SENDER`.
- `GRAPH_SENDER` — the mailbox the app sends *as* (default
  `elcam.ai@elcam.co.il`).
- `REPORT_RECIPIENTS` — comma-separated recipient list (default: the sender).

If any `GRAPH_*` key is blank or `REPORT_RECIPIENTS` is empty, emailing is
disabled: the run logs an "email not configured" note and finishes normally.
A send failure never fails the run — it is logged as a note. The CLI
(`python -m solaranalysis.cli`) does not email.

## Development

```bash
python -m pytest -q          # backend (includes solaranalysis.web)
cd frontend && npm run build # frontend type-check + production build
```

The Playwright login/fetch glue is validated by live runs; unit tests cover the
pure mappers (raw payload → `PlantData`) and the analysis/report layers.
