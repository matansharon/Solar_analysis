# Daily Raw-Snapshot Persistence — Design

Date: 2026-07-23
Status: draft — pending user review

## 1. Purpose

Every web-app run already **fetches, analyzes, reports, emails, and persists**
(via `on_fetched=persist` → `measurements.save_measurements`). What is missing
for "save each day's data" is threefold:

1. **Nothing runs daily automatically** — no `schedules` row exists yet (the
   NextTODO lists "create the daily schedule" as pending), so no history
   accumulates on its own.
2. **A `snapshot` run writes no clean per-day energy value** — `energy_points`
   at `day` granularity is only populated from `PlantData.energy_timeseries`,
   which the adapters fill **only** for `30d/12mo/all`. A daily snapshot stores
   `energy_today` only inside the `plant_snapshots.kpis_json` blob.
3. **The raw portal JSON is discarded** — adapters map the portal payloads into
   `PlantData` and throw the originals away. We cannot re-derive a metric we
   didn't map without re-fetching (and yesterday's data is then gone).

This project makes the app keep a **full raw daily snapshot for all systems**
(4 SolarEdge sites + Growatt + SMA), so a real day-by-day history builds up and
any figure can be re-derived later without re-fetching.

### Chosen behavior (from brainstorming)

- **Scope:** "full raw snapshot daily" — persist everything each adapter fetches
  **plus the untouched portal JSON**, every day, for all enabled systems.
- **Cadence:** one automatic run per day.

### Non-goals

- **Changing the report/analysis/email path.** The daily run behaves exactly as
  today; this project only adds persistence and the schedule. (YAGNI.)
- **A clean daily-energy series for Growatt/SMA.** Those portals don't expose a
  complete-previous-day figure the way SolarEdge's `energyYesterday` does; their
  day series stays a future enhancement (re-derivable from the raw payloads or a
  lifetime-delta once history exists). SolarEdge gets the clean day point now.
- **A UI to browse raw payloads.** Storage only; querying is ad-hoc for now.

## 2. Current state (what already exists)

- `web/db.py` DDL (SCHEMA_VERSION = 4): `plant_snapshots`, `energy_points`
  (upsert on `(plant_uid, granularity, period)`), `device_snapshots`,
  `alert_snapshots`, `power_points`. `init_db` is additive-only and runs on every
  startup, so an update-deploy migrates automatically.
- `core/measurements.save_measurements(conn, plants, time_range, run_id)` persists
  a snapshot row per plant, upserts energy points from `pd.energy_timeseries`, and
  appends device/alert rows. Called from `web/runner.py`'s `persist` callback;
  persistence failure is non-fatal (logged as a note).
- `web/scheduler.py` (APScheduler): `schedules` rows → cron jobs firing
  `run_manager.start_run("scheduled", time_range)`.
- `adapters/_browser.py`: `BrowserSession` already tees every response through
  `capture(fragments)`; direct calls go through `get_json`/`post_json`.
- SolarEdge `sitesMeasurements` already returns `energyYesterday` (see
  `.discovery/solaredge/optimizer-spike/026.json` request body), but
  `map_solaredge_fleet` currently reads only today/month/year/lifetime.

## 3. Design

### A1 · Daily schedule (operational, ~no code)

Create one `schedules` row via the existing Settings/Schedules UI:
`time_of_day` ≈ `06:00`, all days, `time_range = snapshot`. The scheduler already
turns this into a daily cron job. No new code — this is a deploy/ops step,
captured here so it isn't forgotten. (A `snapshot` range keeps the daily run fast;
A3 supplies the clean day energy point that `snapshot` otherwise lacks.)

### A2 · Raw-payload capture + storage

**New table** (`web/db.py` DDL, additive; bump `SCHEMA_VERSION` → 5):

