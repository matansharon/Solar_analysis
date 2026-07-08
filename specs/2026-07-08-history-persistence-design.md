# Device/Alert/Power History Persistence — Design

Date: 2026-07-08
Status: draft — pending user review

## 1. Purpose

`8d0ca80` (2026-07-07) added deep-fetch (history, devices, alerts) to all
three adapters and started persisting fetched data to SQLite: `plant_snapshots`
(one row per plant per fetch, full KPI JSON) and `energy_points` (upserted
energy time series). `PlantData.devices`, `PlantData.alerts`, and
`PlantData.power_timeseries` are already populated by every adapter but are
either buried inside the `plant_snapshots.kpis_json` blob (devices, alerts —
not independently queryable) or dropped entirely before persistence
(power_timeseries).

This project:

1. Makes devices and alerts independently queryable (their own tables, not a
   JSON blob), as an append-only history.
2. Persists `power_timeseries`, matching how `energy_timeseries` is already
   persisted.
3. Exposes all of the above (plus the already-persisted but unread
   `energy_points`) through a read API and a new Plant Detail page in the web
   UI.

### Non-goals

- Raw portal payload capture (the actual JSON each portal endpoint returns,
  before normalization) — different tradeoffs (storage growth, retention
  policy, opt-in vs always-on) that deserve their own design; tracked as a
  follow-up.
- Retention/pruning of any history table — matches the existing convention
  (`plant_snapshots`/`energy_points` already grow unbounded).
- A per-device drill-down timeline in the UI (e.g. one inverter's status over
  time) — v1 shows current device status plus fleet-level charts only.
- Backfilling `config_plant_id` (§2) onto rows written before this change —
  they stay `NULL` and simply won't appear on the new Plant Detail page.

## 2. Linking snapshot data to a web-managed plant

`plant_snapshots`/`energy_points` key on `plant_uid` (the *portal's own* site
id, e.g. `growatt-10950561`, assigned by the adapter from the fetched data) —
not the web app's `plants.id` (the credential row a user edits on the Plants
page). One credential can fan out to multiple portal sites (`adapter.fetch()`
returns `list[PlantData]`), so there is currently no stored, reliable way to
answer "which rows belong to credential #3" — a prerequisite for the Plant
Detail page.

Fix: thread the web DB's plant id through the pipeline as an opaque
correlation id, the same way `fetched_at_utc` is already stamped onto
`PlantData` after fetch.

- `config.py`: add `PlantConfig.config_id: int | None = None`. CLI's
  `config.load_config()` never sets it (stays `None`); `web/runner.py`'s
  `build_app_config()` sets it from `repo.list_plants()`'s `id`.
- `pipeline.py`: in the fetch loop, alongside `pd.fetched_at_utc = fetched_at`,
  add `pd.config_plant_id = pc.config_id`.
- `core/schema.py`: add `PlantData.config_plant_id: int | None = None`.

This is additive and has zero effect on the CLI path or existing tests that
don't pass a `config_id`.

## 3. Schema v3 (`web/db.py`, additive — `CREATE TABLE IF NOT EXISTS`)

```sql
CREATE TABLE IF NOT EXISTS device_snapshots(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                      -- NULL for CLI runs
  config_plant_id INTEGER,             -- NULL if not fetched via the web app
  plant_uid TEXT NOT NULL,
  device_id TEXT NOT NULL,
  device_type TEXT NOT NULL,
  model TEXT,
  manufacturer TEXT,
  status TEXT NOT NULL,
  current_power_kw REAL,
  energy_lifetime_kwh REAL,
  temperature_c REAL,
  last_seen_local TEXT,
  fetched_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_device_snapshots_plant
  ON device_snapshots(config_plant_id, device_id, fetched_at_utc);

CREATE TABLE IF NOT EXISTS alert_snapshots(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,
  config_plant_id INTEGER,
  plant_uid TEXT NOT NULL,
  alert_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  code TEXT,
  message TEXT,
  timestamp_local TEXT,
  resolved INTEGER,
  fetched_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alert_snapshots_plant
  ON alert_snapshots(config_plant_id, fetched_at_utc);

CREATE TABLE IF NOT EXISTS power_points(
  plant_uid TEXT NOT NULL,
  config_plant_id INTEGER,
  timestamp_local TEXT NOT NULL,
  power_kw REAL,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (plant_uid, timestamp_local)
) WITHOUT ROWID;
```

