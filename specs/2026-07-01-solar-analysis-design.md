# Solar Analysis — Design Spec

- **Date:** 2026-07-01
- **Status:** Draft (awaiting user review)
- **Location:** `AI/solar-analysis/` (new sibling of `AI/firecrawl/`; same git repo rooted at `AI/`)
- **Author:** Matan + Claude (brainstormed via superpowers:brainstorming)

## 1. Goal

Log into three solar-monitoring portals with the owner's own credentials, pull each
plant's data, normalize it into one cross-vendor schema, and have Claude produce a single
**AI analysis report** comparing the plants across four angles: **production & performance,
health & faults, financial/savings, anomalies & recommendations**. Deliverable is a
**styled, self-contained HTML report**.

This generalizes the `firecrawl/` pattern ("log in → extract → hand to Claude") from
password-protected gift catalogs to authenticated solar dashboards, but the extraction
mechanics and the analysis layer are substantially different.

## 2. Scope

**Phase 1 (this spec, build now):**
- Shared framework: adapter interface, normalized schema, normalization/validation,
  rate-limit/session cache, AI analysis layer, HTML report.
- **SolarEdge** adapter (email/password path).
- **Growatt** adapter (email/password path).
- End-to-end: 2 plants → normalized data → Claude → HTML report.

**Phase 2 (later, separate effort):**
- **SMA Sunny Portal** adapter. It is 3–5x harder (see §7.3) and is deliberately deferred
  so it does not gate the two easy wins. The adapter interface and schema are designed now
  so SMA drops in as one more adapter.

**Auth decision:** start with **email/password** (the credentials already provided). Design
stays **token-ready** — SolarEdge `api_key` and Growatt OpenAPI token paths are first-class
config options that can be dropped in later for higher reliability (no lockouts, no bot
challenges).

**Runtime environment:** runs on the user's **own Windows machine (residential IP)**. This
is the reliable case — the datacenter-IP anti-bot fragility that plagues server deployments
largely does not apply. Headed-browser and manual-login-once fallbacks are acceptable.

## 3. Non-goals

- Not a real-time monitoring service or dashboard; it is an on-demand report generator.
- Not unattended datacenter/cron operation (Phase 1). Anti-bot reality makes that unreliable
  for SolarEdge/SMA; revisit only with token/api_key auth.
- No control actions (never write settings to inverters). Read-only.
- No local-Modbus/LAN extraction (out of scope; noted as a future robustness option).
- Claude does **not** compute numbers — see §10.

## 4. Architecture

```
solar-analysis/
  cli.py                 # entrypoint: solar-analysis --range 30d --plants all
  config.py              # loads .env + config.yaml; per-plant auth config
  adapters/
    base.py              # SolarPortalAdapter ABC (the common interface)
    solaredge.py         # official API + one-time Playwright cookie-harvest fallback
    growatt.py           # growattServer wrapper (no browser)
    sma.py               # Phase 2 stub: Playwright Keycloak SSO -> requests
  core/
    schema.py            # PlantData dataclasses (normalized, typed, nullable)
    normalize.py         # unit conversion + rollups + derived metrics + sanity gates
    session_store.py     # persist/reuse cookies/tokens; rate-limit + backoff guard
    analyze.py           # build prompt, call Claude, return report markdown/JSON
    report.py            # render analysis -> styled self-contained HTML
  prompts/
    system.txt           # frozen, cacheable system prompt (grounding contract)
  output/                # git-ignored: <run-id>/report.html (+ intermediate data)
  tests/
  specs/                 # this file (repo ignores docs/ globally)
  .env / .env.example    # secrets (git-ignored)
  requirements.txt
  README.md
```

**Data flow:** `cli → config → for each plant: adapter.login(); adapter.fetch(range) →
PlantData` → `normalize.rollup()` (Python does ALL math) → `analyze.run()` (Claude
synthesizes) → `report.render()` → `output/<run-id>/report.html`.

## 5. Adapter interface (`adapters/base.py`)

Every platform hides behind one interface so the core is platform-agnostic and a new
platform is one new file:

```python
class SolarPortalAdapter(ABC):
    platform: str                       # "solaredge" | "growatt" | "sma"

    def __init__(self, auth: AuthConfig, session_store: SessionStore): ...

    @abstractmethod
    def login(self) -> None:
        """Establish an authenticated session; reuse cached cookies/tokens when valid."""

    @abstractmethod
    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        """Return one PlantData per plant/site on the account for the given range."""
```

