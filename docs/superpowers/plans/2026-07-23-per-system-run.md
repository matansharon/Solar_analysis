# Per-System Analysis Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the web app run the full analysis on a single chosen system, not just the whole fleet, by giving a run an optional target system (`plant_id`).

**Architecture:** A run gains a nullable `runs.plant_id`. `NULL` = today's fleet run (all enabled systems); a value = "just this system." The runner filters `build_app_config` to the target and the existing pipeline/report/status/dashboard/email path runs unchanged (it already handles *N* systems, *N*=1 works). The Runs page gets a system picker beside the time-range selector. No new endpoint, subprocess, table, or lock.

**Tech Stack:** Python 3.10, FastAPI, SQLite (`sqlite3`), pytest; React 18 + Vite + TypeScript + @tanstack/react-query frontend.

Spec: `specs/2026-07-23-per-system-run-design.md`.

## Global Constraints

- **Backward compatibility is load-bearing.** `plant_id = NULL` must mean the exact fleet run of today. Every existing row, every scheduled run (the scheduler calls `start_run` with no target), and any `POST /api/runs` without `plant_id` must behave byte-for-byte as before.
- **`runs.plant_id` is a plain nullable `INTEGER`, no foreign key** — matches the existing nullable `run_id`/`config_plant_id` columns so run history survives a plant deletion.
- **Unknown or disabled target → HTTP 422** on `POST /api/runs` (chosen for consistency with that route's existing "invalid time_range" 422, not the 409 used elsewhere).
- **No new secrets, lock, subprocess, or table.** Reuse the `runs` table, the single-active `Busy` lock, and the existing runner subprocess. The spawned command line is unchanged — the runner reads `plant_id` from the run row.
- **Subject/subtitle name a single target** (e.g. `… · Beta · range snapshot · …`) instead of "1 plants"; the `render_html` report title stays "Solar Fleet Analysis".
- **Test runner:** `.venv/Scripts/python.exe -m pytest`. Frontend build/type-check: `npm run build` in `frontend/`.
- **Commit messages:** conventional style (`feat:`/`docs:`), **no AI attribution**.

## File Structure

- `solaranalysis/web/db.py` — add `plant_id` to the `runs` DDL, guarded `ALTER`, bump `SCHEMA_VERSION` → 4. (Task 1)
- `solaranalysis/web/repo.py` — `create_run` persists `plant_id`; `run_public` exposes it. (Task 1)
- `solaranalysis/web/run_manager.py` — `start_run` accepts + forwards `plant_id`. (Task 2)
- `solaranalysis/web/runner.py` — `build_app_config` filter; `run_analysis_job` threads `plant_id` + scope label. (Task 3)
- `solaranalysis/web/routes/runs.py` — `RunBody.plant_id`, validation, pass-through. (Task 4)
- `frontend/src/api.ts`, `frontend/src/routes/Runs.tsx` — picker, `startRun`, System column. (Task 5)
- `README.md` — document per-system runs. (Task 6)
- Tests: `tests/web/test_db.py`, `tests/web/test_repo_runs.py`, `tests/web/test_run_manager.py`, `tests/web/test_runner.py`, `tests/web/test_api_runs.py`.

---

### Task 1: Persist `plant_id` on runs (schema + repo)

**Files:**
- Modify: `solaranalysis/web/db.py` (runs DDL, `init_db`, `SCHEMA_VERSION`)
- Modify: `solaranalysis/web/repo.py` (`create_run`, `run_public`)
- Test: `tests/web/test_db.py`, `tests/web/test_repo_runs.py`

**Interfaces:**
- Produces: `runs.plant_id` column (nullable INTEGER); `repo.create_run(conn, trigger, time_range, log_path, started_at, plant_id=None) -> int`; `run_public(row)` dict now includes `"plant_id"`. `get_run`/`list_runs`/`running_runs` surface `plant_id` for free (all go through `run_public`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_db.py` (after the existing `_V2_DDL` migration test):

```python
_V3_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  status TEXT, trigger TEXT, time_range TEXT, runner_pid INTEGER,
  started_at TEXT, finished_at TEXT, report_path TEXT, log_path TEXT,
  plants_summary TEXT, skipped_plants TEXT, notes TEXT, error TEXT
);
"""


def test_v3_db_migrates_to_v4_adds_run_plant_id(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    c.executescript(_V3_RUNS_DDL)  # runs table pre-exists WITHOUT plant_id
    c.execute("INSERT INTO settings(key,value) VALUES('schema_version','3')")
    c.commit()
    db.init_db(c)  # CREATE ... IF NOT EXISTS skips runs; guarded ALTER adds the column
    run_cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
    assert "plant_id" in run_cols
    ver = c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    assert ver["value"] == str(db.SCHEMA_VERSION)
```

Add to `tests/web/test_repo_runs.py`:

```python
def test_create_run_persists_plant_id(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log",
                          started_at="2026-07-23T00:00:00", plant_id=7)
    assert repo.get_run(c, rid)["plant_id"] == 7


def test_create_run_defaults_plant_id_null(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="scheduled", time_range="all",
                          log_path="logs/run-2.log",
                          started_at="2026-07-23T00:00:00")
    assert repo.get_run(c, rid)["plant_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db.py::test_v3_db_migrates_to_v4_adds_run_plant_id tests/web/test_repo_runs.py::test_create_run_persists_plant_id tests/web/test_repo_runs.py::test_create_run_defaults_plant_id_null -v`
Expected: FAIL — migration test fails (no `plant_id` column and/or version still 3); repo tests fail with `TypeError: create_run() got an unexpected keyword argument 'plant_id'` (and/or `KeyError: 'plant_id'`).

- [ ] **Step 3: Implement the schema migration in `db.py`**

Bump the version:

```python
SCHEMA_VERSION = 4
```

In the `runs` `CREATE TABLE` inside `_DDL`, add `plant_id` as the last column (change the final `  error TEXT` line):

```python
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('running','success','partial','failed','cancelled','interrupted')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduled')),
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  runner_pid INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  report_path TEXT,
  log_path TEXT NOT NULL,
  plants_summary TEXT,
  skipped_plants TEXT,
  notes TEXT,
  error TEXT,
  plant_id INTEGER
);
```

In `init_db`, add a guarded `ALTER` next to the existing `config_plant_id` loop (after that `for` loop, before the settings upsert):

```python
    if not _has_column(conn, "runs", "plant_id"):
        conn.execute("ALTER TABLE runs ADD COLUMN plant_id INTEGER")
```

- [ ] **Step 4: Implement the repo changes in `repo.py`**

`create_run` — add the trailing keyword and the column:

```python
def create_run(conn, trigger, time_range, log_path, started_at, plant_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO runs(status,trigger,time_range,started_at,log_path,plant_id) "
        "VALUES('running',?,?,?,?,?)",
        (trigger, time_range, started_at, log_path, plant_id))
    conn.commit()
    return cur.lastrowid
```

`run_public` — add the field to the returned dict (e.g. right after the `"notes"`/`"error"` entries):

```python
        "notes": _dec(row["notes"]), "error": row["error"],
        "plant_id": row["plant_id"],
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db.py tests/web/test_repo_runs.py -v`
Expected: PASS (new tests plus the existing `test_run_lifecycle`, `test_mark_interrupted`, all migration tests).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/web/db.py solaranalysis/web/repo.py tests/web/test_db.py tests/web/test_repo_runs.py
git commit -m "feat: persist optional target plant_id on runs"
```

---

### Task 2: `start_run` accepts a target system

**Files:**
- Modify: `solaranalysis/web/run_manager.py` (`RunManager.start_run`)
- Test: `tests/web/test_run_manager.py`

**Interfaces:**
- Consumes: `repo.create_run(..., plant_id=None)` from Task 1.
- Produces: `RunManager.start_run(self, trigger, time_range, plant_id=None) -> int` — persists `plant_id` on the created run. Spawn command unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_run_manager.py`:

```python
def test_start_run_persists_plant_id(tmp_path):
    paths = _paths(tmp_path)
    proc = FakeProc([_ev({"event": "run_complete", "status": "failed"})], code=1)
    proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d", plant_id=3)
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["plant_id"] == 3


def test_start_run_default_plant_id_is_null(tmp_path):
    paths = _paths(tmp_path)
    proc = FakeProc([_ev({"event": "run_complete", "status": "failed"})], code=1)
    proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["plant_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_run_manager.py::test_start_run_persists_plant_id -v`
Expected: FAIL — `TypeError: start_run() got an unexpected keyword argument 'plant_id'`.

- [ ] **Step 3: Implement the change in `run_manager.py`**

Change the `start_run` signature and the `create_run` call (leave everything else in the method untouched):

```python
    def start_run(self, trigger: str, time_range: str, plant_id: int | None = None) -> int:
        with self._lock:
            if self._active:
                raise Busy({"kind": self._active["kind"], "id": self._active["id"]})
            conn = db.connect(self.paths.db_path)
            log_rel = ""  # set after we know the id
            rid = repo.create_run(conn, trigger=trigger, time_range=time_range,
                                  log_path="pending", started_at=_now(),
                                  plant_id=plant_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_run_manager.py -v`
Expected: PASS (new tests plus existing `test_start_run_success_finalizes`, `test_busy_rejects_second_start`, etc.).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/run_manager.py tests/web/test_run_manager.py
git commit -m "feat: start_run accepts an optional target plant_id"
```

---

### Task 3: Runner scopes the run to the target system

**Files:**
- Modify: `solaranalysis/web/runner.py` (`build_app_config`, `run_analysis_job`)
- Test: `tests/web/test_runner.py`

**Interfaces:**
- Consumes: `run["plant_id"]` from `repo.get_run` (Task 1).
- Produces: `build_app_config(conn, key, plant_id=None) -> (AppConfig, dict[int,str])` — when `plant_id` is set, `cfg.plants` contains only that one enabled system and `names` maps just that id. `run_analysis_job` names a single target in the email subject and report subtitle.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_runner.py` (a two-plant seed helper plus three tests):

```python
def _seed_two(paths):
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    key = crypto.load_or_create_key(paths.key_path)
    repo.create_plant(conn, key, {"name": "Alpha", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "pw"})
    repo.create_plant(conn, key, {"name": "Beta", "platform": "growatt",
                                  "auth_mode": "password", "username": "u2",
                                  "password": "pw2"})
    return conn, key


def test_build_app_config_filters_to_plant_id(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    target = next(p for p in repo.list_plants(conn) if p["name"] == "Beta")
    cfg, names = runner.build_app_config(conn, key, plant_id=target["id"])
    assert [p.name for p in cfg.plants] == ["Beta"]
    assert names == {target["id"]: "Beta"}


def test_build_app_config_none_is_all_enabled(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    cfg, _ = runner.build_app_config(conn, key)
    assert {p.name for p in cfg.plants} == {"Alpha", "Beta"}


def test_run_job_scopes_pipeline_to_target(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    beta = next(p for p in repo.list_plants(conn) if p["name"] == "Beta")
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-23T00:00:00",
                    plant_id=beta["id"])
    conn.close()

    seen = {}
    from solaranalysis.core.schema import PlantData

    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
        seen["plants"] = [p.name for p in cfg.plants]
        return {"report_md": "# R", "plants": [PlantData(
                    plant_id="b", source_platform="growatt",
                    source_plant_id="1", plant_name="Beta")],
                "verify_missing": [], "skipped_plants": []}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)

    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))

    runner.run_analysis_job(paths, run_id=1)
    assert seen["plants"] == ["Beta"]              # pipeline saw only the target
    assert "Beta" in sent[0]                       # subject names the system
    assert "1 plants" not in sent[0]
```

(The autouse `_stub_llm_calls` fixture already stubs `summarize_executive`, `status_overview`, `design_charts`, and `compose_dashboard`, so no network is hit.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_runner.py::test_build_app_config_filters_to_plant_id tests/web/test_runner.py::test_run_job_scopes_pipeline_to_target -v`
Expected: FAIL — `build_app_config` filter test fails with `TypeError: build_app_config() got an unexpected keyword argument 'plant_id'`; the scope test fails on the subject assertion (currently "2 plants"/"1 plants").

- [ ] **Step 3: Implement the `build_app_config` filter**

Add the `plant_id` parameter and the per-plant filter (the `enabled` check stays first):

```python
def build_app_config(conn, key, plant_id=None):
    settings = repo.get_app_settings(conn)
    plants, names = [], {}
    for p in repo.list_plants(conn):
        if not p["enabled"]:
            continue
        if plant_id is not None and p["id"] != plant_id:
            continue
        auth = repo.load_plant_auth(conn, key, p["id"])
        plants.append(PlantConfig(name=p["name"], auth=auth,
                                  tariff_per_kwh=p["tariff_per_kwh"],
                                  currency=p["currency"], config_id=p["id"]))
        names[p["id"]] = p["name"]
    cfg = AppConfig(plants=plants, model=settings["model"],
                    max_input_tokens=settings["max_input_tokens"],
                    output_language=settings["output_language"])
    return cfg, names
```

- [ ] **Step 4: Implement the `run_analysis_job` threading + scope label**

Read the target from the run row and capture `names` (change the two existing lines around 66-67):

```python
        run = repo.get_run(conn, run_id)
        time_range = TimeRange(run["time_range"])
        plant_id = run.get("plant_id")
        cfg, names = build_app_config(conn, key, plant_id=plant_id)
```

Build the scope label and use it in the subtitle (change the existing `stamp`/`subtitle` lines ~117-118):

```python
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if plant_id is not None:
            scope_label = names.get(plant_id, f"system {plant_id}")
        else:
            scope_label = f"{len(res['plants'])} plants"
        subtitle = f"{scope_label} · range {run['time_range']} · {stamp} UTC"
```

Use it in the subject (change the existing `subject` assignment ~147-148):

```python
        subject = (f"Solar Fleet Analysis · {status} · {scope_label} "
                   f"· range {run['time_range']} · {stamp} UTC")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_runner.py -v`
Expected: PASS — new tests plus all existing runner tests (including `test_run_job_emails_on_success`, which still asserts the subject starts with "Solar Fleet Analysis"; a fleet run's `scope_label` is `"{n} plants"`, so that prefix is unchanged).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/web/runner.py tests/web/test_runner.py
git commit -m "feat: runner scopes analysis to a target system when set"
```

---

### Task 4: API accepts and validates a target system

**Files:**
- Modify: `solaranalysis/web/routes/runs.py` (`RunBody`, `create_run`)
- Test: `tests/web/test_api_runs.py`

**Interfaces:**
- Consumes: `RunManager.start_run(..., plant_id=None)` (Task 2); `repo.get_plant` (existing).
- Produces: `POST /api/runs` accepts optional `plant_id`; validates it exists and is enabled (else 422); forwards it to `start_run`.

- [ ] **Step 1: Update the test fake and write the failing tests**

In `tests/web/test_api_runs.py`, update `FakeRM` so its `start_run` matches the new signature and records the target (this also keeps the existing `test_create_run_ok`/`test_create_run_busy` passing):

```python
class FakeRM:
    def __init__(self, busy=False):
        self.busy = busy
        self.cancelled = None
        self.last_plant_id = None
        self._progress = {"plants": {"A": "running"}, "status": "running"}
    def start_run(self, trigger, time_range, plant_id=None):
        self.last_plant_id = plant_id
        if self.busy:
            raise Busy({"kind": "run", "id": 1})
        return 5
    def get_progress(self, rid): return self._progress
    def cancel(self, rid):
        self.cancelled = rid; return True
```

Add a plant-seeding helper and the new tests:

```python
def _seed_plant(paths, *, enabled=True):
    from solaranalysis.web import crypto
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    pid = repo.create_plant(conn, key, {"name": "Sys", "platform": "growatt",
                                        "auth_mode": "password", "username": "u",
                                        "password": "pw", "enabled": enabled})
    conn.close()
    return pid


def test_create_run_accepts_plant_id(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    pid = _seed_plant(paths)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": pid})
    assert r.status_code == 201 and r.json()["id"] == 5
    assert rm.last_plant_id == pid


def test_create_run_rejects_unknown_plant(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": 999})
    assert r.status_code == 422
    assert rm.last_plant_id is None


def test_create_run_rejects_disabled_plant(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    pid = _seed_plant(paths, enabled=False)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": pid})
    assert r.status_code == 422
    assert rm.last_plant_id is None


def test_create_run_without_plant_id_is_fleet(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 201
    assert rm.last_plant_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_api_runs.py -v`
Expected: FAIL — `test_create_run_accepts_plant_id` fails (a valid `plant_id` is currently ignored, so `rm.last_plant_id` is `None`); `test_create_run_rejects_unknown_plant`/`..._disabled_plant` fail (currently return 201, not 422). (`test_create_run_ok`, `test_create_run_busy`, `test_create_run_without_plant_id_is_fleet` pass once the `FakeRM` signature is updated.)

- [ ] **Step 3: Implement the route changes in `routes/runs.py`**

Add the optional field to the body:

```python
class RunBody(BaseModel):
    time_range: str
    plant_id: int | None = None
```

Add validation and pass-through in `create_run` (add the `conn` dependency — the route currently has none):

```python
@router.post("")
def create_run(body: RunBody, request: Request, conn=Depends(_conn)):
    if body.time_range not in _RANGES:
        return JSONResponse({"detail": "invalid time_range"}, status_code=422)
    if body.plant_id is not None:
        p = repo.get_plant(conn, body.plant_id)
        if not p:
            return JSONResponse({"detail": "system not found"}, status_code=422)
        if not p["enabled"]:
            return JSONResponse({"detail": "system is disabled"}, status_code=422)
    rm = request.app.state.run_manager
    try:
        rid = rm.start_run("manual", body.time_range, plant_id=body.plant_id)
    except Busy as b:
        return JSONResponse({"detail": "busy", "active": b.active}, status_code=409)
    return JSONResponse({"id": rid}, status_code=201)
```

(`_conn`, `repo`, and `Busy` are already imported in this module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_api_runs.py -v`
Expected: PASS (all six run-creation tests, plus the cancel/log/progress tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/routes/runs.py tests/web/test_api_runs.py
git commit -m "feat: POST /api/runs accepts and validates an optional target system"
```

---

### Task 5: Frontend — system picker on the Runs page

**Files:**
- Modify: `frontend/src/api.ts` (`Run` interface, `startRun`)
- Modify: `frontend/src/routes/Runs.tsx` (picker, mutation call, System column)

**Interfaces:**
- Consumes: `POST /api/runs` with optional `plant_id` (Task 4); the existing `api.plants()` query.
- Produces: a "System" `<select>` (default "All enabled systems") beside the range selector; a "System" column in the runs table.

- [ ] **Step 1: Update `api.ts`**

Add `plant_id` to the `Run` interface (place it beside `report_path`/`log_path`):

```ts
  report_path: string | null; log_path: string; plant_id: number | null;
```

Change `startRun` to send the target:

```ts
  startRun: (time_range: TimeRange, plantId?: number | null) =>
    req<{ id: number }>("POST", "/api/runs", { time_range, plant_id: plantId ?? null }),
```

- [ ] **Step 2: Update `Runs.tsx`**

Load the plants for the picker and add target state. In the `Runs` component, after the existing `runs` query, add:

```tsx
  const { data: plants } = useQuery({ queryKey: ["plants"], queryFn: api.plants });
  const enabledPlants = (plants ?? []).filter((p) => p.enabled);
  const nameById = new Map(enabledPlants.map((p) => [p.id, p.name] as const));
  const [plantId, setPlantId] = useState<number | null>(null); // null = all enabled
```

Change the mutation to send the target:

```tsx
  const startRun = useMutation({
    mutationFn: () => api.startRun(range, plantId),
```

Add the System `<select>` as the first control in the header `btn-row` (before the range selector):

```tsx
          <select
            className="field__select"
            value={plantId ?? ""}
            onChange={(e) => setPlantId(e.target.value === "" ? null : Number(e.target.value))}
            aria-label="System for new run"
          >
            <option value="">All enabled systems</option>
            {enabledPlants.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
```

Add a "System" column header (between "Range" and "Started"):

```tsx
                  <th>Range</th>
                  <th>System</th>
                  <th>Started</th>
```

Pass the resolved label into each row — change the map to:

```tsx
                {runs.map((r) => (
                  <RunRow
                    key={r.id}
                    run={r}
                    systemLabel={r.plant_id == null ? "All" : (nameById.get(r.plant_id) ?? `#${r.plant_id}`)}
                  />
                ))}
```

Update `RunRow` to accept and render the label:

```tsx
function RunRow({ run, systemLabel }: { run: Run; systemLabel: string }) {
  return (
    <tr>
      <td className="mono">#{run.id}</td>
      <td>
        <RunStatusChip status={run.status} />
      </td>
      <td className="cell-muted">{run.trigger}</td>
      <td className="cell-muted">{run.time_range}</td>
      <td className="cell-muted">{systemLabel}</td>
      <td className="cell-timestamp">{formatTimestamp(run.started_at)}</td>
      <td className="cell-timestamp">{formatDuration(run.started_at, run.finished_at)}</td>
      <td>
        <Link className="btn btn--ghost btn--small" to={`/runs/${run.id}`}>
          View
        </Link>
      </td>
    </tr>
  );
}
```

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npm run build`
Expected: `tsc -b` reports no type errors and `vite build` writes `frontend/dist/` successfully.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts frontend/src/routes/Runs.tsx
git commit -m "feat: Runs page can target a single system"
```

---

### Task 6: Document per-system runs

**Files:**
- Modify: `README.md` (`## Web UI` intro)

- [ ] **Step 1: Add the documentation**

In `README.md`, extend the `## Web UI` intro paragraph (the one beginning "A local web app (FastAPI + React) …") by appending this sentence after "…without editing YAML.":

```markdown
A manual run can target **all enabled systems** (the default) or **a single
system** chosen from the picker on the Runs page; a single-system run produces
and emails a report/dashboard scoped to just that system and is labeled with its
name in the run history.
```

- [ ] **Step 2: Verify the docs render**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: full suite green (docs-only change; this is the whole-suite gate before the final review).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: note per-system analysis runs in the web UI"
```

---

## Self-Review

**Spec coverage:** §3 data model → Task 1; §4a repo → Task 1; §4b `start_run` → Task 2; §4c/§4d runner → Task 3; §5 API → Task 4; §6 frontend → Task 5; §9 docs → Task 6. §7 edge cases are covered by existing behavior (verified by leaving the skipped/partial and fleet paths' tests green) and the 422 tests in Task 4. All §8 test cases are assigned.

**Placeholder scan:** No TBD/TODO; every code step shows the actual code and every run step shows the exact command and expected result.

**Type/name consistency:** `create_run(..., plant_id=None)` is defined in Task 1 and called with `plant_id=` in Task 2 (`run_manager`) and Task 3's test; `build_app_config(conn, key, plant_id=None) -> (cfg, names)` defined in Task 3 and consumed by `run_analysis_job` in the same task; `start_run(..., plant_id=None)` defined in Task 2 and called in Task 4's route + `FakeRM`; `run_public` adds `plant_id`, consumed as `run.get("plant_id")` (runner) and `r.plant_id` (frontend `Run` type). The frontend `startRun(time_range, plantId?)` matches the `POST /api/runs {time_range, plant_id}` body the route parses.
