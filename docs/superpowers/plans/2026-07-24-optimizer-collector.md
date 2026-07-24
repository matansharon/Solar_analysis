# SolarEdge Per-Optimizer Collector (B1: collect + store) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone CLI that logs into the SolarEdge account, and for every site pulls each optimizer's daily energy plus inventory via the `/services/layout/*` API, persisting both into `app.db` — with an optional multi-day backfill.

**Architecture:** New self-contained package `solaranalysis/optimizers/` (pure mappers + thin IO shell), reusing the existing `SolarEdgeAdapter` login/session, `web/repo`+`web/crypto` for credentials, and `web/paths`. This B1 plan covers fetch + store only; analysis, report, email, and the daily schedule are a separate follow-up plan (B2). This is the first of two sub-plans for the Phase B spec.

**Tech Stack:** Python 3.10, stdlib `sqlite3`, Playwright browser session (reused via the adapter), pytest. No new dependencies.

## Global Constraints

- Run tests with the project venv: `.venv/Scripts/python.exe -m pytest` (never bare `python`/`python3`; on Windows Git Bash `python` is not on PATH). Prefix `PYTHONUTF8=1` when Hebrew site names may print.
- DB migrations are additive-only (`CREATE TABLE IF NOT EXISTS`); bump `SCHEMA_VERSION`. `init_db` runs on startup — no manual migration step. (Phase A left it at 5; this plan takes it to 6.)
- Read-only against the SolarEdge portal — GET/POST for data only; never issue layout remote-commands.
- Commits carry NO AI attribution (clean-commits skill): no `Co-Authored-By: Claude`/Anthropic, no AI/Claude/Anthropic mention in any message.
- Committed tests must NOT depend on the git-ignored `.discovery/` fixtures; use small inline synthetic payloads shaped like the real ones (structures are given in each task).
- TDD: failing test first → confirm it fails → minimal implementation → confirm pass → commit.
- Endpoint facts (verified live 2026-07-23), `BASE = "https://monitoring.solaredge.com"`:
  - Inventory tree: `GET {BASE}/services/layout/logical/generic/v2/site/{sid}?include-optimizers=true` → `{"siteStructure": <node>}`. Nodes: `{type, name, serial, order, displayOrder, properties:{model,status,...}, children:[...]}`. `type` ∈ SITE/FOLDER/INVERTER/STRING/OPTIMIZER; FOLDER nodes group children. Optimizer leaf: `{type:"OPTIMIZER", serial, name:"Optimizer 1.4.5", displayOrder:"1.4.5", properties:{model:"P950-…", status:"ACTIVE"}}`.
  - Per-optimizer energy: `GET {BASE}/services/layout/energy/site/{sid}/by-inverter?start-date={d}&end-date={d}&inverter-serials={csv}&include-color=true` → `{siteId,startDate,endDate,inverters:[{serial, energy:{value,unit}, strings:[...], optimizers:[{serial, energy:{value:<Wh>,unit:"watt-hour"}, temperature:{temperature,temperatureUnit}, color:<0..1>}]}]}`. `energy.value` is watt-hours; `color` is SolarEdge's peer-normalized 0..1.
  - Site list: `POST {BASE}/services/sitelist/searchSites` → `{page:[{solarFieldId,name,...}]}`.

## File Structure

**Create:**
- `solaranalysis/optimizers/__init__.py` — package marker (empty)
- `solaranalysis/optimizers/mappers.py` — pure: `OptimizerInfo`, `OptimizerEnergyRow`, `flatten_inventory`, `map_by_inverter_energy`
- `solaranalysis/optimizers/store.py` — DB upserts + loaders for the two new tables
- `solaranalysis/optimizers/layout_client.py` — thin authenticated GETs on a `BrowserSession`
- `solaranalysis/optimizers/collector.py` — `parse_site_ids`, `day_range`, `collect_site`, `collect`
- `solaranalysis/optimizers/cli.py` — argparse entry point
- `solaranalysis/optimizers/__main__.py` — `raise SystemExit(main())`
- `tests/optimizers/__init__.py`
- `tests/optimizers/test_mappers.py`, `test_store.py`, `test_layout_client.py`, `test_collector.py`, `test_cli.py`
- `tests/web/test_db_optimizers.py`

**Modify:**
- `solaranalysis/web/db.py` — two new tables + `SCHEMA_VERSION` → 6

---

### Task 1: DB tables `optimizers` + `optimizer_energy` (schema v6)

**Files:**
- Modify: `solaranalysis/web/db.py`
- Test: `tests/web/test_db_optimizers.py`

**Interfaces:**
- Produces: table `optimizers` PK `(site_id, optimizer_serial)`; table `optimizer_energy` PK `(site_id, optimizer_serial, day)` + index `ix_optenergy_day`; `SCHEMA_VERSION == 6`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_db_optimizers.py`:

```python
from solaranalysis.web import db