- `TimeRange`: `snapshot | last_30d | last_12mo | all` (user-configurable, §1 requirement).
- Adapters return **raw-but-typed** data; they do **not** do cross-vendor normalization —
  that is `core/normalize.py`'s job, so conversion rules live in one place.
- `login()` and `fetch()` must be idempotent w.r.t. the session cache (never re-login when a
  cached session is still valid).

## 6. Normalized schema (`core/schema.py`)

A thin normalized core. Every native value is preserved verbatim in `source_*` fields;
every computed value is tagged `is_derived=True`; every field is explicitly nullable with a
`data_source_status` note (`ok` | `not_exposed` | `not_configured` | `comms_gap`) so the
LLM never reads absence as zero.

**Plant identity/metadata:** `plant_id` (our surrogate key), `source_platform`,
`source_plant_id`, `plant_name`, `peak_power_kwp`, `location_*`, `latitude`, `longitude`,
`timezone` (IANA), `install_date`, `currency`, `reporting_timestamp` (UTC + site tz).

**Production:** `energy_today_kwh`, `energy_month_kwh`, `energy_year_kwh`,
`energy_lifetime_kwh`, `current_power_kw`, `energy_timeseries[]`
(`{timestamp_local, energy_kwh, granularity}`), `power_timeseries[]`
(`{timestamp_local, power_kw}` + native resolution).

**Derived KPIs (`is_derived=True`):** `specific_yield_kwh_per_kwp` (the key apples-to-apples
metric — neutralizes plant-size differences), `performance_ratio` (only where irradiance
available; else null), `uptime_pct`, `capacity_factor`.

**Devices[]:** `device_id` (serial = natural key), `device_type` (enum), `device_model`,
`device_manufacturer`, `device_status` (enum: online/offline/standby/fault/unknown),
`device_current_power_kw`, `device_energy_lifetime_kwh`, `device_temperature_c`,
`device_last_seen`.

**Alerts[]:** `alert_id`, `alert_severity` (info/warning/error/critical), `alert_code`,
`alert_message`, `alert_timestamp`, `alert_resolved`.

**Financial/environmental:** `revenue`, `savings`, `co2_avoided_kg`, `trees_equivalent`
(each native value + currency kept; cross-plant comparison only after FX/factor
normalization, flagged approximate).

## 7. Per-platform extraction strategy

The single most important design decision (validated by adversarial review): **one common
interface, but a different extraction strategy per platform — cleanest-auth-first, browser
only where structurally unavoidable.** A uniform "Playwright-first" default was rejected
because 2026 Cloudflare anti-bot flags headless browsers by TLS/JA4 fingerprint + IP
reputation regardless of fidelity.

### 7.1 Growatt — pure `requests`, no browser
- Depend on **`growattServer` 2.2.0** (maintained; PyPI `growattServer`,
  indykoning/PyPi_GrowattServer).
- **Classic email/password path (Phase 1):** `GrowattApi.login(user, password)` — the lib
  hashes the password (MD5 + leading-zero-nibble→`c` fix via `hash_password()`), sends a
  mobile User-Agent, holds a `requests.Session` cookie. Endpoints:
  `newTwoLoginAPI.do`, `PlantListAPI.do`, `PlantDetailAPI.do`, `newInverterAPI.do`,
  `newTlxApi.do`, `newMixApi.do`, device list + per-device detail.
- **Token path (token-ready):** `OpenApiV1(token=...)` — self-serve token from ShinePhone
  app; relaxed limits, no login = no lockout/2FA risk. Covers MIN/SPH devices.
- **Rate limits:** classic API can lock the account ~24h if over-polled → poll ≥5 min, cache
  session, never re-login per request. History endpoints cap at 7-day windows → paginate.
- **Region:** `server.growatt.com` (EU/global). US accounts use `server-us.growatt.com`.
- **Gotchas:** `eTotal` is lifetime (no calendar-year total → derive by summing months);
  lat/lon often empty; status is integer-coded → map to enum; some fault text localized.

### 7.2 SolarEdge — official API first, browser as bootstrap only
- **Best path (token-ready):** official REST API `monitoringapi.solaredge.com` with
  `?api_key=` (clean, `requests`, no JS challenge). Use `solaredge` PyPI lib (v1.1.1, 2025).
  **Caveat:** since ~Mar-2025 consumer accounts often **cannot self-generate** the key (must
  ask installer/support). So this is opt-in, not assumed.
