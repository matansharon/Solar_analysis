# Device/Alert/Power History Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist device status, alerts, and power readings to SQLite as queryable history (not just a JSON blob), link snapshot rows to a specific web-managed plant, and expose it all through a read API and a new Plant Detail page.

**Architecture:** Extend the existing `plant_snapshots`/`energy_points` persistence pattern (`core/measurements.py`, `web/db.py`) with three new tables and a `config_plant_id` correlation column threaded through the pipeline from the web app's `plants.id`. Add four read-only FastAPI routes and a new React page that renders them.

**Tech Stack:** Python 3.10, FastAPI, stdlib `sqlite3`, pytest; React 18 + TypeScript, react-router-dom, @tanstack/react-query, Vite. No new dependencies (backend or frontend).

## Global Constraints

- Spec: `specs/2026-07-08-history-persistence-design.md` — read it first; this plan implements it exactly.
- Schema changes are additive-only (`CREATE TABLE IF NOT EXISTS` / guarded `ALTER TABLE ... ADD COLUMN`) — this is documented policy in `web/db.py`'s module docstring.
- `config_plant_id` defaults to `None` everywhere (CLI path, `config.yaml` plants) — the CLI must be completely unaffected.
- Devices and alerts are **append-only** (one row per device/alert per fetch). `power_points` and the new `config_plant_id`-scoped energy query are **upsert, latest-wins**, keyed the same way `energy_points` already is.
- No retention/pruning of any table (matches existing `plant_snapshots`/`energy_points` behavior).
- No new npm dependency for charting — hand-rolled inline SVG.
- Every new/changed Python module keeps the existing `from __future__ import annotations` + type-hint style used throughout `solaranalysis/`.

---

## File Structure

| File | Change |
|---|---|
| `solaranalysis/config.py` | Modify — add `PlantConfig.config_id` |
| `solaranalysis/core/schema.py` | Modify — add `PlantData.config_plant_id` |
| `solaranalysis/pipeline.py` | Modify — stamp `config_plant_id` after fetch |
| `solaranalysis/web/runner.py` | Modify — `build_app_config` sets `config_id` |
| `solaranalysis/web/db.py` | Modify — schema v3 (3 new tables + 2 altered) |
| `solaranalysis/core/measurements.py` | Modify — device/alert/power writers + loaders |
| `solaranalysis/web/routes/plant_history.py` | Create — 4 read endpoints |
| `solaranalysis/web/app.py` | Modify — mount the new router |
| `tests/test_pipeline.py` | Modify — `config_plant_id` propagation tests |
| `tests/web/test_runner.py` | Modify — `build_app_config` sets `config_id` test |
| `tests/web/test_db.py` | Modify — v2→v3 migration test |
| `tests/web/test_measurements.py` | Modify — device/alert/power writer+loader tests |
| `tests/web/test_api_plant_history.py` | Create — endpoint tests |
| `frontend/src/api.ts` | Modify — types + client methods |
| `frontend/src/lineChart.tsx` | Create — hand-rolled SVG line chart |
| `frontend/src/routes/PlantDetail.tsx` | Create — new page |
| `frontend/src/App.tsx` | Modify — add `/plants/:id` route |
| `frontend/src/routes/Plants.tsx` | Modify — plant name becomes a link |
| `frontend/src/styles.css` | Modify — chart styles |

---

### Task 1: Thread `config_plant_id` from the web DB through the pipeline

**Files:**
- Modify: `solaranalysis/config.py`
- Modify: `solaranalysis/core/schema.py`
- Modify: `solaranalysis/pipeline.py:36-40`
- Modify: `solaranalysis/web/runner.py:19-33`
- Test: `tests/test_pipeline.py`, `tests/web/test_runner.py`

**Interfaces:**
- Produces: `PlantConfig.config_id: int | None` (default `None`); `PlantData.config_plant_id: int | None` (default `None`, set by `run_pipeline`).

- [ ] **Step 1: Write the failing pipeline test**

Add to `tests/test_pipeline.py` (after `test_pipeline_stamps_fetched_at`):

```python
def test_pipeline_stamps_config_plant_id(tmp_path):
    cfg = AppConfig(plants=[PlantConfig("A", AuthConfig("growatt", username="u", password="p"),
                                        config_id=42)])
    ss = SessionStore(str(tmp_path))
    def factory(auth, store): return FakeAdapter(_pd("A"))
    def analyzer(plants, tr, c, client=None): return "ok"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    assert res["plants"][0].config_plant_id == 42

def test_pipeline_config_plant_id_defaults_none(tmp_path):
    cfg = AppConfig(plants=[PlantConfig("A", AuthConfig("growatt", username="u", password="p"))])
    ss = SessionStore(str(tmp_path))
    def factory(auth, store): return FakeAdapter(_pd("A"))
    def analyzer(plants, tr, c, client=None): return "ok"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    assert res["plants"][0].config_plant_id is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_pipeline.py -k config_plant_id -v`