def test_init_db_creates_optimizer_tables_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"optimizers", "optimizer_energy"} <= tables
    inv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(optimizers)")}
    assert {"site_id", "optimizer_serial", "label", "inverter_serial",
            "model", "first_seen_utc", "last_seen_utc"} <= inv_cols
    e_cols = {r["name"] for r in conn.execute("PRAGMA table_info(optimizer_energy)")}
    assert {"site_id", "optimizer_serial", "day", "energy_wh", "color",
            "temperature_c", "updated_at_utc"} <= e_cols
    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == "6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db_optimizers.py -v`
Expected: FAIL — tables missing / version is `"5"`.

- [ ] **Step 3: Add DDL and bump version**

In `solaranalysis/web/db.py`, set `SCHEMA_VERSION = 6`. Append to the `_DDL` string (before its closing `"""`, after `raw_payloads`):

```sql
CREATE TABLE IF NOT EXISTS optimizers(
  site_id INTEGER NOT NULL,
  optimizer_serial TEXT NOT NULL,
  label TEXT,                     -- displayOrder, e.g. '1.4.5'
  name TEXT,                      -- 'Optimizer 1.4.5'
  inverter_serial TEXT,
  inverter_name TEXT,             -- 'Inverter 1'
  string_label TEXT,              -- '1.4'
  string_name TEXT,               -- 'String 1.4'
  model TEXT,                     -- optimizer hardware model, e.g. 'P950-...'
  status TEXT,
  module_manufacturer TEXT,       -- not exposed by the tree; nullable in v1
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
  day TEXT NOT NULL,              -- 'YYYY-MM-DD' (site-local day)
  energy_wh REAL,
  color REAL,                     -- SolarEdge normalized 0..1 (nullable)
  temperature_c REAL,             -- nullable
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (site_id, optimizer_serial, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_optenergy_day ON optimizer_energy(site_id, day);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db_optimizers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/db.py tests/web/test_db_optimizers.py
git commit -m "feat: optimizer inventory + daily energy tables (schema v6)"
```

---

### Task 2: Package skeleton + inventory mapper

**Files:**
- Create: `solaranalysis/optimizers/__init__.py` (empty), `solaranalysis/optimizers/mappers.py`, `tests/optimizers/__init__.py` (empty)
- Test: `tests/optimizers/test_mappers.py`

**Interfaces:**
- Produces:
  - `@dataclass OptimizerInfo(serial:str, label:str|None, name:str|None, inverter_serial:str|None, inverter_name:str|None, string_label:str|None, string_name:str|None, model:str|None, status:str|None)`
  - `flatten_inventory(payload: dict) -> list[OptimizerInfo]` — accepts the full `{siteStructure:…}` payload OR the bare node; recurses through FOLDER nodes; one `OptimizerInfo` per `type=="OPTIMIZER"` node, carrying its nearest ancestor INVERTER (serial/name/`properties.model` unused here) and STRING (name/displayOrder).

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/__init__.py` (empty) and `tests/optimizers/test_mappers.py`:

```python
from solaranalysis.optimizers.mappers import flatten_inventory, OptimizerInfo


def _tree():
    # Minimal synthetic siteStructure mirroring the live shape:
    # SITE -> FOLDER(INVERTER) -> INVERTER -> FOLDER(STRING) -> STRING
    #      -> FOLDER(OPTIMIZER) -> OPTIMIZER leaves.
    opt = lambda s, d: {"type": "OPTIMIZER", "serial": s, "name": f"Optimizer {d}",
                        "displayOrder": d,
                        "properties": {"model": "P950-4RM4MBY-NM24", "status": "ACTIVE"}}
    return {"siteStructure": {
        "type": "SITE", "name": "Site",
        "children": [{"type": "FOLDER", "name": "INVERTER", "children": [
            {"type": "INVERTER", "serial": "INV-1", "name": "Inverter 1",
             "properties": {"model": "SE50K-IL00IBNQ4", "status": "ACTIVE"},
             "children": [{"type": "FOLDER", "name": "STRING", "children": [
                 {"type": "STRING", "name": "String 1.0", "displayOrder": "1.0",
                  "children": [{"type": "FOLDER", "name": "OPTIMIZER", "children": [
                      opt("136F487F-49", "1.0.1"), opt("136BCBF7-40", "1.0.2")]}]}]}]}]}]}}


def test_flatten_inventory_extracts_optimizers_with_lineage():
    infos = flatten_inventory(_tree())
    assert len(infos) == 2
    a = infos[0]
    assert isinstance(a, OptimizerInfo)
    assert a.serial == "136F487F-49"
    assert a.label == "1.0.1"
    assert a.name == "Optimizer 1.0.1"
    assert a.inverter_serial == "INV-1"
    assert a.inverter_name == "Inverter 1"
    assert a.string_label == "1.0"
    assert a.string_name == "String 1.0"
    assert a.model == "P950-4RM4MBY-NM24"
    assert a.status == "ACTIVE"


def test_flatten_inventory_accepts_bare_node_and_ignores_non_optimizers():
    infos = flatten_inventory(_tree()["siteStructure"])  # bare node, no wrapper
    assert [i.serial for i in infos] == ["136F487F-49", "136BCBF7-40"]