```sql
CREATE TABLE IF NOT EXISTS raw_payloads(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                 -- NULL for CLI runs
  config_plant_id INTEGER,
  plant_uid TEXT NOT NULL,        -- PlantData.plant_id (e.g. 'solaredge-2387929')
  platform TEXT NOT NULL,
  endpoint_label TEXT NOT NULL,   -- short tag, e.g. 'sitesMeasurements'
  url TEXT,
  method TEXT,
  status INTEGER,
  fetched_at_utc TEXT NOT NULL,
  payload_zjson BLOB NOT NULL     -- zlib-compressed UTF-8 JSON
);
CREATE INDEX IF NOT EXISTS ix_raw_payloads_plant
  ON raw_payloads(plant_uid, fetched_at_utc);
```

**Capture mechanism.** Add an opt-in raw recorder to `BrowserSession`:
`bs.start_raw_capture()` registers a `response` listener that appends
`{url, method, status, body}` for every JSON/text response to an in-memory list
(reusing the same skip/JSON logic as `tools/discover.py`), and `bs.get_json` /
`bs.post_json` append their results too. `bs.raw_records()` returns the list.
Adapters opt in only when a `record_raw` flag is set on the fetch, so normal CLI
runs pay nothing.

**Plumbing.** `SolarPortalAdapter.fetch` gains no signature change; instead each
adapter, when `self.record_raw` is set (set by the runner before `fetch`),
attaches its raw records onto the returned `PlantData` list via a new
`PlantData.raw_payloads: list[RawPayload]` field (default empty). `PlantData` is
already the transport between fetch and persist.

**Persist.** `save_measurements` gains a loop that compresses each
`RawPayload.body` (`zlib.compress(json.dumps(...).encode())`) and inserts a
`raw_payloads` row. Wrapped by the existing non-fatal `persist` try/except.

**Volume.** Per site/day the fleet dashboard JSONs are small (≈5–40 KB each);
compressed, a full fleet day is well under ~1 MB. A year ≈ low hundreds of MB in
`app.db` — acceptable for SQLite/WAL. A retention prune (delete rows older than N
days) is a trivial future add; out of scope now.

### A3 · Clean daily energy point (SolarEdge)

Extend `map_solaredge_fleet` to read `energyYesterday` and emit one
`EnergyPoint(period=<yesterday YYYY-MM-DD>, energy_kwh, granularity="day")` onto
`pd.energy_timeseries`. `save_measurements` already upserts `energy_timeseries`
into `energy_points`, so a tidy per-site daily kWh series accumulates from the
daily `snapshot` runs — no run-manager change. Yesterday is a **complete** day,
so the value never needs self-correction (unlike `energy_today`).

Growatt/SMA: unchanged (see non-goals).

## 4. Migration & compatibility

- DDL is additive; `init_db` `executescript` + guarded `ALTER` pattern already in
  place. Bump `SCHEMA_VERSION` to 5. An update-deploy migrates on startup with no
  manual step (same guarantee as the per-system-run v3→v4 change).
- CLI (`solaranalysis.cli`) is unaffected: `record_raw` defaults off, and it does
  not use the web DB. Existing runs, reports, and emails are byte-for-byte
  unchanged.

## 5. Testing

- `RawPayload` compress/round-trip and `save_measurements` raw-insert (in-memory
  SQLite), including the non-fatal path when a body isn't JSON-serializable.
- `map_solaredge_fleet` emits the `energyYesterday` day point with the correct
  `period` (yesterday) and kWh, and omits it when `energyYesterday` is absent.
- Schema migration: opening a v4 DB adds `raw_payloads` and reports version 5.
- Existing `save_measurements`/adapter tests stay green (no signature changes on
  the happy path).

## 6. Rollout

1. Ship A2 + A3 (code), verify tests + a manual snapshot run persists raw rows
   and a yesterday energy point.
2. On the server, create the daily schedule (A1) via the UI.
3. Confirm the next day's automatic run persisted a new raw snapshot + day point.