Expected: FAIL — `TypeError: PlantConfig.__init__() got an unexpected keyword argument 'config_id'` (or `AttributeError: 'PlantData' object has no attribute 'config_plant_id'`).

- [ ] **Step 3: Add `PlantConfig.config_id`**

In `solaranalysis/config.py`, change:

```python
@dataclass
class PlantConfig:
    name: str
    auth: AuthConfig
    tariff_per_kwh: float | None = None
    currency: str | None = None
```

to:

```python
@dataclass
class PlantConfig:
    name: str
    auth: AuthConfig
    tariff_per_kwh: float | None = None
    currency: str | None = None
    config_id: int | None = None  # web app's plants.id; None for CLI config.yaml plants
```

- [ ] **Step 4: Add `PlantData.config_plant_id`**

In `solaranalysis/core/schema.py`, change the "pipeline metadata" block:

```python
    # pipeline metadata
    fetched_at_utc: str | None = None  # when this run actually pulled the data
    data_quality_flags: list[str] = field(default_factory=list)
```

to:

```python
    # pipeline metadata
    fetched_at_utc: str | None = None  # when this run actually pulled the data
    config_plant_id: int | None = None  # web app's plants.id this fetch belongs to
    data_quality_flags: list[str] = field(default_factory=list)
```

- [ ] **Step 5: Stamp it in the pipeline**

In `solaranalysis/pipeline.py`, change:

```python
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                pd.fetched_at_utc = fetched_at
                plants.append(_normalize(pd, pc))
```

to:

```python
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                pd.fetched_at_utc = fetched_at
                pd.config_plant_id = pc.config_id
                plants.append(_normalize(pd, pc))
```

- [ ] **Step 6: Run to verify the pipeline tests pass**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 7: Write the failing runner test**

Add to `tests/web/test_runner.py` (after `test_build_app_config_from_db`):

```python
def test_build_app_config_sets_config_id(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    pid = repo.list_plants(conn)[0]["id"]
    cfg, _ = runner.build_app_config(conn, key)
    assert cfg.plants[0].config_id == pid
```

- [ ] **Step 8: Run to verify it fails**

Run: `python -m pytest tests/web/test_runner.py -k config_id -v`
Expected: FAIL — `AssertionError: assert None == <pid>`.

- [ ] **Step 9: Set `config_id` in `build_app_config`**

In `solaranalysis/web/runner.py`, change:

```python
        plants.append(PlantConfig(name=p["name"], auth=auth,
                                  tariff_per_kwh=p["tariff_per_kwh"],
                                  currency=p["currency"]))
```

to:

```python
        plants.append(PlantConfig(name=p["name"], auth=auth,
                                  tariff_per_kwh=p["tariff_per_kwh"],
                                  currency=p["currency"], config_id=p["id"]))
```

- [ ] **Step 10: Run to verify it passes**

Run: `python -m pytest tests/web/test_runner.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add solaranalysis/config.py solaranalysis/core/schema.py solaranalysis/pipeline.py \
        solaranalysis/web/runner.py tests/test_pipeline.py tests/web/test_runner.py
git commit -m "feat: thread config_plant_id from web DB through the pipeline"
```

---

### Task 2: Schema v3 — device/alert/power tables + `config_plant_id` columns

**Files:**
- Modify: `solaranalysis/web/db.py`
- Test: `tests/web/test_db.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: tables `device_snapshots`, `alert_snapshots`, `power_points`; `plant_snapshots.config_plant_id` and `energy_points.config_plant_id` columns; `db.SCHEMA_VERSION == 3`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/web/test_db.py` (after `test_v1_db_migrates_to_v2_on_init`):

```python
_V2_DDL = """
CREATE TABLE IF NOT EXISTS plants(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, platform TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS plant_snapshots(
  id INTEGER PRIMARY KEY, run_id INTEGER, plant_uid TEXT NOT NULL,
  source_platform TEXT NOT NULL, fetched_at_utc TEXT NOT NULL,
  time_range TEXT NOT NULL, kpis_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS energy_points(
  plant_uid TEXT NOT NULL, granularity TEXT NOT NULL, period TEXT NOT NULL,
  energy_kwh REAL, updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (plant_uid, granularity, period)
) WITHOUT ROWID;
"""


def test_v2_db_migrates_to_v3_on_init(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    c.executescript(_V2_DDL)
    c.execute("INSERT INTO settings(key,value) VALUES('schema_version','2')")
    c.commit()
    db.init_db(c)  # additive DDL + guarded ALTER picks up the new shape
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"device_snapshots", "alert_snapshots", "power_points"} <= names
    snap_cols = {r["name"] for r in c.execute("PRAGMA table_info(plant_snapshots)")}
    assert "config_plant_id" in snap_cols
    energy_cols = {r["name"] for r in c.execute("PRAGMA table_info(energy_points)")}
    assert "config_plant_id" in energy_cols
    ver = c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    assert ver["value"] == str(db.SCHEMA_VERSION)
```

Also update the existing `test_init_creates_tables` assertion set:

```python
def test_init_creates_tables(tmp_path):
    c = _conn(tmp_path)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plants", "settings", "schedules", "runs",
            "plant_snapshots", "energy_points",
            "device_snapshots", "alert_snapshots", "power_points"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/web/test_db.py -v`
Expected: FAIL — `test_init_creates_tables` and `test_v2_db_migrates_to_v3_on_init` both fail (missing tables/columns).

- [ ] **Step 3: Add the new tables and the guarded ALTER to `web/db.py`**

Change the top of the file to:

```python
from __future__ import annotations
import sqlite3

# Migration policy: the DDL below is additive-only (CREATE ... IF NOT EXISTS),
# and init_db executescripts it on every startup, so older DBs pick up new
# tables automatically. Column additions to existing tables use a guarded
# ALTER (see init_db) since CREATE TABLE IF NOT EXISTS can't add columns to
# an existing table.
SCHEMA_VERSION = 3
```

Append the three new tables to the end of the `_DDL` string (right after the existing `energy_points` block, before the closing `"""`):

```python
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

Change `init_db` from:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()
```

to:

```python
def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    for table in ("plant_snapshots", "energy_points"):
        if not _has_column(conn, table, "config_plant_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN config_plant_id INTEGER")
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/web/test_db.py -v`
Expected: PASS (all tests, including `test_init_is_idempotent` — `ALTER TABLE ADD COLUMN` is now guarded, so a second `init_db` call on an already-v3 DB doesn't raise `sqlite3.OperationalError: duplicate column name`).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/db.py tests/web/test_db.py
git commit -m "feat: schema v3 — device/alert/power tables, link snapshots to a web plant"
```

---

### Task 3: Persist devices and alerts (append-only writers + loaders)

**Files:**
- Modify: `solaranalysis/core/measurements.py`
- Test: `tests/web/test_measurements.py`

**Interfaces:**
- Consumes: schema from Task 2 (`device_snapshots`, `alert_snapshots`); `PlantData.config_plant_id`, `PlantData.devices: list[Device]`, `PlantData.alerts: list[Alert]` (already populated by every adapter).
- Produces: `measurements.load_devices_latest(conn, config_plant_id) -> list[dict]`; `measurements.load_alerts(conn, config_plant_id, limit=100) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Add to the top of `tests/web/test_measurements.py`, extend the import line:

```python
from solaranalysis.core.schema import (
    EnergyPoint, PlantData, Metric, TimeRange, Device, DeviceStatus, Alert, AlertSeverity,
)
```

Add these helpers and tests (anywhere after `_plant`):

```python
def _plant_with_device(status=DeviceStatus.ONLINE):
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="P")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = 5
    pd.devices = [Device(device_id="inv-1", status=status, model="MIN 3000",
                         current_power_kw=3.2)]
    return pd


def _plant_with_alert():
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="P")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = 5
    pd.alerts = [Alert(alert_id="A1", severity=AlertSeverity.ERROR,
                       message="Grid fault", timestamp_local="2026-07-07 09:00")]
    return pd


def test_device_snapshot_written():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_device()], TimeRange.SNAPSHOT, run_id=3)
    conn.commit()
    row = conn.execute("SELECT * FROM device_snapshots").fetchone()
    assert row["run_id"] == 3
    assert row["config_plant_id"] == 5
    assert row["device_id"] == "inv-1"
    assert row["status"] == "online"
    assert row["current_power_kw"] == 3.2


def test_device_history_appends_each_run_instead_of_overwriting():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_device(DeviceStatus.ONLINE)],
                                   TimeRange.SNAPSHOT, run_id=1)
    measurements.save_measurements(conn, [_plant_with_device(DeviceStatus.OFFLINE)],
                                   TimeRange.SNAPSHOT, run_id=2)
    conn.commit()
    rows = conn.execute("SELECT status FROM device_snapshots ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["online", "offline"]


def test_load_devices_latest_dedupes_to_most_recent_fetch():
    conn = _conn()
    older = _plant_with_device(DeviceStatus.ONLINE)
    older.fetched_at_utc = "2026-07-06T10:00:00+00:00"
    measurements.save_measurements(conn, [older], TimeRange.SNAPSHOT, run_id=1)
    newer = _plant_with_device(DeviceStatus.OFFLINE)
    newer.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    measurements.save_measurements(conn, [newer], TimeRange.SNAPSHOT, run_id=2)
    conn.commit()
    latest = measurements.load_devices_latest(conn, 5)
    assert len(latest) == 1
    assert latest[0]["status"] == "offline"


def test_alert_snapshot_written_and_loaded_newest_first():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_alert()], TimeRange.SNAPSHOT, run_id=1)
    conn.commit()
    alerts = measurements.load_alerts(conn, 5)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "error"
    assert alerts[0]["message"] == "Grid fault"


def test_load_alerts_respects_limit():
    conn = _conn()
    for i in range(3):
        pd = _plant_with_alert()
        pd.alerts[0].alert_id = f"A{i}"
        measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=i)
    conn.commit()
    assert len(measurements.load_alerts(conn, 5, limit=2)) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/web/test_measurements.py -k "device or alert" -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: device_snapshots` (writers don't exist yet) and `AttributeError: module 'measurements' has no attribute 'load_devices_latest'`.

- [ ] **Step 3: Add the writers to `core/measurements.py`**

After the existing `save_measurements` function's energy-points loop, before its closing (i.e. augment the function body) — change:

```python
def save_measurements(conn: sqlite3.Connection, plants: list[PlantData],
                      time_range: TimeRange, run_id: int | None) -> None:
    """One snapshot row per plant + upserted energy points. Caller commits."""
    now = _now_utc()
    for pd in plants:
        kpis = pd.to_dict()
        kpis.pop("energy_timeseries", None)
        kpis.pop("power_timeseries", None)
        conn.execute(
            "INSERT INTO plant_snapshots"
            "(run_id, plant_uid, source_platform, fetched_at_utc, time_range, kpis_json) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, pd.plant_id, pd.source_platform,
             pd.fetched_at_utc or now, time_range.value,
             json.dumps(kpis, ensure_ascii=False)))
        for p in pd.energy_timeseries:
            if p.energy_kwh is None:
                continue
            conn.execute(
                "INSERT INTO energy_points"
                "(plant_uid, granularity, period, energy_kwh, updated_at_utc) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(plant_uid, granularity, period) DO UPDATE SET "
                "energy_kwh=excluded.energy_kwh, "
                "updated_at_utc=excluded.updated_at_utc",
                (pd.plant_id, p.granularity, p.timestamp_local,
                 p.energy_kwh, now))
```

to:

```python
def save_measurements(conn: sqlite3.Connection, plants: list[PlantData],
                      time_range: TimeRange, run_id: int | None) -> None:
    """One snapshot row per plant + upserted energy points. Caller commits."""
    now = _now_utc()
    for pd in plants:
        kpis = pd.to_dict()
        kpis.pop("energy_timeseries", None)
        kpis.pop("power_timeseries", None)
        conn.execute(
            "INSERT INTO plant_snapshots"
            "(run_id, config_plant_id, plant_uid, source_platform, fetched_at_utc, "
            "time_range, kpis_json) VALUES (?,?,?,?,?,?,?)",
            (run_id, pd.config_plant_id, pd.plant_id, pd.source_platform,
             pd.fetched_at_utc or now, time_range.value,
             json.dumps(kpis, ensure_ascii=False)))
        for p in pd.energy_timeseries:
            if p.energy_kwh is None:
                continue
            conn.execute(
                "INSERT INTO energy_points"
                "(plant_uid, config_plant_id, granularity, period, energy_kwh, updated_at_utc) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(plant_uid, granularity, period) DO UPDATE SET "
                "energy_kwh=excluded.energy_kwh, "
                "config_plant_id=excluded.config_plant_id, "
                "updated_at_utc=excluded.updated_at_utc",
                (pd.plant_id, pd.config_plant_id, p.granularity, p.timestamp_local,
                 p.energy_kwh, now))
        for d in pd.devices:
            conn.execute(
                "INSERT INTO device_snapshots"
                "(run_id, config_plant_id, plant_uid, device_id, device_type, model,"
                " manufacturer, status, current_power_kw, energy_lifetime_kwh,"
                " temperature_c, last_seen_local, fetched_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, pd.config_plant_id, pd.plant_id, d.device_id, d.device_type,
                 d.model, d.manufacturer, d.status.value, d.current_power_kw,
                 d.energy_lifetime_kwh, d.temperature_c, d.last_seen_local,
                 pd.fetched_at_utc or now))
        for a in pd.alerts:
            conn.execute(
                "INSERT INTO alert_snapshots"
                "(run_id, config_plant_id, plant_uid, alert_id, severity, code,"
                " message, timestamp_local, resolved, fetched_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, pd.config_plant_id, pd.plant_id, a.alert_id, a.severity.value,
                 a.code, a.message, a.timestamp_local,
                 None if a.resolved is None else int(a.resolved),
                 pd.fetched_at_utc or now))
```

Note the `energy_points` upsert now also sets `config_plant_id=excluded.config_plant_id` on conflict, so a later web-triggered run backfills the id onto a period a CLI run wrote first.

- [ ] **Step 4: Add the loaders**

At the end of `core/measurements.py`, after `load_series`, add:

```python
def load_devices_latest(conn: sqlite3.Connection, config_plant_id: int) -> list[dict]:
    """Most recent snapshot row per device_id, newest fetch wins."""
    rows = conn.execute(
        "SELECT * FROM device_snapshots WHERE config_plant_id=? "
        "ORDER BY fetched_at_utc DESC", (config_plant_id,)).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(row["device_id"], row)
    return [dict(r) for r in latest.values()]


def load_alerts(conn: sqlite3.Connection, config_plant_id: int,
               limit: int = 100) -> list[dict]:
    """Most recent alert rows, newest first."""
    rows = conn.execute(
        "SELECT * FROM alert_snapshots WHERE config_plant_id=? "
        "ORDER BY fetched_at_utc DESC, id DESC LIMIT ?",
        (config_plant_id, limit)).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/web/test_measurements.py -v`
Expected: PASS (all tests, including the pre-existing ones — the `energy_points` INSERT column list changed but all existing assertions only check `period`/`energy_kwh`, so they're unaffected).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/core/measurements.py tests/web/test_measurements.py
git commit -m "feat: persist device and alert history per fetch"
```

---

### Task 4: Persist power readings and expose energy-by-plant

**Files:**
- Modify: `solaranalysis/core/measurements.py`
- Test: `tests/web/test_measurements.py`

**Interfaces:**
- Consumes: schema from Task 2 (`power_points`); `PlantData.power_timeseries: list[PowerPoint]`.
- Produces: `measurements.load_power_series(conn, config_plant_id, since=None) -> list[PowerPoint]`; `measurements.load_energy_series(conn, config_plant_id, granularity="day", since=None) -> list[EnergyPoint]`.

- [ ] **Step 1: Write the failing tests**

Extend the schema import in `tests/web/test_measurements.py` to include `PowerPoint`:

```python
from solaranalysis.core.schema import (
    EnergyPoint, PlantData, Metric, TimeRange, Device, DeviceStatus, Alert, AlertSeverity,
    PowerPoint,
)
```

Add:

```python
def test_power_points_upsert_latest_wins():
    conn = _conn()
    p1 = _plant()
    p1.config_plant_id = 5
    p1.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.0)]
    measurements.save_measurements(conn, [p1], TimeRange.SNAPSHOT, run_id=None)
    p2 = _plant()
    p2.config_plant_id = 5
    p2.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.5)]
    measurements.save_measurements(conn, [p2], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    rows = conn.execute("SELECT power_kw FROM power_points").fetchall()
    assert len(rows) == 1
    assert rows[0]["power_kw"] == 3.5


def test_load_power_series_orders_and_filters():
    conn = _conn()
    pd = _plant()
    pd.config_plant_id = 5
    pd.power_timeseries = [PowerPoint("2026-07-07T11:00", 2.0),
                           PowerPoint("2026-07-07T09:00", 1.0)]
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    out = measurements.load_power_series(conn, 5, since="2026-07-07T10:00")
    assert [(p.timestamp_local, p.power_kw) for p in out] == [("2026-07-07T11:00", 2.0)]


def test_load_energy_series_by_config_plant_id():
    conn = _conn()
    pd = _plant([EnergyPoint("2026-07-06", 100.0, "day")])
    pd.config_plant_id = 5
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=None)
    conn.commit()
    out = measurements.load_energy_series(conn, 5, granularity="day")
    assert [(p.timestamp_local, p.energy_kwh) for p in out] == [("2026-07-06", 100.0)]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/web/test_measurements.py -k "power or energy_series" -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: power_points` / `AttributeError` on the missing loaders.

- [ ] **Step 3: Add the power writer**

In `core/measurements.py`, in `save_measurements`, right after the `for a in pd.alerts:` block added in Task 3, add:

```python
        for p in pd.power_timeseries:
            if p.power_kw is None:
                continue
            conn.execute(
                "INSERT INTO power_points"
                "(plant_uid, config_plant_id, timestamp_local, power_kw, updated_at_utc) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(plant_uid, timestamp_local) DO UPDATE SET "
                "power_kw=excluded.power_kw, "
                "config_plant_id=excluded.config_plant_id, "
                "updated_at_utc=excluded.updated_at_utc",
                (pd.plant_id, pd.config_plant_id, p.timestamp_local, p.power_kw, now))