- **Phase-1 email/password path:** the internal login is Cloudflare-fronted (bare `requests`
  POST to `/solaredge-apigw/api/login` returns 403). So: **one-time headed Playwright login**
  (Spring-Security form: `j_username`/`j_password`) on the user's machine → harvest the
  **~20-day session cookie** → replay internal JSON endpoints with `requests.Session`.
  Persist the cookie; only re-drive the browser monthly when it expires.
- **Endpoints:** official `/site/{id}/overview` (best single call: current power + all energy
  buckets + revenue), `/site/{id}/energy`, `/site/{id}/energyDetails`, `/equipment/{id}/list`,
  `/equipment/{siteId}/{sn}/data`. Internal `/solaredge-web/p/chartData` (epoch-ms
  timestamps, per-device `reporterId`).
- **Rate limits (official):** 300 req/day per token+site, max 3 concurrent (429), per-endpoint
  time-window caps (403) → prefer `/overview`, batch bulk `/sites/{ids}/...`, chunk backfills.
- **Data gaps:** official API exposes **no** alerts/faults and **no** CO2 — those are
  internal-SPA/web-UI only. So Health/Financial sections degrade gracefully to "not reported"
  on the official path; the cookie path can reach them. All energy/power in **W/Wh → ÷1000**.
- **Endpoint drift:** portal mid-migration (legacy `/solaredge-web/p/` → Angular `/one#/`
  apigw). Discover internal paths via Playwright network interception; don't hardcode.

### 7.3 SMA Sunny Portal — Phase 2 (its own mini-project)
- Both portals delegate to **Keycloak SSO** (`login.sma.energy`, realm `SMA`). The old
  `sunnyportal-py` is **archived/dead** (no maintained cloud client exists).
- Two OIDC clients: **Classic** (`SunnyPortalClassic`, no PKCE, embedded client_secret,
  ASP.NET session cookie) vs **ennexOS** (`SPpbeOS`, S256 PKCE, Bearer + ~300s tokens).
  Plants migrate Classic→ennexOS per-plant → runtime-detect which portal a plant lives on.
- Strategy: **Playwright drives the Keycloak dance once** (dismiss cookie-consent; handle
  per-session `session_code`/`execution`/`tab_id`/`client_data`), capture Bearer+refresh
  (ennexOS) or session cookie (Classic), then hand off to `requests`. Refresh the token
  rather than re-driving the browser.
- Build the adapter to the same interface; expect breakage on SMA redesigns; document
  "may require manual-login-once."

## 8. Normalization & validation (`core/normalize.py`)

- **Units — field-by-field, never global:** SolarEdge is all watts; Growatt mixes W (power) +
  kWh (energy); SMA varies per endpoint. Convert every W→kW and Wh→kWh at ingest.
- **Derived metrics computed in Python** and tagged `is_derived`: `specific_yield`,
  `performance_ratio` (where possible), `uptime`, period deltas, savings.
- **Sanity gates** (emit `data_quality_flags`, do not silently trust): no negative energy,
  no night-time power, PR ∈ [0,1], `kWh ≤ kWp × hours`, monotonic lifetime energy.
- **Explicit nulls with status** (`not_exposed` vs `not_configured` vs `comms_gap`) so the
  report says "not reported" instead of implying a fault-free/zero site.
