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

SOLAREDGE_USERNAME=your-email@example.com
SOLAREDGE_PASSWORD=your-password

GROWATT_USERNAME=your-username
GROWATT_PASSWORD=your-password
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
      mode: password
      username: ${SOLAREDGE_USERNAME}
      password: ${SOLAREDGE_PASSWORD}
    tariff_per_kwh: 0.55
    currency: ILS
  - name: Growatt Roof
    auth:
      platform: growatt
      mode: password
      username: ${GROWATT_USERNAME}
      password: ${GROWATT_PASSWORD}
    tariff_per_kwh: 0.55
    currency: ILS
```

Credentials are read from `.env` via variable substitution (`${VARIABLE_NAME}`).

### 3. One-Time SolarEdge Login (Browser)

SolarEdge's official API requires either an API key or a session cookie. For password-based login, you must run a headed Playwright browser to complete any login challenges (CAPTCHAs, OTP) and capture the session cookie:

```bash
python -m solaranalysis.tools.se_login
```

This opens a browser window, logs in, and caches a ~20-day session cookie in `.session_cache/`. If your cookie expires, re-run this command.

Growatt does not require browser login; it uses direct API calls.

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
- **Minimum 5 minutes** between consecutive polls.
- Polling faster risks a 24-hour account lockout. If you hit the rate limit, you will see: `[warn] growatt: poll guard active (min 5 min between logins)`.

### SolarEdge
- Official Monitoring API: **300 requests per day**.
- High-frequency polling will exhaust the quota.

Both adapters enforce client-side guards to prevent lockouts.

## Future Enhancements

### SMA Sunny Portal (Phase 2)
SMA Sunny Portal adapter is planned for future releases. Currently, only SolarEdge and Growatt are supported.

### Token / API Key Auth
For more reliable authentication without browser login:

- **Growatt**: Use an OpenAPI token instead of password. Update `config.yaml` to:
  ```yaml
  auth:
    platform: growatt
    mode: token
    token: your-openapi-token
  ```

- **SolarEdge**: Use an API key instead of password. Update `config.yaml` to:
  ```yaml
  auth:
    platform: solaredge
    mode: api_key
    api_key: your-api-key
  ```

These modes skip the browser login step entirely and reduce authentication complexity.

## Troubleshooting

### "no cookie captured" or "no cached session cookie"
Run `python -m solaranalysis.tools.se_login` again to refresh the session cookie.

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
pytest tests/test_growatt_adapter.py -v
```

## License

(Add your license here if applicable.)