```

- [ ] **Step 4: Add the loaders**

Add `PowerPoint` to the existing schema import at the top of `core/measurements.py`:

```python
from .schema import EnergyPoint, PlantData, PowerPoint, TimeRange
```

At the end of the file, after `load_series`, add:

```python
def load_power_series(conn: sqlite3.Connection, config_plant_id: int,
                      since: str | None = None) -> list[PowerPoint]:
    """Accumulated power series for a web-managed plant, oldest first."""
    sql = ("SELECT timestamp_local, power_kw FROM power_points "
           "WHERE config_plant_id=?")
    args: list = [config_plant_id]
    if since is not None:
        sql += " AND timestamp_local>=?"
        args.append(since)
    sql += " ORDER BY timestamp_local"
    return [PowerPoint(row[0], row[1]) for row in conn.execute(sql, args)]


def load_energy_series(conn: sqlite3.Connection, config_plant_id: int,
                       granularity: str = "day",
                       since: str | None = None) -> list[EnergyPoint]:
    """Accumulated energy series for a web-managed plant, oldest first."""
    sql = ("SELECT period, energy_kwh FROM energy_points "
           "WHERE config_plant_id=? AND granularity=?")
    args: list = [config_plant_id, granularity]
    if since is not None:
        sql += " AND period>=?"
        args.append(since)
    sql += " ORDER BY period"
    return [EnergyPoint(row[0], row[1], granularity) for row in conn.execute(sql, args)]
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/web/test_measurements.py -v`
Expected: PASS (all tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/core/measurements.py tests/web/test_measurements.py
git commit -m "feat: persist power readings; add config_plant_id-scoped energy loader"
```

