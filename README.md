# Solar Analysis

Compare solar plants from SolarEdge and Growatt, normalize the data, and generate an AI-powered HTML analysis report.

## Purpose

This tool logs into your SolarEdge and Growatt solar-monitoring portals, fetches plant metrics (energy, power, device status, alerts), normalizes them to a common schema, and uses Claude AI to synthesize a comparison report with insights and recommendations. The analysis is grounded: **all numeric values come from live portal data; Claude only writes the narrative**, eliminating hallucination risk.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

The `playwright` library is needed only for the one-time SolarEdge browser login step (see below); Growatt uses direct API calls.

### 2. Configure Credentials

Copy the example configuration and secrets files:

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

**`.env` (git-ignored; store your secrets here)**
```
ANTHROPIC_API_KEY=sk-ant-...

# SolarEdge: Account/Site Admin -> API Access (or ask your installer)
SOLAREDGE_API_KEY=your-api-key

# Growatt: classic email/password login is blocked by Growatt (403 on the
# mobile login endpoint). Generate a ShinePhone app API token instead:
# Me -> tap username -> API Token -> Reopen.
GROWATT_TOKEN=your-openapi-v1-token
```

**`config.yaml` (edit to add your plants)**
```yaml
model: null            # null = auto-pick (sonnet-5 for 30d/snapshot; opus for 12mo/all)
max_input_tokens: 60000
output_language: en
plants:
  - name: SolarEdge Roof
    auth:
      platform: solaredge
      mode: api_key
      api_key: ${SOLAREDGE_API_KEY}
    tariff_per_kwh: 0.55
    currency: ILS
  - name: Growatt Roof
    auth:
      platform: growatt
      mode: token
      token: ${GROWATT_TOKEN}
    tariff_per_kwh: 0.55
    currency: ILS
```

Credentials are read from `.env` via variable substitution (`${VARIABLE_NAME}`).

### 3. One-Time SolarEdge Login (Browser) — only needed for password mode

`mode: api_key` (shown above) needs no browser login at all — this is the primary,
recommended path. If you instead use `mode: password`, SolarEdge requires a headed
Playwright browser to complete any login challenges (CAPTCHAs, OTP) and capture a
session cookie:

```bash
python -m solaranalysis.tools.se_login
```

This opens a browser window, logs in, and caches a ~20-day session cookie in `.session_cache/`. If your cookie expires, re-run this command.

Growatt does not use browser login. Classic email/password login against Growatt's
mobile API is blocked (403) — see the token instructions above; `mode: token` talks
directly to the Growatt OpenAPI v1 with plain HTTP calls, no login step required.

## Running the Analysis

```bash
python -m solaranalysis.cli --range 30d
```

**Arguments:**
- `--config config.yaml` (default) — path to your config file
- `--range {snapshot|30d|12mo|all}` (default: `30d`) — time range for analysis
  - `snapshot`: today's data
  - `30d`: last 30 days
  - `12mo`: last 12 months
  - `all`: lifetime
- `--out output/<timestamp>` (default) — output directory for the HTML report
- `--cache-dir .session_cache` (default) — where to store session cookies

The report is written to `output/<YYYYMMDD-HHMMSS>/report.html` and printed to stdout:

```
Report written: output/20250701-152345/report.html
```

Open the HTML file in your browser to view the styled analysis report.

## How It Works

1. **Fetch**: Each adapter (SolarEdge, Growatt) logs in and fetches plant metrics.
2. **Normalize**: Data is mapped to a common schema (energy, power, devices, alerts, CO₂).
3. **Analyze**: Metrics and structured data are sent to Claude AI along with a grounding prompt.
4. **Report**: Claude synthesizes insights (efficiency, trends, faults) into narrative sections, which are rendered as styled HTML.

**Key guarantee:** Python computes all numeric values from live portal data. Claude reads the numbers and writes only the narrative, so no figures are hallucinated.

## Rate Limits & Warnings

### Growatt
- The OpenAPI v1 token path has no client-side poll guard; be reasonable with
  polling frequency to avoid triggering Growatt-side throttling.
- Classic email/password login is **blocked** (403) — `mode: password` for
  Growatt is no longer supported; use `mode: token` (see Setup above).

### SolarEdge
- `mode: api_key` uses the official Monitoring API: **300 requests per day**.
  High-frequency polling will exhaust the quota.
- `mode: password` (fallback) enforces a client-side session-cache guard to
  avoid excessive logins.

## Future Enhancements

### SMA Sunny Portal (Phase 2)
SMA Sunny Portal adapter is planned for future releases. Currently, only SolarEdge and Growatt are supported.

### Field mapping confirmation (Growatt)
The Growatt v1 mapper (`solaranalysis/adapters/growatt.py`) uses defensive
`.get()` lookups for `plant/details` and `plant/data` field names, since the
exact field names/units for peak power and current power were not confirmed
against a live account at implementation time (see `CONFIRM LIVE` comments in
the source). These are expected to be finalized on the first live run with a
real token; if a field silently maps to `None`, check the comment block at
the top of `growatt.py` and adjust the `.get()` key to match the live payload.

## Troubleshooting

### "no cookie captured" or "no cached session cookie"
Run `python -m solaranalysis.tools.se_login` again to refresh the session cookie (password mode only).

### "growatt: classic password login is blocked..."
Switch Growatt to `mode: token` and set `GROWATT_TOKEN` from the ShinePhone app
(Me -> tap username -> API Token -> Reopen). See Setup above.

### Plants unavailable / fetch errors
Check your credentials in `.env` and confirm your portal account has access to the plants listed in `config.yaml`.

### "[warn] X report numbers not found in DATA"
Some metrics may be unavailable on specific portals (e.g., SolarEdge does not expose alerts or CO₂ via the official API). The analysis will note missing sections.

## Project Structure

```
solaranalysis/
├── cli.py                 # Entry point
├── config.py              # Config loading & validation
├── pipeline.py            # Data fetch → normalize → analyze → report
├── core/
│   ├── schema.py          # Common data model (PlantData, Metric, Device, etc.)
│   ├── session_store.py   # Cookie/session caching
│   ├── report.py          # HTML rendering
│   └── units.py           # Unit conversions (W→kW, Wh→kWh, etc.)
├── adapters/
│   ├── base.py            # SolarPortalAdapter interface
│   ├── solaredge.py       # SolarEdge implementation
│   └── growatt.py         # Growatt implementation
└── tools/
    └── se_login.py        # One-time SolarEdge browser login
tests/
└── (unit & integration tests)
```

## Development

Run tests:
```bash
pytest
```

Run a single test:
```bash
pytest tests/test_growatt_v1.py -v
```

## License

(Add your license here if applicable.)
