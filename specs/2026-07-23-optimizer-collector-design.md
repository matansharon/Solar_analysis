# SolarEdge Per-Optimizer Collector & Anomaly Report — Design

Date: 2026-07-23
Status: draft — pending user review

## 1. Purpose

The SolarEdge Digital-Twin page
(`monitoring.solaredge.com/one#/commercial/digital-twin?siteId=…`) shows, per
optimizer, that panel's energy for a day (e.g. `Optimizer 1.4.5 → 5601 Wh`). This
is the finest-grained health signal SolarEdge exposes — a single dead, shaded, or
degrading panel is invisible at the site/inverter level but obvious per optimizer.

This project builds a **standalone daily collector** that, across **all 4 sites**
on the account, pulls **every optimizer's daily energy**, stores it as an
accumulating series, **analyzes** it for underperformers and degradation, and
emails a **per-optimizer anomaly report**.

### Chosen behavior (from brainstorming)

- **Standalone** (Approach B): its own module + CLI entry point, **not** wired
  into the fleet analysis pipeline. It reuses the SolarEdge login/session cache
  and the Graph mailer, but runs and is scheduled independently.
- **Storage:** shared `app.db` (new tables) — one file to back up; the web UI can
  surface per-panel data later. "Standalone" applies to code + schedule, not data.
- **Analysis:** find underperformers **and** trend over time, delivered as a
  **written daily anomaly report/email** (the "full anomaly report" option).
- **Backfill:** ~90 days on first run, then daily incremental.
- **Delivery:** its own dedicated daily email, separate from the fleet report.

### Non-goals

- **Intraday per-optimizer power** (the hourly `energy-graph` endpoint) — daily
  energy only for now. Future extension.
- **Physical-layout visualization** — we store positions but don't render the
  panel map. Future.
- **Growatt/SMA per-panel** — no comparable per-optimizer API; SolarEdge only.
- **Remote commands** — the layout API exposes inverter commands; strictly
  read-only here.

## 2. Discovered SolarEdge Layout API (verified live 2026-07-23)

Authenticated GETs on `https://monitoring.solaredge.com`, reusing the existing
headless-browser session (cookies shared via `context.request`). Fixtures live in
`.discovery/solaredge/optimizer-spike/` (git-ignored). Site `2387929` example:
3 inverters, **229 optimizers**, site day-total 1,314,469 Wh.

| Endpoint | Returns | Fixture |
|----------|---------|---------|
| `GET /services/layout/logical/generic/v2/site/{sid}?include-optimizers=true` | `siteStructure` tree: `SITE → INVERTER → STRING → OPTIMIZER`, with names ("Inverter 1", "String 1.0") and serials — the inventory + labels | `032.json` |
| `GET /services/layout/information/inverters?inverter-serials={csv}` | `basicInformationList` (manufacturer, `fullModel` e.g. `SE50K-IL00IBNQ4`), `serialToLiveData` (status, `pAc_W`, `acEnergyOnGrid_Wh`, `lastMeasurement`), per-phase measurements | `040.json` |
| `GET /services/layout/energy/site/{sid}/by-inverter?start-date={d}&end-date={d}&inverter-serials={csv}&include-color=true` | **per-optimizer daily energy** — `inverters[].optimizers[] = {serial, energy:{value: Wh}, temperature, color: 0..1}`, plus per-string and per-inverter `energy` | `037.json` |
| `GET /services/layout/energy/site/{sid}?start-date={d}&end-date={d}` | site day-total `{energy: Wh}` (cross-check) | `036.json` |
| `GET /services/layout/physical/site/{sid}` | physical panel positions/rectangles | `031.json` |
| `GET /services/layout/information/site/{sid}` | site-level layout info | `030.json` |

**Key facts.**
- `energy.value` is **watt-hours** for the requested date (single day when
  `start-date == end-date`). Past dates are accepted (enables backfill).