---

### Task 5: Read API — devices, alerts, power, energy endpoints

**Files:**
- Create: `solaranalysis/web/routes/plant_history.py`
- Modify: `solaranalysis/web/app.py`
- Test: `tests/web/test_api_plant_history.py`

**Interfaces:**
- Consumes: `repo.get_plant(conn, pid)`; `measurements.load_devices_latest/load_alerts/load_power_series/load_energy_series` (Tasks 3–4).
- Produces: `GET /api/plants/{pid}/devices`, `GET /api/plants/{pid}/alerts?limit=`, `GET /api/plants/{pid}/power?since=`, `GET /api/plants/{pid}/energy?since=`.

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/web/test_api_plant_history.py`:

```python
import hashlib
from fastapi.testclient import TestClient
from solaranalysis.core import measurements
from solaranalysis.core.schema import (
    PlantData, TimeRange, Device, DeviceStatus, Alert, AlertSeverity,
    PowerPoint, EnergyPoint,
)
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def _create_plant(client):
    return client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]


def test_devices_404_for_unknown_plant(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/plants/999/devices").status_code == 404
    assert client.get("/api/plants/999/alerts").status_code == 404
    assert client.get("/api/plants/999/power").status_code == 404
    assert client.get("/api/plants/999/energy").status_code == 404


def test_devices_empty_list_for_never_fetched_plant(tmp_path):
    client, _ = _client(tmp_path)
    pid = _create_plant(client)
    r = client.get(f"/api/plants/{pid}/devices")
    assert r.status_code == 200
    assert r.json() == []


def test_devices_alerts_power_energy_round_trip(tmp_path):
    client, paths = _client(tmp_path)
    pid = _create_plant(client)

    conn = db.connect(paths.db_path)
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="G")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = pid
    pd.devices = [Device(device_id="inv-1", status=DeviceStatus.ONLINE)]
    pd.alerts = [Alert(alert_id="A1", severity=AlertSeverity.WARNING, message="Low output")]
    pd.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.1)]
    pd.energy_timeseries = [EnergyPoint("2026-07-06", 42.0, "day")]
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=None)
    conn.commit(); conn.close()

    devices = client.get(f"/api/plants/{pid}/devices").json()
    assert devices[0]["device_id"] == "inv-1" and devices[0]["status"] == "online"

    alerts = client.get(f"/api/plants/{pid}/alerts").json()
    assert alerts[0]["message"] == "Low output"

    power = client.get(f"/api/plants/{pid}/power").json()
    assert power == [{"timestamp_local": "2026-07-07T10:00", "power_kw": 3.1}]

    energy = client.get(f"/api/plants/{pid}/energy").json()
    assert energy == [{"timestamp_local": "2026-07-06", "energy_kwh": 42.0}]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/web/test_api_plant_history.py -v`
Expected: FAIL — `404 Not Found` for all paths (no such route registered yet).

- [ ] **Step 3: Create the routes module**

Create `solaranalysis/web/routes/plant_history.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import repo
from ...core import measurements

