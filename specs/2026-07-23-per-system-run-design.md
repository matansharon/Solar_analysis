# Per-System Analysis Run — Design

Date: 2026-07-23
Status: draft — pending user review

## 1. Purpose

Today a run analyzes **all enabled systems** as one fleet: `start_run(trigger,
time_range)` spawns the runner, which builds its config from every enabled plant
and produces one fleet report + dashboard + email. There is no way to run the
analysis for **one** system on demand — e.g. to re-check a single site after a
fault, or to get a focused report for just that system without waiting for the
whole fleet.

This project lets an operator **run the analysis on a single chosen system** from
the web app. The mechanism is a small generalization, not a new pipeline: a run
gains an optional **target system**. When set, the run fetches, analyzes, reports,
and emails for just that system; when unset, it behaves exactly as today (all
enabled systems). Everything downstream — pipeline, status overview, executive
summary, dashboard, email — already handles *N* systems where *N* can be 1, so it
is reused unchanged.

### Chosen behavior (from brainstorming)

- **Trigger:** a **System picker on the Runs page** — a dropdown next to the
  existing time-range selector, defaulting to "All enabled systems," plus one
  option per enabled system. Then the existing "Run now" button.
- **Delivery:** a single-system run **emails its scoped dashboard** to the
  configured recipients, exactly like the fleet run.
- **Time range:** the single-system run **reuses the same range selector**
  (snapshot / 30d / 12mo / all).

### Non-goals

- **Arbitrary multi-system subsets.** The picker is one-system-or-all. Running an
  explicit subset (systems 2, 3 and 5) is a clean future extension on the same
  column; out of scope here (YAGNI).
- **Per-system schedules.** Schedules stay fleet-wide. The scheduler calls
  `start_run` with no target, so scheduled runs are unchanged.
- **Changing the fleet run's default.** "All enabled systems" remains the
  default and the behavior of every existing/scheduled run.
- **A second run mechanism.** No new endpoint, subprocess, table, or lock — the
  existing `runs` table, runner subprocess, and single-active `Busy` lock are
  reused. A per-system run is just a normal run with a target set.
- **Concurrency.** Still one run/test at a time. A per-system run while another
  run is active returns `Busy` (409), same as today.

## 2. Overview of the flow

```
Runs page: [System ▾ = "All enabled systems" | <system name>]  [Range ▾]  [Run now]
        │  POST /api/runs { time_range, plant_id? }
        ▼
route validates: time_range ok; if plant_id given → system exists AND enabled
        │
        ▼
RunManager.start_run("manual", time_range, plant_id)
        │  repo.create_run(..., plant_id)         ← persists the target (NULL = fleet)
        │  spawn: runner.py --run --run-id N       ← command unchanged
        ▼
runner.run_analysis_job: run = get_run(id); plant_id = run["plant_id"]
        │  cfg, names = build_app_config(conn, key, plant_id=plant_id)
        │       plant_id set  → cfg has just that one enabled system
        │       plant_id None → cfg has all enabled systems (today)
        ▼
run_pipeline(cfg, …) → report → status/summary → dashboard → email   (all reused, N=1 works)
```

The only semantic addition is **which systems `cfg.plants` contains**. Persisting
the target on the run row (rather than passing it as a CLI arg) matches how the
runner already reads `time_range` from the run row.

## 3. Data model — `runs.plant_id`

Add a nullable column `plant_id INTEGER` to the `runs` table.

- **Meaning:** `NULL` = fleet run (all enabled systems) — the default and the
  value for every existing row and every scheduled run. A value = the target
  system's `plants.id`.
- **No foreign-key constraint.** Consistent with the existing `run_id` /
  `config_plant_id` columns elsewhere, which are plain nullable integers so run
  history survives a plant being deleted. A deleted target simply no longer
  resolves to a name in the UI (falls back to "system #N").

### Migration — `web/db.py`

Additive, matching the existing guarded-`ALTER` pattern:

1. Add `plant_id INTEGER` to the `runs` `CREATE TABLE` DDL (for fresh DBs).
2. In `init_db`, alongside the existing `config_plant_id` guard:

   ```python
   if not _has_column(conn, "runs", "plant_id"):
       conn.execute("ALTER TABLE runs ADD COLUMN plant_id INTEGER")
   ```