Plus an explicit `ALTER TABLE ... ADD COLUMN config_plant_id INTEGER` guard
(keyed off `settings.schema_version`, per the migration policy already
documented in `db.py`) for the existing `plant_snapshots` and `energy_points`
tables, so the Plant Detail page can also chart the energy history that's
already accumulating there. `SCHEMA_VERSION` becomes `3`.

Devices and alerts are **append-only**: one row per device/alert per fetch, so
status changes and alert occurrences over time stay queryable (consistent with
`plant_snapshots`, which already appends one row per plant per fetch).
`power_points` **upserts** keyed on `(plant_uid, timestamp_local)`, same
latest-wins pattern as `energy_points`, since a re-fetch of the same window
should self-correct rather than duplicate.

## 4. Persistence wiring (`core/measurements.py`)

Extend `save_measurements(conn, plants, time_range, run_id)` to also write, per
plant, from data already present on `PlantData` (no adapter changes needed):

- `pd.devices` → one `device_snapshots` insert per device.
- `pd.alerts` → one `alert_snapshots` insert per alert.
- `pd.power_timeseries` → upsert into `power_points`.

New loaders alongside the existing `load_series`:

- `load_devices_latest(conn, config_plant_id)` — most recent row per
  `device_id`.
- `load_alerts(conn, config_plant_id, limit=100)` — newest first.
- `load_power_series(conn, config_plant_id, since=None)` — ordered by
  `timestamp_local`.

Both the CLI (`cli.py`'s `on_fetched`) and the web runner
(`web/runner.py`'s `persist`) already call `save_measurements()` before
analysis — no change needed to either call site beyond what schema/pipeline
changes provide automatically.

## 5. Read API (`web/routes/plant_history.py`, mounted under `/api/plants`)

- `GET /api/plants/{pid}/devices` — latest known status per device.
- `GET /api/plants/{pid}/alerts?limit=100` — recent alerts, newest first.
- `GET /api/plants/{pid}/power?since=` — power time series.
- `GET /api/plants/{pid}/energy?since=` — energy time series (reuses the
  already-persisted `energy_points` via the existing `load_series`, now
  filterable by `config_plant_id`).

All four 404 if `pid` doesn't exist (matching `GET /api/plants/{pid}` today).
An empty result set (plant exists but has never been fetched, or predates this
change) is a normal `200` with an empty list, not a 404.

## 6. Frontend

New route `/plants/:id` (`PlantDetail.tsx`):

- Header: plant name, platform badge, enabled/last-test status (reuse
  existing bits from `Plants.tsx`).
- Devices table: id/model, status, current power, last seen.
- Recent alerts list: severity, message, timestamp.
- Energy and power line charts. The frontend has no charting dependency
  today and stays minimal elsewhere (plain CSS, no component library), so
  these are a small hand-rolled inline SVG line-chart component rather than
  pulling in a charting library. If richer interaction is wanted later, a
  library (e.g. Recharts) is the natural upgrade path.

`Plants.tsx`: plant name becomes a `<Link to={`/plants/${p.id}`}>`.
`App.tsx`: add the `/plants/:id` route.
`api.ts`: add `plantDevices`, `plantAlerts`, `plantPower`, `plantEnergy`
client methods and their response types.

## 7. Testing

- `tests/test_pipeline.py` — `config_plant_id` flows from `PlantConfig` to
  `PlantData`.
- `tests/web/test_measurements.py` (already exists, covering the current
  `plant_snapshots`/`energy_points` writers) — extend with cases for the new
  device/alert/power writers and loaders; upsert-vs-append behavior.
- `tests/web/test_api_plants.py` — new endpoint tests: 404 on unknown plant,
  empty list on a never-fetched plant, correct shape once data exists.
- Frontend: no existing test suite convention beyond `npm run build`
  type-checking; match that.