- **Enums:** map each vendor's status/severity vocabulary to the common enums.
- **Rollups by range:** snapshot = latest + today; 30d = daily; 12mo = monthly (+ "worst 5
  days" appendix); all = monthly + yearly + notable-events list. Over-aggregation is
  countered by always shipping a "worst N periods" appendix so a bad day isn't smoothed away.

## 9. Config & secrets

- `.env` (git-ignored, mirrors `firecrawl/.env` pattern) holds per-plant credentials and
  `ANTHROPIC_API_KEY`. `.env.example` documents keys without values.
- `config.yaml` (or CLI flags): plant list, auth mode per plant (`password` | `api_key` |
  `token`), tariff + currency per plant (for financial section), model override, input-token
  cap, output language.
- Credentials provided are stored **only** in `.env`; never committed, never logged.

## 10. AI analysis layer (`core/analyze.py`)

**Division of labor (anti-hallucination core):** Python owns all normalization, unit
conversion, rollups, arithmetic, and validation. **Claude only synthesizes** — it quotes and
compares numbers, never computes them.

- **Model:** default **`claude-sonnet-5`** at `effort: high`; **`claude-opus-4-8`** for
  `all` / `12mo` or anomaly-heavy runs. Runtime config keyed to `time_range` (one-line switch;
  identical request surface). *(Verify current model IDs/pricing via the `claude-api` skill at
  implementation time.)*
- **System prompt (frozen, cache-controlled):** role = solar-PV fleet analyst; the four
  `##` sections verbatim and in order; the **grounding contract** — every figure must come
  from the DATA block or be arithmetically derived from it; missing = "not reported" (never a
  cross-plant average); show source numbers inline for any derived value; kWh (energy) vs kW
  (power) are never interchangeable; timestamps are pre-normalized (do not convert).
- **User message = serialized DATA block:** hybrid format — compact JSON for
  metadata/summary + **CSV** for time-series rollup tables (dense, one header, authoritative,
  ~3–5x fewer tokens than per-row JSON). Identical column order across plants; explicit
  nulls; numbers pre-rounded in Python; deterministic key order (cache-stable).
- **Token control:** pre-aggregate before sending (365 daily → 12 monthly rows cuts ~97%);
  `count_tokens` pre-flight gate with a configured input cap → auto-coarsen rollup instead of
  silent truncation; prompt-cache the frozen system prefix (~0.1x on re-runs); stream with
  `max_tokens` ~16–32K. Est. 12-month report ≈ 8–15K in / 3–5K out (< $0.10).
- **Output contract:** report as markdown (four sections + per-section comparison table +
  flagged anomalies + recommendations). This markdown is the input to §11.

## 11. Output — styled HTML report (`core/report.py`)

- Primary (and only requested) deliverable: a **self-contained, styled HTML file** —
  `output/<run-id>/report.html` — following the look of the existing
  `firecrawl/catalog-comparison-he.html`.
- Render the Claude markdown → HTML with inline CSS (no external assets), a header (run date,
  range, plants compared), the four analysis sections, comparison tables, and a
  data-quality/caveats footer (which fields were "not reported" and why).
- Keep the intermediate Claude markdown + normalized data on disk for debugging (not a
  user-facing deliverable).

## 12. Rate-limiting & session persistence (`core/session_store.py`)

Shared, not per-adapter: persist cookies/tokens to disk (encrypted-at-rest or user-profile
scoped), reuse until expiry, central backoff/retry, per-platform min-poll interval (Growatt
≥5 min). This is the primary defense against account lockout and repeated bot-challenge
exposure.

## 13. Error handling & data quality

- Adapter failures are isolated: one plant failing to fetch still produces a report for the
  others, with that plant marked unavailable (reason surfaced).
- Auth expiry → attempt cached-session reuse → re-login → if OTP/captcha appears, enter an
  explicit "manual intervention required" state (headed browser) rather than failing silently.
- `data_quality_flags` from §8 are surfaced in the report's Anomalies section.

## 14. Testing strategy

- **Unit:** `normalize.py` conversions (W→kW, Wh→kWh, derived KPIs), sanity gates, enum
  mapping, rollups — pure functions, fixture-driven (recorded vendor JSON).
- **Adapter contract tests:** each adapter maps a recorded fixture payload → valid PlantData
  (no live network in CI). Live smoke tests are opt-in/manual (real credentials).
- **Analyze:** a numeric-verification post-step — every number in Claude's output must be
  present in / derivable from the DATA block; flag any that aren't.
- **Report:** golden-file HTML render from a fixed PlantData fixture.

## 15. Risks & mitigations (top)

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Cloudflare blocks SolarEdge browser login | High | Run on residential IP (user's machine); prefer api_key; harvest cookie once; stealth browser + manual fallback |
| Account lockout from over-polling | High | Session cache, ≥5-min poll, prefer `/overview`, central backoff |
| Silent unit/normalization corruption → confident-but-wrong report | High | All math in Python; sanity gates; per-plant unit map; `data_quality_flags` surfaced |
| Claude hallucinates figures | High | Grounding contract; Python does arithmetic; numeric-verification post-step |
| Internal-endpoint drift (SolarEdge/SMA) | Medium | Prefer official endpoints; discover internal paths at runtime; defensive parsing; pin lib versions |
| SMA scope blows up | Medium | Deferred to Phase 2 as its own effort; interface designed for drop-in |

## 16. Open questions / future

- Tariff + currency per plant: need the user's actual feed-in/import tariffs for the
  financial section (else it reports energy only). Confirm at implementation.
- Upgrade to token/api_key auth for reliability once the user obtains them.
- Optional: scheduled runs + Batches API (50% cheaper) once auth is token-based.
- Optional: local-Modbus mode for maximum robustness if run on the plant LAN.