- **`color` (0–1) is SolarEdge's own peer-normalized performance value** — the
  heat-map the UI paints panels with. Healthy panels on 2387929 today are
  ~0.96–0.99; a dead/severely-shaded panel trends toward 0. This is the primary
  anomaly signal — SolarEdge has already done the peer normalization.
- `temperature` is often null on this hardware (optimizers here don't report it);
  store when present, never require it.
- The energy call needs the inverter-serial CSV, obtained from the logical tree.

**Site enumeration.** The account's 4 sites come from the existing
`sitelist/searchSites` (`solarFieldId`), already used by `SolarEdgeAdapter.fetch`.
Confirmed IDs: `2387929, 2257529, 3506413, 3136790` — but we enumerate
dynamically so the collector survives site add/remove.

## 3. Architecture

New package `solaranalysis/optimizers/` (pure-mapper core + thin IO shell,
mirroring the adapter/analyze split so the mappers are unit-testable without a
browser):

```
solaranalysis/optimizers/
├── __init__.py
├── layout_client.py   # authenticated GETs (reuses SolarEdgeAdapter session)
├── mappers.py         # pure: raw layout JSON -> OptimizerInventory / OptimizerEnergy
├── store.py           # DB read/write (shared app.db)
├── analyze.py         # pure: series -> anomalies/trends (Python computes figures)
├── report.py          # grounded HTML report; Claude narrates only
└── cli.py             # entry point: python -m solaranalysis.optimizers
```

Reuses: `adapters/solaredge.SolarEdgeAdapter` (login + `_browser` session +
session cache), `web/repo` + `web/crypto` (load the SolarEdge credentials from
`app.db`, same as `web/runner`), `web/mailer` (Graph send), `web/paths.Paths`
(`--data-dir`/`--app-dir` → db_path, key_path, session_cache_dir), and the
grounded-narrative pattern from `core/analyze` (Python computes every figure;
Claude writes prose only).

### Data flow (per daily run)

1. **Credentials + session:** load the SolarEdge plant auth from `app.db`
   (`repo.load_plant_auth`), build `SolarEdgeAdapter`, `login()`, reuse cached
   browser session.
2. **Enumerate sites** via `searchSites`.
3. **Per site:** GET logical tree → inventory + inverter serials; GET
   `information/inverters` → models/status; GET `energy/.../by-inverter` for the
   target day(s) → per-optimizer energy.
4. **Store** inventory (upsert) + per-optimizer daily energy (upsert).
5. **Analyze** the accumulated series → anomalies + trends.
6. **Report + email** a single per-optimizer anomaly digest across all sites.

## 4. Storage (shared `app.db`; additive DDL, bump `SCHEMA_VERSION` → 6, after Phase A's 5)

```sql
CREATE TABLE IF NOT EXISTS optimizers(
  site_id INTEGER NOT NULL,
  optimizer_serial TEXT NOT NULL,
  label TEXT,                     -- e.g. '1.4.5' (inverter.string.opt)
  inverter_serial TEXT,
  inverter_name TEXT,             -- 'Inverter 1'
  string_name TEXT,               -- 'String 1.4'
  module_manufacturer TEXT,
  module_model TEXT,
  tilt REAL,
  azimuth REAL,
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  PRIMARY KEY (site_id, optimizer_serial)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS optimizer_energy(
  site_id INTEGER NOT NULL,
  optimizer_serial TEXT NOT NULL,
  day TEXT NOT NULL,              -- 'YYYY-MM-DD' (local site day)
  energy_wh REAL,
  color REAL,                     -- SolarEdge normalized 0..1 (nullable)
  temperature_c REAL,             -- nullable
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (site_id, optimizer_serial, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_optenergy_day ON optimizer_energy(site_id, day);
```

Module `tilt`/`azimuth`/model may not appear in the logical tree; if the
selection-time `information` call is needed we discover it during implementation
(the fields are nullable, so absence never blocks the core energy series). The
`color` signal makes tilt/azimuth optional for v1 anomaly detection.

## 5. Analysis (pure, Python-computed)

Per site, over the accumulated series. All thresholds are constants at the top of
`analyze.py`, tunable later:

- **Dead / offline:** `energy_wh ≈ 0` (or `color ≈ 0`) for ≥ 2 consecutive recent
  days → highest severity.
- **Underperforming:** `color < 0.60` on ≥ 3 of the last 5 days, **or** daily
  energy < 70% of its **string median** on ≥ 3 of the last 5 days (string ≈ same
  orientation, so a fair peer group). Persistence over multiple days separates a
  real fault from a passing cloud.
- **Watch:** `color < 0.75` yesterday (single-day dip) — informational.
- **Degradation trend:** per-optimizer linear slope over the last ~30 days,
  normalized to its string; flag optimizers declining materially faster than
  their string peers.

Every reported number (energy, color, ratio-to-peer, slope) is computed here;
the report/email prose only restates them (no model-invented figures).

## 6. Report & email

`report.py` builds a grounded HTML digest: a fleet headline (N optimizers across
M sites, X flagged), then per site a ranked table of flagged optimizers — label,
S/N, yesterday energy, color, ratio-to-string-median, trend, severity. A single
Claude call (following `core/analyze`'s grounding contract; language from the
app's `output_language` setting) writes a short narrative; if it fails the digest
still sends with the tables (non-fatal, like the fleet summary). Emailed via
`web/mailer.send_report` with subject `SolarEdge Optimizers · {status} · {date}`
and its own recipient list (reuse `REPORT_RECIPIENTS`, overridable via
`OPTIMIZER_RECIPIENTS`).

## 7. CLI, backfill & scheduling

```
python -m solaranalysis.optimizers --data-dir <dir> --app-dir <dir>
    [--date YYYY-MM-DD]   # default: yesterday (last complete day)
    [--backfill N]        # pull N prior days if the series is empty/short
    [--no-email]          # store + analyze only
    [--sites 2387929,...] # default: all enumerated sites
```

- **Backfill:** on first run (empty `optimizer_energy`), fetch the last **90**
  days per site — one `by-inverter` call per site per day (≈360 calls total,
  one-time), with a small inter-call delay to be polite; the session cache keeps
  it to a single login. Subsequent runs fetch only `--date` (yesterday) and
  recompute analysis over the stored window.
- **Scheduling:** a separate daily task on the server (NSSM service or Windows
  Task Scheduler) invoking the CLI after the fleet run's window. Actual
  setup is an ops step (documented in DEPLOYMENT.md), not code.

## 8. Testing

- **Mappers (pure, fixture-driven):** `037.json` → per-optimizer energy list
  (229 optimizers, correct Wh/color); `032.json` → inventory tree flattened to
  `(serial, label, inverter, string)`; `040.json` → inverter model/status.
  Malformed/partial payloads degrade gracefully (skip, never crash).
- **Store:** inventory upsert (first/last-seen), energy upsert idempotency
  (re-running a day doesn't duplicate), in-memory SQLite.
- **Analyze:** synthetic series exercising each rule — dead panel, multi-day
  underperformer, single-day cloud dip (must *not* flag), degradation slope vs.
  a healthy string.
- **Report:** grounded-numbers check (every figure in the digest traces to the
  computed data); non-fatal skip when the Claude call raises.
- Live login/fetch validated by a real run (as with the other adapters).

## 9. Open items to confirm during implementation (low-risk)

- Exact past-date behavior of `energy/by-inverter` (assumed supported; verify
  with one probe before the 90-day backfill).
- Whether module tilt/azimuth/model come from the logical tree or a per-optimizer
  `information` call (nullable either way; does not block v1).
- Precise `siteStructure` field names for label/serial extraction (fixture `032`
  drives the mapper; confirmed shape `SITE→INVERTER→STRING→OPTIMIZER`).
