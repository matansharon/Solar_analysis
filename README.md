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

## Development

```bash
python -m pytest -q
```

The Playwright login/fetch glue is validated by live runs; unit tests cover the
pure mappers (raw payload → `PlantData`) and the analysis/report layers.