def test_flatten_inventory_empty_payload():
    assert flatten_inventory({}) == []
    assert flatten_inventory({"siteStructure": {}}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_mappers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers'`.

- [ ] **Step 3: Create the package and mapper**

Create empty `solaranalysis/optimizers/__init__.py`. Create `solaranalysis/optimizers/mappers.py`:

```python
"""Pure mappers: SolarEdge /services/layout/* payloads -> plain records.

No IO, no browser — fixture/synthetic-testable. The IO shell (layout_client)
fetches the raw dicts; these turn them into rows the store persists.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class OptimizerInfo:
    serial: str
    label: str | None = None            # displayOrder, e.g. '1.4.5'
    name: str | None = None             # 'Optimizer 1.4.5'
    inverter_serial: str | None = None
    inverter_name: str | None = None
    string_label: str | None = None     # string displayOrder, e.g. '1.4'
    string_name: str | None = None      # 'String 1.4'
    model: str | None = None            # optimizer hardware model
    status: str | None = None


def flatten_inventory(payload: dict) -> list[OptimizerInfo]:
    """Walk the logical tree; emit one OptimizerInfo per OPTIMIZER leaf,
    carrying its nearest ancestor INVERTER and STRING. FOLDER nodes are
    transparent grouping containers and are recursed through."""
    root = (payload or {}).get("siteStructure", payload) or {}
    out: list[OptimizerInfo] = []

    def walk(node, inv, st):
        if not isinstance(node, dict):
            return
        t = node.get("type")
        if t == "INVERTER":
            inv = node
        elif t == "STRING":
            st = node
        elif t == "OPTIMIZER":
            props = node.get("properties") or {}
            inv = inv or {}
            st = st or {}
            out.append(OptimizerInfo(
                serial=node.get("serial"),
                label=node.get("displayOrder"),
                name=node.get("name"),
                inverter_serial=inv.get("serial"),
                inverter_name=inv.get("name"),
                string_label=st.get("displayOrder"),
                string_name=st.get("name"),
                model=props.get("model"),
                status=props.get("status"),
            ))
            return
        for child in node.get("children") or []:
            walk(child, inv, st)

    walk(root, None, None)
    return [i for i in out if i.serial]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_mappers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/optimizers/__init__.py solaranalysis/optimizers/mappers.py tests/optimizers/__init__.py tests/optimizers/test_mappers.py
git commit -m "feat: optimizer package + logical-tree inventory mapper"
```

---

### Task 3: Per-optimizer energy mapper

**Files:**
- Modify: `solaranalysis/optimizers/mappers.py`
- Test: `tests/optimizers/test_mappers.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `@dataclass OptimizerEnergyRow(inverter_serial:str|None, optimizer_serial:str, energy_wh:float|None, color:float|None, temperature_c:float|None)`
  - `map_by_inverter_energy(payload: dict) -> list[OptimizerEnergyRow]` — flattens `inverters[].optimizers[]`; `energy_wh` from `energy.value` (watt-hours), `color` passthrough, `temperature_c` from `temperature.temperature` (often None).

- [ ] **Step 1: Write the failing test**

Add to `tests/optimizers/test_mappers.py`:

```python
from solaranalysis.optimizers.mappers import map_by_inverter_energy, OptimizerEnergyRow


def _energy_payload():
    return {"siteId": 1, "startDate": "2026-07-22", "endDate": "2026-07-22",
            "inverters": [
                {"serial": "INV-1", "energy": {"value": 100.0, "unit": "watt-hour"},
                 "optimizers": [
                     {"serial": "OPT-A", "energy": {"value": 5824.75, "unit": "watt-hour"},
                      "temperature": {"temperature": None, "temperatureUnit": None},
                      "color": 0.978},
                     {"serial": "OPT-B", "energy": {"value": 0.0, "unit": "watt-hour"},
                      "temperature": {"temperature": 41.5, "temperatureUnit": "C"},
                      "color": 0.0}]},
                {"serial": "INV-2", "optimizers": [
                     {"serial": "OPT-C", "energy": {"value": 4000.0, "unit": "watt-hour"},
                      "color": 0.71}]}]}


def test_map_by_inverter_energy_flattens_all_optimizers():
    rows = map_by_inverter_energy(_energy_payload())
    assert len(rows) == 3
    by = {r.optimizer_serial: r for r in rows}
    assert isinstance(by["OPT-A"], OptimizerEnergyRow)
    assert by["OPT-A"].inverter_serial == "INV-1"
    assert by["OPT-A"].energy_wh == 5824.75
    assert by["OPT-A"].color == 0.978
    assert by["OPT-A"].temperature_c is None
    assert by["OPT-B"].energy_wh == 0.0 and by["OPT-B"].temperature_c == 41.5
    assert by["OPT-C"].inverter_serial == "INV-2" and by["OPT-C"].energy_wh == 4000.0


def test_map_by_inverter_energy_tolerates_missing_pieces():
    rows = map_by_inverter_energy({"inverters": [
        {"serial": "INV-1", "optimizers": [
            {"serial": "OPT-X"},  # no energy/color/temperature
            {"energy": {"value": 1.0}}]}]})  # no serial -> skipped
    assert len(rows) == 1
    assert rows[0].optimizer_serial == "OPT-X"
    assert rows[0].energy_wh is None and rows[0].color is None and rows[0].temperature_c is None


def test_map_by_inverter_energy_empty():
    assert map_by_inverter_energy({}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_mappers.py -v -k by_inverter`
Expected: FAIL — `ImportError: cannot import name 'map_by_inverter_energy'`.

- [ ] **Step 3: Implement the energy mapper**

Append to `solaranalysis/optimizers/mappers.py`:

```python
@dataclass
class OptimizerEnergyRow:
    inverter_serial: str | None
    optimizer_serial: str
    energy_wh: float | None
    color: float | None
    temperature_c: float | None


def _num(x):
    return x if isinstance(x, (int, float)) else None


def map_by_inverter_energy(payload: dict) -> list[OptimizerEnergyRow]:
    """energy/by-inverter payload -> per-optimizer rows (energy in watt-hours)."""
    out: list[OptimizerEnergyRow] = []
    for inv in (payload or {}).get("inverters") or []:
        if not isinstance(inv, dict):
            continue
        inv_serial = inv.get("serial")
        for op in inv.get("optimizers") or []:
            if not isinstance(op, dict) or not op.get("serial"):
                continue
            energy = op.get("energy") or {}
            temp = op.get("temperature") or {}
            out.append(OptimizerEnergyRow(
                inverter_serial=inv_serial,
                optimizer_serial=op.get("serial"),
                energy_wh=_num(energy.get("value")),
                color=_num(op.get("color")),
                temperature_c=_num(temp.get("temperature")),
            ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_mappers.py -v`
Expected: PASS (all mapper tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/optimizers/mappers.py tests/optimizers/test_mappers.py
git commit -m "feat: per-optimizer energy mapper"
```

---

### Task 4: Store (upserts + loaders)

**Files:**
- Create: `solaranalysis/optimizers/store.py`
- Test: `tests/optimizers/test_store.py`

**Interfaces:**
- Consumes: `OptimizerInfo` (Task 2), `OptimizerEnergyRow` (Task 3); tables from Task 1.
- Produces:
  - `save_inventory(conn, site_id: int, infos: list[OptimizerInfo], now: str) -> None` — upsert on `(site_id, optimizer_serial)`; `first_seen_utc` preserved on conflict, `last_seen_utc` refreshed.
  - `save_energy(conn, site_id: int, day: str, rows: list[OptimizerEnergyRow], now: str) -> None` — upsert on `(site_id, optimizer_serial, day)`; latest value wins.
  - `load_inventory(conn, site_id) -> list[dict]`, `load_energy(conn, site_id, day) -> list[dict]` (test/read helpers).

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_store.py`:

```python
from solaranalysis.web import db
from solaranalysis.optimizers import store
from solaranalysis.optimizers.mappers import OptimizerInfo, OptimizerEnergyRow


def _conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def test_save_inventory_upserts_and_preserves_first_seen():
    conn = _conn()
    info = OptimizerInfo(serial="OPT-A", label="1.0.1", name="Optimizer 1.0.1",
                         inverter_serial="INV-1", inverter_name="Inverter 1",
                         string_label="1.0", string_name="String 1.0",
                         model="P950", status="ACTIVE")
    store.save_inventory(conn, 42, [info], now="2026-07-01T00:00:00+00:00")
    store.save_inventory(conn, 42, [info], now="2026-07-05T00:00:00+00:00")
    rows = store.load_inventory(conn, 42)
    assert len(rows) == 1
    r = rows[0]
    assert r["optimizer_serial"] == "OPT-A" and r["label"] == "1.0.1"
    assert r["inverter_serial"] == "INV-1" and r["model"] == "P950"
    assert r["first_seen_utc"] == "2026-07-01T00:00:00+00:00"   # preserved
    assert r["last_seen_utc"] == "2026-07-05T00:00:00+00:00"    # refreshed


def test_save_energy_upserts_latest_value():
    conn = _conn()
    row = OptimizerEnergyRow("INV-1", "OPT-A", energy_wh=5000.0, color=0.9, temperature_c=None)
    store.save_energy(conn, 42, "2026-07-22", [row], now="2026-07-23T00:00:00+00:00")
    row2 = OptimizerEnergyRow("INV-1", "OPT-A", energy_wh=5100.0, color=0.92, temperature_c=40.0)
    store.save_energy(conn, 42, "2026-07-22", [row2], now="2026-07-23T06:00:00+00:00")
    rows = store.load_energy(conn, 42, "2026-07-22")
    assert len(rows) == 1
    assert rows[0]["energy_wh"] == 5100.0 and rows[0]["color"] == 0.92
    assert rows[0]["temperature_c"] == 40.0


def test_energy_isolated_by_site_and_day():
    conn = _conn()
    r = OptimizerEnergyRow("INV-1", "OPT-A", 1.0, 0.5, None)
    store.save_energy(conn, 1, "2026-07-22", [r], now="n")
    store.save_energy(conn, 2, "2026-07-22", [r], now="n")
    store.save_energy(conn, 1, "2026-07-23", [r], now="n")
    assert len(store.load_energy(conn, 1, "2026-07-22")) == 1
    assert len(store.load_energy(conn, 2, "2026-07-22")) == 1
    assert len(store.load_energy(conn, 1, "2026-07-23")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.store'`.

- [ ] **Step 3: Implement the store**

Create `solaranalysis/optimizers/store.py`:

```python
"""Persist optimizer inventory + daily energy into app.db (schema v6 tables)."""
from __future__ import annotations
import sqlite3

from .mappers import OptimizerInfo, OptimizerEnergyRow


def save_inventory(conn: sqlite3.Connection, site_id: int,
                   infos: list[OptimizerInfo], now: str) -> None:
    for i in infos:
        conn.execute(
            "INSERT INTO optimizers"
            "(site_id, optimizer_serial, label, name, inverter_serial, inverter_name,"
            " string_label, string_name, model, status, first_seen_utc, last_seen_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id, optimizer_serial) DO UPDATE SET "
            "label=excluded.label, name=excluded.name, "
            "inverter_serial=excluded.inverter_serial, inverter_name=excluded.inverter_name, "
            "string_label=excluded.string_label, string_name=excluded.string_name, "
            "model=excluded.model, status=excluded.status, "
            "last_seen_utc=excluded.last_seen_utc",
            (site_id, i.serial, i.label, i.name, i.inverter_serial, i.inverter_name,
             i.string_label, i.string_name, i.model, i.status, now, now))


def save_energy(conn: sqlite3.Connection, site_id: int, day: str,
                rows: list[OptimizerEnergyRow], now: str) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO optimizer_energy"
            "(site_id, optimizer_serial, day, energy_wh, color, temperature_c, updated_at_utc) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id, optimizer_serial, day) DO UPDATE SET "
            "energy_wh=excluded.energy_wh, color=excluded.color, "
            "temperature_c=excluded.temperature_c, updated_at_utc=excluded.updated_at_utc",
            (site_id, r.optimizer_serial, day, r.energy_wh, r.color, r.temperature_c, now))


def load_inventory(conn: sqlite3.Connection, site_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizers WHERE site_id=? ORDER BY optimizer_serial", (site_id,))]


def load_energy(conn: sqlite3.Connection, site_id: int, day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizer_energy WHERE site_id=? AND day=? ORDER BY optimizer_serial",
        (site_id, day))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/optimizers/store.py tests/optimizers/test_store.py
git commit -m "feat: optimizer inventory + energy store (upserts)"
```

---

### Task 5: Layout client (thin authenticated GETs)

**Files:**
- Create: `solaranalysis/optimizers/layout_client.py`
- Test: `tests/optimizers/test_layout_client.py`

**Interfaces:**
- Consumes: a `BrowserSession`-like object with `.get_json(url)` and `.post_json(url)` (Phase A's `_browser.BrowserSession`).
- Produces:
  - `get_site_list(bs) -> dict` — `POST {BASE}/services/sitelist/searchSites`.
  - `get_logical_tree(bs, sid) -> dict` — logical tree with optimizers.
  - `get_by_inverter_energy(bs, sid, day: str, inverter_serials: list[str]) -> dict` — per-optimizer energy for one day.
  - `BASE` constant.

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_layout_client.py`:

```python
from solaranalysis.optimizers import layout_client as lc


class FakeBS:
    def __init__(self):
        self.gets, self.posts = [], []
    def get_json(self, url):
        self.gets.append(url)
        return {"ok": url}
    def post_json(self, url, **kw):
        self.posts.append(url)
        return {"page": []}


def test_logical_tree_url():
    bs = FakeBS()
    lc.get_logical_tree(bs, 2387929)
    assert bs.gets == [
        "https://monitoring.solaredge.com/services/layout/logical/generic/v2/site/2387929?include-optimizers=true"]


def test_by_inverter_energy_url_joins_serials_and_dates():
    bs = FakeBS()
    lc.get_by_inverter_energy(bs, 2387929, "2026-07-22", ["7E04A726-4F", "7E04A823-4D"])
    url = bs.gets[0]
    assert url.startswith(
        "https://monitoring.solaredge.com/services/layout/energy/site/2387929/by-inverter?")
    assert "start-date=2026-07-22" in url and "end-date=2026-07-22" in url
    assert "inverter-serials=7E04A726-4F,7E04A823-4D" in url
    assert "include-color=true" in url


def test_site_list_uses_post():
    bs = FakeBS()
    out = lc.get_site_list(bs)
    assert bs.posts == ["https://monitoring.solaredge.com/services/sitelist/searchSites"]
    assert out == {"page": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_layout_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.layout_client'`.

- [ ] **Step 3: Implement the client**

Create `solaranalysis/optimizers/layout_client.py`:

```python
"""Thin authenticated GET/POST wrappers for the SolarEdge /services/layout/*
API. All calls run inside an already-authenticated BrowserSession (cookies
shared via context.request). No parsing here — see mappers.py."""
from __future__ import annotations

BASE = "https://monitoring.solaredge.com"


def get_site_list(bs) -> dict:
    return bs.post_json(f"{BASE}/services/sitelist/searchSites") or {}


def get_logical_tree(bs, sid) -> dict:
    return bs.get_json(
        f"{BASE}/services/layout/logical/generic/v2/site/{sid}"
        "?include-optimizers=true") or {}


def get_by_inverter_energy(bs, sid, day: str, inverter_serials: list[str]) -> dict:
    serials = ",".join(inverter_serials)
    return bs.get_json(
        f"{BASE}/services/layout/energy/site/{sid}/by-inverter"
        f"?start-date={day}&end-date={day}"
        f"&inverter-serials={serials}&include-color=true") or {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_layout_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/optimizers/layout_client.py tests/optimizers/test_layout_client.py
git commit -m "feat: SolarEdge layout-API client"
```

---

### Task 6: Collector orchestration

**Files:**
- Create: `solaranalysis/optimizers/collector.py`
- Test: `tests/optimizers/test_collector.py`

**Interfaces:**
- Consumes: `layout_client` (Task 5), `mappers` (Tasks 2-3), `store` (Task 4).
- Produces:
  - `parse_site_ids(search_payload: dict) -> list[int]` — pull `page[].solarFieldId`.
  - `day_range(target: date, count: int) -> list[str]` — `count` ISO date strings ending at `target` (count ≥ 1), oldest first.
  - `collect_site(bs, conn, site_id: int, days: list[str], now: str) -> dict` — fetch tree → save inventory → per day fetch energy → save; returns `{"site_id", "optimizers", "days", "energy_rows"}`.
  - `collect(bs, conn, site_ids: list[int], days: list[str], now: str) -> list[dict]` — loop `collect_site` over sites, `conn.commit()` per site, per-site failures isolated (logged into the returned dict as `{"site_id", "error"}`), never abort the run.

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_collector.py`:

```python
from datetime import date

from solaranalysis.web import db
from solaranalysis.optimizers import collector, store


def test_parse_site_ids():
    payload = {"page": [{"solarFieldId": 2387929}, {"solarFieldId": 2257529},
                        {"noId": 1}]}
    assert collector.parse_site_ids(payload) == [2387929, 2257529]
    assert collector.parse_site_ids({}) == []


def test_day_range_counts_back_from_target():
    assert collector.day_range(date(2026, 7, 22), 1) == ["2026-07-22"]
    assert collector.day_range(date(2026, 7, 22), 3) == [
        "2026-07-20", "2026-07-21", "2026-07-22"]


class FakeBS:
    """Returns canned tree + energy payloads keyed by URL fragment."""
    def __init__(self):
        self.tree = {"siteStructure": {"type": "SITE", "children": [
            {"type": "FOLDER", "name": "INVERTER", "children": [
                {"type": "INVERTER", "serial": "INV-1", "name": "Inverter 1",
                 "properties": {"model": "SE50K"}, "children": [
                    {"type": "FOLDER", "name": "STRING", "children": [
                        {"type": "STRING", "name": "String 1.0", "displayOrder": "1.0",
                         "children": [{"type": "FOLDER", "name": "OPTIMIZER", "children": [
                             {"type": "OPTIMIZER", "serial": "OPT-A", "displayOrder": "1.0.1",
                              "name": "Optimizer 1.0.1", "properties": {"model": "P950"}}]}]}]}]}]}]}}
        self.energy = {"inverters": [{"serial": "INV-1", "optimizers": [
            {"serial": "OPT-A", "energy": {"value": 5000.0, "unit": "watt-hour"},
             "color": 0.9, "temperature": {"temperature": None}}]}]}
    def get_json(self, url):
        if "/logical/" in url:
            return self.tree
        if "/by-inverter" in url:
            return self.energy
        return {}


def test_collect_site_persists_inventory_and_energy():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_site(FakeBS(), conn, site_id=42,
                                 days=["2026-07-21", "2026-07-22"], now="NOW")
    assert res["optimizers"] == 1 and res["days"] == 2 and res["energy_rows"] == 2
    inv = store.load_inventory(conn, 42)
    assert len(inv) == 1 and inv[0]["optimizer_serial"] == "OPT-A"
    e21 = store.load_energy(conn, 42, "2026-07-21")
    e22 = store.load_energy(conn, 42, "2026-07-22")
    assert e21[0]["energy_wh"] == 5000.0 and e22[0]["color"] == 0.9


def test_collect_isolates_per_site_failure():
    conn = db.connect(":memory:"); db.init_db(conn)

    class Boom(FakeBS):
        def get_json(self, url):
            raise RuntimeError("network down")

    results = collector.collect(
        bs_for={42: FakeBS(), 99: Boom()}, conn=conn,
        site_ids=[42, 99], days=["2026-07-22"], now="NOW")
    ok = {r["site_id"]: r for r in results}
    assert ok[42]["optimizers"] == 1
    assert "error" in ok[99]
    # site 42 still persisted despite site 99 blowing up
    assert len(store.load_inventory(conn, 42)) == 1
```

Note: `collect` in the test uses a `bs_for` mapping so the test can give each site its own fake session. In production a single `bs` serves all sites; support both — see the implementation.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.collector'`.

- [ ] **Step 3: Implement the collector**

Create `solaranalysis/optimizers/collector.py`:

```python
"""Orchestrate per-site optimizer collection: tree -> inventory, then
per-day energy/by-inverter -> stored rows. Pure of login concerns — the CLI
supplies an authenticated BrowserSession."""
from __future__ import annotations
from datetime import date, timedelta

from . import layout_client as lc
from . import mappers, store


def parse_site_ids(search_payload: dict) -> list[int]:
    ids = []
    for s in (search_payload or {}).get("page") or []:
        if isinstance(s, dict) and isinstance(s.get("solarFieldId"), int):
            ids.append(s["solarFieldId"])
    return ids


def day_range(target: date, count: int) -> list[str]:
    """`count` consecutive ISO days ending at `target` (oldest first)."""
    count = max(1, count)
    return [(target - timedelta(days=n)).isoformat()
            for n in range(count - 1, -1, -1)]


def collect_site(bs, conn, site_id: int, days: list[str], now: str) -> dict:
    tree = lc.get_logical_tree(bs, site_id)
    infos = mappers.flatten_inventory(tree)
    store.save_inventory(conn, site_id, infos, now)
    inverter_serials = sorted({i.inverter_serial for i in infos if i.inverter_serial})
    energy_rows = 0
    for day in days:
        payload = lc.get_by_inverter_energy(bs, site_id, day, inverter_serials)
        rows = mappers.map_by_inverter_energy(payload)
        store.save_energy(conn, site_id, day, rows, now)
        energy_rows += len(rows)
    return {"site_id": site_id, "optimizers": len(infos),
            "days": len(days), "energy_rows": energy_rows}


def collect(conn, site_ids: list[int], days: list[str], now: str,
            bs=None, bs_for: dict | None = None) -> list[dict]:
    """Collect every site. Provide either one `bs` (production) or a
    `bs_for` {site_id: bs} mapping (tests). Per-site failures are isolated:
    logged into the result list and skipped, never aborting the run."""
    results = []
    for sid in site_ids:
        session = bs_for[sid] if bs_for is not None else bs
        try:
            res = collect_site(session, conn, sid, days, now)
            conn.commit()
        except Exception as e:  # isolate per-site failure
            res = {"site_id": sid, "error": str(e)}
        results.append(res)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_collector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/optimizers/collector.py tests/optimizers/test_collector.py
git commit -m "feat: optimizer collector orchestration (per-site, backfill-aware)"
```

---

### Task 7: CLI + `__main__`

**Files:**
- Create: `solaranalysis/optimizers/cli.py`, `solaranalysis/optimizers/__main__.py`
- Test: `tests/optimizers/test_cli.py`

**Interfaces:**
- Consumes: `collector` (Task 6), `web.db`/`web.repo`/`web.crypto`/`web.paths`, `core.session_store.SessionStore`, `adapters.solaredge.SolarEdgeAdapter`, `adapters._browser.BrowserSession`.
- Produces:
  - `resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]` — `--date` (default: yesterday = `today - 1d`); with `--backfill N` returns N days ending at that date.
  - `main(argv=None, today=None) -> int` — parse args, load the enabled SolarEdge plant's creds from `app.db`, authenticate via `SolarEdgeAdapter`, enumerate (or use `--sites`) and run `collector.collect`, print a one-line summary per site. Returns 0 on success, 2 if no SolarEdge plant is configured.

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_cli.py`:

```python
from datetime import date
from solaranalysis.optimizers import cli


def test_resolve_days_default_is_yesterday():
    assert cli.resolve_days(None, 1, today=date(2026, 7, 24)) == ["2026-07-23"]


def test_resolve_days_explicit_date():
    assert cli.resolve_days("2026-07-10", 1, today=date(2026, 7, 24)) == ["2026-07-10"]


def test_resolve_days_backfill_counts_back_from_target():
    assert cli.resolve_days("2026-07-10", 3, today=date(2026, 7, 24)) == [
        "2026-07-08", "2026-07-09", "2026-07-10"]


def test_resolve_days_backfill_from_yesterday():
    got = cli.resolve_days(None, 90, today=date(2026, 7, 24))
    assert len(got) == 90 and got[-1] == "2026-07-23" and got[0] == "2026-04-25"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.cli'`.

- [ ] **Step 3: Implement the CLI**

Create `solaranalysis/optimizers/cli.py`:

```python
"""Standalone optimizer collector entry point.

    python -m solaranalysis.optimizers --data-dir DIR --app-dir DIR
        [--date YYYY-MM-DD]   # default: yesterday
        [--backfill N]        # N days ending at --date (default 1)
        [--sites 2387929,...] # default: all sites on the account

Loads the enabled SolarEdge plant's credentials from app.db, authenticates
via the SolarEdge adapter (reusing its session cache), and collects per-
optimizer daily energy + inventory into app.db.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta

from dotenv import load_dotenv

from ..adapters._browser import BrowserSession
from ..adapters.solaredge import SolarEdgeAdapter
from ..core.session_store import SessionStore
from ..web import db, repo, crypto
from ..web.paths import Paths
from . import collector, layout_client
from ._now import now_utc  # tiny helper, see below


def resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]:
    target = date.fromisoformat(date_arg) if date_arg else (today - timedelta(days=1))
    return collector.day_range(target, backfill)


def _solaredge_plant_id(conn) -> int | None:
    for p in repo.list_plants(conn):
        if p["platform"] == "solaredge" and p["enabled"]:
            return p["id"]
    return None


def main(argv=None, today=None) -> int:
    ap = argparse.ArgumentParser(prog="solaranalysis.optimizers")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=1)
    ap.add_argument("--sites")
    args = ap.parse_args(argv)

    paths = Paths.create(args.data_dir, args.app_dir)
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    db.init_db(conn)

    plant_id = _solaredge_plant_id(conn)
    if plant_id is None:
        print("no enabled SolarEdge plant configured in app.db")
        return 2

    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    days = resolve_days(args.date, args.backfill, today or date.today())

    adapter = SolarEdgeAdapter(auth, SessionStore(paths.session_cache_dir))
    adapter.login()
    state = adapter._load_session()
    with BrowserSession(storage_state=state) as bs:
        adapter._authenticate(bs, had_state=bool(state))
        adapter._save_session(bs)
        if args.sites:
            site_ids = [int(s) for s in args.sites.split(",") if s.strip()]
        else:
            site_ids = collector.parse_site_ids(layout_client.get_site_list(bs))
        now = now_utc()
        results = collector.collect(conn, site_ids, days, now, bs=bs)

    for r in results:
        if "error" in r:
            print(f"site {r['site_id']}: ERROR {r['error']}")
        else:
            print(f"site {r['site_id']}: {r['optimizers']} optimizers, "
                  f"{r['energy_rows']} energy rows over {r['days']} day(s)")
    conn.close()
    return 0
```

Create `solaranalysis/optimizers/_now.py` (tiny, so `main` stays testable and `date.today()` isn't the only clock):

```python
from __future__ import annotations
from datetime import datetime, timezone


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

Create `solaranalysis/optimizers/__main__.py`:

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (Phase A tests + all new optimizer tests).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/optimizers/cli.py solaranalysis/optimizers/_now.py solaranalysis/optimizers/__main__.py tests/optimizers/test_cli.py
git commit -m "feat: optimizer collector CLI entry point"
```

---

### Task 8 (manual, no unit test): live smoke run + past-date probe

Not a code change — a one-time live verification on a machine with the SolarEdge credentials (spec §9 open item: confirm the energy endpoint accepts past dates before trusting a 90-day backfill).

- [ ] Run a single-day collect: `.venv/Scripts/python.exe -m solaranalysis.optimizers --data-dir ./data --app-dir . --date <yesterday>`. Confirm each of the 4 sites prints a non-zero optimizer + energy-row count, and that `optimizer_energy`/`optimizers` in `data/app.db` are populated.
- [ ] Probe one past date (e.g. `--date <10 days ago>`): confirm non-zero energy rows come back (validates backfill). If a past date returns empty, note it and cap backfill to what the endpoint supports before the B2 analysis relies on history.
- [ ] Then a bounded backfill: `--backfill 90`. Confirm row counts (~229 optimizers × 90 days per site) and that a re-run is idempotent (upsert — no duplicate rows).

---

## Self-Review

**Spec coverage** (`specs/2026-07-23-optimizer-collector-design.md`, B1 portion):
- §3 package layout: `mappers`/`store`/`layout_client`/`collector`/`cli`/`__main__` created (Tasks 2-7). `analyze`/`report` are B2. ✓
- §2 endpoints (logical tree, by-inverter energy, site list) → Task 5, with the verified URLs/params. ✓
- §4 storage (`optimizers`, `optimizer_energy`, shared app.db, additive DDL + version bump) → Task 1. ✓
- §7 CLI (`--date` default yesterday, `--backfill`, `--sites`, all-sites default), backfill, credential load from app.db, session reuse → Tasks 6-7. ✓
- §1 "across all 4 sites" via dynamic enumeration → `parse_site_ids` + `get_site_list` (Task 6-7). ✓
- §9 open items: past-date probe → Task 8; module tilt/azimuth nullable (tree doesn't expose them) → Task 1 columns nullable, not populated. ✓
- Deferred to B2 (correctly out of scope here): analysis (§5), report/email (§6), the daily schedule (§7 scheduling).

**Placeholder scan:** No TBD/TODO; every code step has complete code; every test step shows the test + expected fail/pass. Task 8 is explicitly a manual step, not a code placeholder.

**Type consistency:** `OptimizerInfo`/`OptimizerEnergyRow` fields defined in Tasks 2-3 are used identically in `store` (Task 4) and `collector` (Task 6). `collect_site(bs, conn, site_id, days, now)` and `collect(conn, site_ids, days, now, bs=/bs_for=)` signatures match their tests and the CLI call. `layout_client` function names (`get_site_list`/`get_logical_tree`/`get_by_inverter_energy`) match the collector's calls. `resolve_days(date_arg, backfill, today)` matches its test and `main`. `now_utc()` produced in `_now.py`, consumed in `cli.main`.