3. Bump `SCHEMA_VERSION` 3 → 4.

Both the DDL and the `ALTER` are needed: the DDL covers new databases, the
guarded `ALTER` upgrades existing ones on the next startup.

## 4. Backend

### 4a. `web/repo.py`

- `create_run(conn, trigger, time_range, log_path, started_at, plant_id=None)` —
  add the trailing keyword and include the column in the INSERT:

  ```python
  def create_run(conn, trigger, time_range, log_path, started_at, plant_id=None) -> int:
      cur = conn.execute(
          "INSERT INTO runs(status,trigger,time_range,started_at,log_path,plant_id) "
          "VALUES('running',?,?,?,?,?)",
          (trigger, time_range, started_at, log_path, plant_id))
      conn.commit()
      return cur.lastrowid
  ```

- `run_public(row)` — expose the field so the API and runner can read it:

  ```python
  "plant_id": row["plant_id"],
  ```

  (added to the returned dict; `get_run`/`list_runs`/`running_runs` all go through
  `run_public`, so they surface it automatically.)

### 4b. `web/run_manager.py::start_run`

```python
def start_run(self, trigger: str, time_range: str, plant_id: int | None = None) -> int:
    ...
    rid = repo.create_run(conn, trigger=trigger, time_range=time_range,
                          log_path="pending", started_at=_now(), plant_id=plant_id)
    ...
```

Everything else (lock, spawn, pid, pump thread) is unchanged. The spawned command
is unchanged — the runner reads `plant_id` from the run row.

### 4c. `web/runner.py::build_app_config`

Add an optional filter; default preserves today's "all enabled" behavior:

```python
def build_app_config(conn, key, plant_id=None):
    settings = repo.get_app_settings(conn)
    plants, names = [], {}
    for p in repo.list_plants(conn):
        if not p["enabled"]:
            continue
        if plant_id is not None and p["id"] != plant_id:
            continue
        ...
```

A disabled target contributes nothing (the `enabled` check still runs first); the
route (§5) rejects a disabled target up front, so this is defense in depth.

### 4d. `web/runner.py::run_analysis_job`

- Read the target from the run row and pass it through; capture `names` (today
  discarded as `_`) so the target can be named in the subject/subtitle:

  ```python
  run = repo.get_run(conn, run_id)
  time_range = TimeRange(run["time_range"])
  plant_id = run.get("plant_id")
  cfg, names = build_app_config(conn, key, plant_id=plant_id)
  ```

- **Subject / subtitle polish.** Replace the hard-coded `"{n} plants"` token with
  a scope label that names a single target:

  ```python
  if plant_id is not None:
      scope_label = names.get(plant_id, f"system {plant_id}")
  else:
      scope_label = f"{len(res['plants'])} plants"
  subtitle = f"{scope_label} · range {run['time_range']} · {stamp} UTC"
  ...
  subject = (f"Solar Fleet Analysis · {status} · {scope_label} "
             f"· range {run['time_range']} · {stamp} UTC")
  ```

  The `render_html` title ("Solar Fleet Analysis") is unchanged. Only the
  subtitle/subject scope token changes; fleet runs read identically to today.

- The `run_start` event's `plants` list already comes from `cfg.plants`, so for a
  single-system run it naturally reports just that one system.

## 5. API — `web/routes/runs.py`