router = APIRouter()


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("/{pid}/devices")
def plant_devices(pid: int, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return measurements.load_devices_latest(conn, pid)


@router.get("/{pid}/alerts")
def plant_alerts(pid: int, limit: int = 100, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return measurements.load_alerts(conn, pid, limit=limit)


@router.get("/{pid}/power")
def plant_power(pid: int, since: str | None = None, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    points = measurements.load_power_series(conn, pid, since=since)
    return [{"timestamp_local": p.timestamp_local, "power_kw": p.power_kw} for p in points]


@router.get("/{pid}/energy")
def plant_energy(pid: int, since: str | None = None, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    points = measurements.load_energy_series(conn, pid, since=since)
    return [{"timestamp_local": p.timestamp_local, "energy_kwh": p.energy_kwh} for p in points]
```

- [ ] **Step 4: Mount the router**

In `solaranalysis/web/app.py`, change:

```python
    from .routes.plants import router as plants_router
    from .routes.settings import router as settings_router
    app.include_router(plants_router, prefix="/api/plants")
    app.include_router(settings_router, prefix="/api/settings")
```

to:

```python
    from .routes.plants import router as plants_router
    from .routes.plant_history import router as plant_history_router
    from .routes.settings import router as settings_router
    app.include_router(plants_router, prefix="/api/plants")
    app.include_router(plant_history_router, prefix="/api/plants")
    app.include_router(settings_router, prefix="/api/settings")
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/web/test_api_plant_history.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/web/routes/plant_history.py solaranalysis/web/app.py \
        tests/web/test_api_plant_history.py
git commit -m "feat: add read API for per-plant devices, alerts, power, energy"
```

---

### Task 6: Frontend API client — types and methods

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `DeviceSnapshot`, `AlertSnapshot`, `SeriesPoint` types; `api.plantDevices`, `api.plantAlerts`, `api.plantPower`, `api.plantEnergy` methods.

- [ ] **Step 1: Add response types**

In `frontend/src/api.ts`, after the existing `Run` interface, add:

```ts
export interface DeviceSnapshot {
  device_id: string;
  device_type: string;
  model: string | null;
  manufacturer: string | null;
  status: string;
  current_power_kw: number | null;
  energy_lifetime_kwh: number | null;
  temperature_c: number | null;
  last_seen_local: string | null;
  fetched_at_utc: string;
}
export interface AlertSnapshot {
  alert_id: string;
  severity: string;
  code: string | null;
  message: string | null;
  timestamp_local: string | null;
  resolved: number | null;
  fetched_at_utc: string;
}
export interface SeriesPoint {
  timestamp_local: string;
  power_kw?: number | null;
  energy_kwh?: number | null;
}
```

- [ ] **Step 2: Add client methods**

At the end of the `api` object (after `runImport`), add:

```ts
  plantDevices: (id: number) => req<DeviceSnapshot[]>("GET", `/api/plants/${id}/devices`),
  plantAlerts: (id: number, limit = 100) =>
    req<AlertSnapshot[]>("GET", `/api/plants/${id}/alerts?limit=${limit}`),
  plantPower: (id: number, since?: string) =>
    req<SeriesPoint[]>("GET", `/api/plants/${id}/power${since ? `?since=${since}` : ""}`),
  plantEnergy: (id: number, since?: string) =>
    req<SeriesPoint[]>("GET", `/api/plants/${id}/energy${since ? `?since=${since}` : ""}`),
```

- [ ] **Step 3: Verify the frontend still type-checks and builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat: add API client types/methods for plant history"
```

---

### Task 7: Hand-rolled SVG line chart component

**Files:**
- Create: `frontend/src/lineChart.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `LineChart` component — `{ points: { x: string; y: number }[]; color?: string; height?: number; unit?: string }`.

- [ ] **Step 1: Create the component**

Create `frontend/src/lineChart.tsx`:

```tsx
interface Point {
  x: string;
  y: number;
}

interface LineChartProps {
  points: Point[];
  color?: string;
  height?: number;
  unit?: string;
}

const WIDTH = 640;
const PADDING = 24;

export function LineChart({ points, color = "var(--amber)", height = 140, unit = "" }: LineChartProps) {
  if (points.length === 0) {
    return <div className="empty-state">No data yet.</div>;
  }
  const ys = points.map((p) => p.y);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(...ys, minY + 1);
  const stepX = points.length > 1 ? (WIDTH - PADDING * 2) / (points.length - 1) : 0;
  const scaleY = (y: number) =>
    height - PADDING - ((y - minY) / (maxY - minY)) * (height - PADDING * 2);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${PADDING + i * stepX} ${scaleY(p.y)}`)
    .join(" ");

  return (
    <svg
      className="line-chart"
      viewBox={`0 0 ${WIDTH} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Trend chart"
    >
      <path d={path} className="line-chart__path" style={{ stroke: color }} />
      <text x={PADDING} y={14} className="line-chart__label">
        {maxY.toFixed(1)} {unit}
      </text>
      <text x={PADDING} y={height - 6} className="line-chart__label">
        {points[0].x} → {points[points.length - 1].x}
      </text>
    </svg>
  );
}
```

- [ ] **Step 2: Add chart styles**

In `frontend/src/styles.css`, after the `.subtle-note` rule, add:

```css
/* ---- Line chart ---------------------------------------------------- */

.line-chart {
  width: 100%;
  height: 140px;
  display: block;
}
.line-chart__path {
  fill: none;
  stroke-width: 2;
}
.line-chart__label {
  fill: var(--text-faint);
  font-size: 10px;
  font-family: var(--font-mono);
}
```

- [ ] **Step 3: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors (the component isn't used anywhere yet, but must type-check standalone).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lineChart.tsx frontend/src/styles.css
git commit -m "feat: add hand-rolled SVG line chart component"
```

---

### Task 8: Plant Detail page

**Files:**
- Create: `frontend/src/routes/PlantDetail.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes/Plants.tsx`

**Interfaces:**
- Consumes: `api.plants`, `api.plantDevices`, `api.plantAlerts`, `api.plantEnergy`, `api.plantPower` (Task 6); `LineChart` (Task 7).

- [ ] **Step 1: Create the page**

Create `frontend/src/routes/PlantDetail.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { LineChart } from "../lineChart";

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function PlantDetail() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const idValid = Number.isFinite(id);

  const plantsQuery = useQuery({ queryKey: ["plants"], queryFn: api.plants });
  const plant = plantsQuery.data?.find((p) => p.id === id);

  const devicesQuery = useQuery({
    queryKey: ["plantDevices", id],
    queryFn: () => api.plantDevices(id),
    enabled: idValid,
  });
  const alertsQuery = useQuery({
    queryKey: ["plantAlerts", id],
    queryFn: () => api.plantAlerts(id),
    enabled: idValid,
  });
  const energyQuery = useQuery({
    queryKey: ["plantEnergy", id],
    queryFn: () => api.plantEnergy(id),
    enabled: idValid,
  });
  const powerQuery = useQuery({
    queryKey: ["plantPower", id],
    queryFn: () => api.plantPower(id),
    enabled: idValid,
  });

  if (!idValid) {
    return <div className="empty-state">Invalid plant id.</div>;
  }
  if (plantsQuery.isLoading) {
    return <div className="empty-state">Loading…</div>;
  }
  if (!plant) {
    return <div className="empty-state">Plant not found.</div>;
  }

  const energyPoints = (energyQuery.data ?? [])
    .filter((p) => p.energy_kwh != null)
    .map((p) => ({ x: p.timestamp_local, y: p.energy_kwh as number }));
  const powerPoints = (powerQuery.data ?? [])
    .filter((p) => p.power_kw != null)
    .map((p) => ({ x: p.timestamp_local, y: p.power_kw as number }));

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>{plant.name}</h1>
          <p>
            <span className={`badge-platform badge-platform--${plant.platform}`}>{plant.platform}</span>
          </p>
        </div>
        <Link className="btn btn--ghost" to="/plants">
          Back to plants
        </Link>
      </div>

      <section style={{ marginBottom: 24 }}>
        <h2>Energy history</h2>
        {energyQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <LineChart points={energyPoints} unit="kWh" />
        )}
      </section>

      <section style={{ marginBottom: 24 }}>
        <h2>Power history</h2>
        {powerQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <LineChart points={powerPoints} color="var(--cyan)" unit="kW" />
        )}
      </section>

      <section style={{ marginBottom: 24 }}>
        <h2>Devices</h2>
        {devicesQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : !devicesQuery.data || devicesQuery.data.length === 0 ? (
          <div className="empty-state">No device data yet. Run an analysis to populate this.</div>
        ) : (
          <div className="panel table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Power</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {devicesQuery.data.map((d) => (
                  <tr key={d.device_id}>
                    <td>{d.device_id}</td>
                    <td className="cell-muted">{d.model ?? "—"}</td>
                    <td>{d.status}</td>
                    <td className="mono">{d.current_power_kw != null ? `${d.current_power_kw} kW` : "—"}</td>
                    <td className="cell-muted">{formatTimestamp(d.last_seen_local)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2>Recent alerts</h2>
        {alertsQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : !alertsQuery.data || alertsQuery.data.length === 0 ? (
          <div className="empty-state">No alerts recorded.</div>
        ) : (
          <ul className="skip-list">
            {alertsQuery.data.map((a, i) => (
              <li key={`${a.alert_id}-${i}`}>
                <strong>{a.severity}</strong> — {a.message ?? a.code ?? "alert"}{" "}
                <span className="cell-muted">{formatTimestamp(a.timestamp_local)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Wire the route**

In `frontend/src/App.tsx`, change:

```tsx
import Plants from "./routes/Plants";
import Runs from "./routes/Runs";
```

to:

```tsx
import Plants from "./routes/Plants";
import PlantDetail from "./routes/PlantDetail";
import Runs from "./routes/Runs";
```

and change:

```tsx
          <Route path="/plants" element={<Plants />} />
          <Route path="/runs" element={<Runs />} />
```

to:

```tsx
          <Route path="/plants" element={<Plants />} />
          <Route path="/plants/:id" element={<PlantDetail />} />
          <Route path="/runs" element={<Runs />} />
```

- [ ] **Step 3: Link the plant name in `Plants.tsx`**

In `frontend/src/routes/Plants.tsx`, add the import:

```tsx
import { Link } from "react-router-dom";
```

Change:

```tsx
    <tr>
      <td>{plant.name}</td>
```

to:

```tsx
    <tr>
      <td>
        <Link to={`/plants/${plant.id}`}>{plant.name}</Link>
      </td>
```

- [ ] **Step 4: Verify the frontend builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 5: Manual smoke test**

Run: `cd frontend && npm run dev` (in one terminal) and `python -m solaranalysis.web` (in another, from the repo root).
Open `http://localhost:5173` (or whatever port Vite prints), log in, go to Plants, click a plant name, and confirm the Plant Detail page loads with its devices/alerts/energy/power sections (empty states are fine if that plant has never been fetched with this code — that's expected for pre-existing data written before Task 1–4).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/PlantDetail.tsx frontend/src/App.tsx frontend/src/routes/Plants.tsx
git commit -m "feat: add Plant Detail page with device/alert/energy/power history"
```

---

### Task 9: Full verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `python -m pytest -q`
Expected: all tests pass, no regressions from Tasks 1–5.

- [ ] **Step 2: Run the frontend build**

Run: `cd frontend && npm run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 3: Run one real pipeline end-to-end (optional, requires real portal credentials in `.env`/DB)**

Run: `python -m solaranalysis.web`, trigger a manual run from the UI against at least one real plant, then open that plant's Plant Detail page and confirm devices/alerts/energy/power actually populate from the run that just completed. This is the only step that proves `config_plant_id` is set correctly end-to-end through a real adapter fetch rather than a test double.