```python
class RunBody(BaseModel):
    time_range: str
    plant_id: int | None = None


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

- Adds `conn=Depends(_conn)` (the route currently has none) to validate the
  target. Unknown or disabled target → `422` (mirrors the disabled-plant guard on
  `POST /api/plants/{id}/test`, which uses 409; 422 is used here for consistency
  with this route's existing "invalid time_range" 422).
- Omitting `plant_id` (or sending `null`) → `start_run(..., plant_id=None)` → the
  unchanged fleet run.

## 6. Frontend

### 6a. `frontend/src/api.ts`

- `Run` interface gains `plant_id: number | null;`.
- `startRun` sends the target:

  ```ts
  startRun: (time_range: TimeRange, plantId?: number | null) =>
    req<{ id: number }>("POST", "/api/runs", { time_range, plant_id: plantId ?? null }),
  ```

### 6b. `frontend/src/routes/Runs.tsx`

- Load systems for the picker (reuse the existing plants query key):

  ```ts
  const { data: plants } = useQuery({ queryKey: ["plants"], queryFn: api.plants });
  const enabledPlants = (plants ?? []).filter((p) => p.enabled);
  const [plantId, setPlantId] = useState<number | null>(null);   // null = all
  ```

- A **System** `<select>` before the range selector in the header `btn-row`:
  - value `""` → **"All enabled systems"** (sets `plantId` to `null`)
  - one `<option value={p.id}>{p.name}</option>` per enabled system
- "Run now" calls `api.startRun(range, plantId)`. The existing `startError`
  banner already surfaces the `422`/`busy` messages.
- Runs list: add a **System** column. Build an id→name map from the plants query;
  each row shows `run.plant_id == null ? "All" : (nameById[run.plant_id] ?? \`#${run.plant_id}\`)`.
  `RunRow` takes the map (or the resolved label) as a prop.

The frontend has no unit-test suite today; these changes are verified through the
e2e/smoke path (§7, §8).

## 7. Edge cases & failure isolation

- **Target disabled/deleted between picking and submitting** → route returns
  `422`, shown in the `startError` banner; no run is created.
- **Target fails to fetch during the run** → identical to the fleet case where
  every plant is skipped: `run_pipeline` returns no plants, the report is "No
  plant data available." plus the "Unavailable Plants" section, status becomes
  `partial`, and the run emails that report (no dashboard). No new path.
- **Rare race — target disabled after validation, before the subprocess reads
  it** → `build_app_config` filters it out, the run yields 0 plants and finishes
  `success` with an empty report, exactly as a fleet run with zero enabled
  systems would. Acceptable; not separately guarded.
- **Busy** → unchanged (409).
- **Backward compatibility** → every existing/scheduled run stores `plant_id =
  NULL` and is byte-for-byte the same run as before.

## 8. Testing (TDD)

- `tests/web/test_db.py` — `test_init_db_adds_plant_id_to_old_runs`: create a
  `runs` table without `plant_id` (old schema), run `init_db`, assert the column
  now exists and `SCHEMA_VERSION` is 4.
- `tests/web/test_repo_runs.py` —
  - `test_create_run_persists_plant_id`: `create_run(..., plant_id=7)` →
    `get_run` returns `plant_id == 7`.
  - `test_create_run_default_plant_id_null`: no `plant_id` → `get_run` returns
    `plant_id is None`.
  - `run_public` exposes `plant_id` (asserted via the above).
- `tests/web/test_runner.py` —
  - `test_build_app_config_filters_to_plant_id`: with two enabled plants, passing
    one id yields a single-plant cfg and a `names` map of just that id.
  - `test_build_app_config_none_is_all_enabled`: `plant_id=None` → all enabled
    (guards the default).
  - `test_run_job_scopes_pipeline_to_plant_id`: a run row with `plant_id` set →
    the (stubbed) pipeline receives a cfg containing only that system; the
    subtitle/subject scope label is the system name, not "1 plants".
- `tests/web/test_run_manager.py` — `test_start_run_passes_plant_id`: with a fake
  spawn, `start_run(..., plant_id=5)` persists `plant_id=5` on the created run.
- `tests/web/test_api_runs.py` —
  - `test_create_run_accepts_plant_id`: `POST {time_range, plant_id}` with an
    enabled plant → 201 and `start_run` is called with that `plant_id` (fake
    run_manager records the call).
  - `test_create_run_rejects_unknown_plant`: unknown id → 422.
  - `test_create_run_rejects_disabled_plant`: disabled id → 422.
  - `test_create_run_without_plant_id_is_fleet`: omitted → `start_run` called with
    `plant_id=None` → 201 (guards backward compatibility).

## 9. Docs

- `README.md`: note that a run can target a single system — the Runs page has a
  system picker (default "All enabled systems") beside the time-range selector; a
  single-system run produces and emails a scoped report/dashboard for just that
  system, and appears in the run history labeled with the system name.
