# Daily Raw-Snapshot Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a full raw daily snapshot for every system (untouched portal JSON) and a clean per-site daily energy point, so real day-by-day history accumulates from the app's daily run.

**Architecture:** Add raw-response capture to the shared `BrowserSession`, opted into per-adapter via a `record_raw` flag set by the pipeline; the recorded payloads ride back on `PlantData.raw_payloads` and are persisted (zlib-compressed) into a new `raw_payloads` table by `measurements.save_measurements`. Separately, the SolarEdge mapper emits `energyYesterday` as a day-granularity `energy_points` row. Turning on the daily schedule is an ops step.

**Tech Stack:** Python 3.10, stdlib `sqlite3` + `zlib`, Playwright (browser sessions), pytest.

## Global Constraints

- Use `python` (not `python3`) on this machine; interpreter is `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`. Run tests with the project venv: `.venv/Scripts/python.exe -m pytest`.
- On Windows, run test commands with `PYTHONUTF8=1` when Hebrew plant names may print.
- DB migrations are **additive-only** (`CREATE TABLE IF NOT EXISTS`); bump `SCHEMA_VERSION`. `init_db` runs on every startup — no manual migration step.
- Persistence must **never fail a run**: the runner's `persist` callback already wraps `save_measurements` in try/except; keep new persistence inside that boundary.
- The CLI and existing runs stay **byte-for-byte unchanged**: `record_raw` defaults to `False`; the raw path is inert unless the web runner turns it on.
- Commits carry **no AI attribution** (see the clean-commits skill): no `Co-Authored-By: Claude`/Anthropic lines, no AI mentions in messages.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit.

## File Structure

**Create:**
- `tests/web/test_db_schema.py` — schema/migration assertions for `raw_payloads`
- `tests/test_schema_raw.py` — `RawPayload` + `PlantData.to_dict` exclusion
- `tests/test_pipeline_record_raw.py` — `run_pipeline(record_raw=…)` propagation
- `tests/web/test_measurements_raw.py` — raw persistence round-trip
- `tests/test_solaredge_daily.py` — `energyYesterday` → daily `EnergyPoint`

**Modify:**
- `solaranalysis/web/db.py` — new table DDL + `SCHEMA_VERSION` → 5
- `solaranalysis/core/schema.py` — `RawPayload` dataclass, `PlantData.raw_payloads`, `to_dict` exclusion
- `solaranalysis/adapters/_browser.py` — raw capture on `BrowserSession` + `raw_label`
- `solaranalysis/adapters/base.py` — `record_raw` + `_begin_raw`/`_finish_raw`
- `solaranalysis/adapters/solaredge.py` — wire raw capture; `map_solaredge_fleet` yesterday point
- `solaranalysis/adapters/growatt.py` — wire raw capture (web path)
- `solaranalysis/adapters/sma.py` — wire raw capture
- `solaranalysis/pipeline.py` — `record_raw` param → `adapter.record_raw`
- `solaranalysis/web/runner.py` — pass `record_raw=True`
- `solaranalysis/core/measurements.py` — persist `raw_payloads`

**Add tests to existing files:**
- `tests/test_browser.py` — raw-capture tests
- `tests/test_adapter_base.py` — `_begin_raw`/`_finish_raw` tests

---

### Task 1: `raw_payloads` table + schema bump

**Files:**
- Modify: `solaranalysis/web/db.py`
- Test: `tests/web/test_db_schema.py`

**Interfaces:**
- Produces: a `raw_payloads` table with columns `(id, run_id, config_plant_id, plant_uid, platform, endpoint_label, url, method, status, fetched_at_utc, payload_zjson)`; `SCHEMA_VERSION == 5`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_db_schema.py`:

```python
from solaranalysis.web import db


def test_init_db_creates_raw_payloads_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "raw_payloads" in tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_payloads)")}
    assert {"run_id", "plant_uid", "platform", "endpoint_label",
            "payload_zjson", "fetched_at_utc"} <= cols
    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == "5"


def test_init_db_idempotent():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.init_db(conn)  # must not raise
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db_schema.py -v`
Expected: FAIL — `test_init_db_creates_raw_payloads_and_bumps_version` asserts `"5"` but the DB reports `"4"`, and `raw_payloads` is missing.

- [ ] **Step 3: Add the table DDL and bump the version**

In `solaranalysis/web/db.py`, change the version constant:

```python
SCHEMA_VERSION = 5
```

Append this block inside the `_DDL = """ … """` string, right before the closing `"""` (after the `power_points` table):

```sql
CREATE TABLE IF NOT EXISTS raw_payloads(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                  -- NULL for CLI runs
  config_plant_id INTEGER,
  plant_uid TEXT NOT NULL,         -- PlantData.plant_id (e.g. 'solaredge-2387929')
  platform TEXT NOT NULL,
  endpoint_label TEXT NOT NULL,    -- short tag from the URL, e.g. 'sitesMeasurements'
  url TEXT,
  method TEXT,
  status INTEGER,
  fetched_at_utc TEXT NOT NULL,
  payload_zjson BLOB NOT NULL      -- zlib-compressed UTF-8 JSON body
);
CREATE INDEX IF NOT EXISTS ix_raw_payloads_plant
  ON raw_payloads(plant_uid, fetched_at_utc);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_db_schema.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/db.py tests/web/test_db_schema.py
git commit -m "feat: add raw_payloads table (schema v5)"
```

---

### Task 2: `RawPayload` type + `PlantData.raw_payloads` + `to_dict` exclusion

**Files:**
- Modify: `solaranalysis/core/schema.py`
- Test: `tests/test_schema_raw.py`

**Interfaces:**
- Produces: `RawPayload(endpoint_label: str, url: str, method: str, status: int | None, body: object)`; `PlantData.raw_payloads: list[RawPayload]` (defaults `[]`); `PlantData.to_dict()` never contains a `"raw_payloads"` key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_raw.py`:

```python
from solaranalysis.core.schema import PlantData, RawPayload


def test_raw_payloads_defaults_empty():
    pd = PlantData("uid", "solaredge", "1", "Site")
    assert pd.raw_payloads == []


def test_to_dict_excludes_raw_payloads_but_keeps_other_fields():
    pd = PlantData("uid", "solaredge", "1", "Site")
    pd.raw_payloads = [RawPayload("meas", "http://x/meas", "GET", 200, {"a": 1})]
    d = pd.to_dict()
    assert "raw_payloads" not in d
    assert d["plant_id"] == "uid"
    assert d["energy_today_kwh"]["unit"] == "kWh"  # nested dataclass still converts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema_raw.py -v`
Expected: FAIL — `ImportError: cannot import name 'RawPayload'`.

- [ ] **Step 3: Add the dataclass, field, and to_dict exclusion**

In `solaranalysis/core/schema.py`, add after the `PowerPoint` dataclass:

```python
@dataclass
class RawPayload:
    endpoint_label: str
    url: str
    method: str
    status: int | None
    body: object  # JSON-serializable portal response (dict/list/scalar)
```

In `PlantData`, add this field in the "pipeline metadata" group (after `data_quality_flags`):

```python
    # untouched portal responses for this fetch, persisted verbatim when the
    # web runner enables raw capture; excluded from to_dict (never fed to the LLM).
    raw_payloads: list = field(default_factory=list)
```

Replace the body of `to_dict` so it skips `raw_payloads` at the top level:

```python
    def to_dict(self) -> dict:
        def convert(o):
            if isinstance(o, Enum):
                return o.value
            if isinstance(o, list):
                return [convert(x) for x in o]
            if dataclasses.is_dataclass(o):
                return {f.name: convert(getattr(o, f.name)) for f in dataclasses.fields(o)}
            return o
        return {f.name: convert(getattr(self, f.name))
                for f in dataclasses.fields(self) if f.name != "raw_payloads"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema_raw.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full core suite to confirm nothing else serializes raw**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q -k "schema or analyze or rollup or validate or measurements"`
Expected: PASS (no regressions from the `to_dict` change).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/core/schema.py tests/test_schema_raw.py
git commit -m "feat: RawPayload type + PlantData.raw_payloads (excluded from to_dict)"
```

---

### Task 3: `BrowserSession` raw capture

**Files:**
- Modify: `solaranalysis/adapters/_browser.py`
- Test: `tests/test_browser.py` (add tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `raw_label(url: str) -> str`; `BrowserSession.start_raw_capture()`, `BrowserSession.raw_records() -> list[dict]` (each `{"url","method","status","body"}`). `get_json`/`post_json` append a record while recording. Recording is off until `start_raw_capture()` is called.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_browser.py` (top-level, after the existing `capture()` tests). The `FakeResp` and `_session_with_fake_page` helpers already exist in this file; add a `FakeReq` for the request path:

```python
import types as _types
from solaranalysis.adapters._browser import raw_label


class FakeReq:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status = payload, ok, status
    def get(self, url):
        return self
    def post(self, url, **kw):
        return self
    def json(self):
        return self._p


def test_raw_label_extracts_last_path_segment():
    assert raw_label("https://h/services/sitelist/sitesMeasurements?x=1") == "sitesMeasurements"
    assert raw_label("https://h/a/b/") == "b"


def test_raw_capture_records_json_responses():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/api/meas", [{"v": 1}]))
    recs = bs.raw_records()
    assert len(recs) == 1
    assert recs[0]["url"] == "https://x/api/meas"
    assert recs[0]["body"] == [{"v": 1}]


def test_raw_capture_skips_static_assets():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/app.js", {"x": 1}))
    assert bs.raw_records() == []


def test_raw_capture_ignores_unparseable_bodies():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/api/meas", None, raises=True))
    assert bs.raw_records() == []


def test_raw_records_empty_before_start():
    bs = _session_with_fake_page()
    assert bs.raw_records() == []


def test_get_json_records_when_capturing():
    bs = _session_with_fake_page()
    bs.context = _types.SimpleNamespace(request=FakeReq({"ok": 1}))
    bs.start_raw_capture()
    body = bs.get_json("https://x/api/y")
    assert body == {"ok": 1}
    recs = bs.raw_records()
    assert recs[-1]["url"] == "https://x/api/y"
    assert recs[-1]["method"] == "GET"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_browser.py -v -k raw`
Expected: FAIL — `ImportError: cannot import name 'raw_label'` / `AttributeError: 'BrowserSession' object has no attribute 'start_raw_capture'`.

- [ ] **Step 3: Implement raw capture**

In `solaranalysis/adapters/_browser.py`, add `import re` at the top (after `import os`), then module-level:

```python
# Skip static assets when recording raw payloads (mirrors tools/discover.py).
_RAW_SKIP = re.compile(
    r"\.(js|css|png|jpg|jpeg|gif|svg|woff2?|ttf|ico|map)(\?|$)", re.I)


def raw_label(url: str) -> str:
    """Short tag for a captured endpoint: the last non-empty path segment."""
    path = url.split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1] or path
```

In `BrowserSession.__init__`, add these two lines at the end:

```python
        self._recording = False
        self._raw: list = []
```

Add these methods to `BrowserSession`:

```python
    def start_raw_capture(self) -> None:
        """Begin recording every JSON response (and get/post_json result)."""
        self._recording = True
        self._raw = []

        def on_raw(resp):
            url = getattr(resp, "url", "") or ""
            if _RAW_SKIP.search(url):
                return
            try:
                body = resp.json()
            except Exception:
                return
            req = getattr(resp, "request", None)
            self._raw.append({"url": url,
                              "method": getattr(req, "method", "GET"),
                              "status": getattr(resp, "status", None),
                              "body": body})

        self.page.on("response", on_raw)

    def raw_records(self) -> list:
        return self._raw
```

Update `get_json` and `post_json` to record when capturing:

```python
    def get_json(self, url: str):
        """Authenticated GET within the browser session (shares cookies)."""
        r = self.context.request.get(url)
        body = r.json() if r.ok else None
        if self._recording and body is not None:
            self._raw.append({"url": url, "method": "GET",
                              "status": getattr(r, "status", None), "body": body})
        return body

    def post_json(self, url: str, **kwargs):
        """Authenticated POST within the browser session (shares cookies)."""
        r = self.context.request.post(url, **kwargs)
        body = r.json() if r.ok else None
        if self._recording and body is not None:
            self._raw.append({"url": url, "method": "POST",
                              "status": getattr(r, "status", None), "body": body})
        return body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_browser.py -v`
Expected: PASS (existing + new raw tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/adapters/_browser.py tests/test_browser.py
git commit -m "feat: raw-payload capture on BrowserSession"
```

---

### Task 4: Base-adapter `record_raw` + `_begin_raw`/`_finish_raw`

**Files:**
- Modify: `solaranalysis/adapters/base.py`
- Test: `tests/test_adapter_base.py` (add tests)

**Interfaces:**
- Consumes: `BrowserSession.start_raw_capture()`/`raw_records()` (Task 3); `RawPayload`/`raw_label`.
- Produces: `SolarPortalAdapter.record_raw: bool = False` (class attr, settable per instance); `adapter._begin_raw(bs)` (starts capture iff `record_raw`); `adapter._finish_raw(bs, results)` (attaches `RawPayload`s to `results[0]` iff `record_raw` and results non-empty).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adapter_base.py`:

```python
from solaranalysis.adapters.base import SolarPortalAdapter
from solaranalysis.core.schema import PlantData, TimeRange


class _StubAdapter(SolarPortalAdapter):
    platform = "stub"
    def login(self): ...
    def verify_login(self): ...
    def fetch(self, time_range): return []


class _FakeBS:
    def __init__(self, recs):
        self._recs, self.started = recs, False
    def start_raw_capture(self):
        self.started = True
    def raw_records(self):
        return self._recs


def test_begin_raw_starts_only_when_enabled():
    a = _StubAdapter(None, None)
    a.record_raw = True
    bs = _FakeBS([])
    a._begin_raw(bs)
    assert bs.started is True

    b = _StubAdapter(None, None)  # record_raw defaults False
    bs2 = _FakeBS([])
    b._begin_raw(bs2)
    assert bs2.started is False


def test_finish_raw_attaches_to_first_result_when_enabled():
    a = _StubAdapter(None, None)
    a.record_raw = True
    bs = _FakeBS([{"url": "https://h/s/meas", "method": "GET",
                   "status": 200, "body": {"a": 1}}])
    r0 = PlantData("uid0", "stub", "0", "S0")
    r1 = PlantData("uid1", "stub", "1", "S1")
    a._finish_raw(bs, [r0, r1])
    assert len(r0.raw_payloads) == 1
    assert r0.raw_payloads[0].endpoint_label == "meas"
    assert r0.raw_payloads[0].body == {"a": 1}
    assert r1.raw_payloads == []  # attached to the first only


def test_finish_raw_noop_when_disabled_or_empty():
    a = _StubAdapter(None, None)  # disabled
    r0 = PlantData("uid0", "stub", "0", "S0")
    a._finish_raw(_FakeBS([{"url": "https://h/x", "body": {}}]), [r0])
    assert r0.raw_payloads == []

    a.record_raw = True
    a._finish_raw(_FakeBS([{"url": "https://h/x", "body": {}}]), [])  # empty results
    # nothing to assert beyond "does not raise"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_adapter_base.py -v -k "raw"`
Expected: FAIL — `AttributeError: '_StubAdapter' object has no attribute '_begin_raw'`.

- [ ] **Step 3: Implement the helpers**

In `solaranalysis/adapters/base.py`, add the class attribute and two methods to `SolarPortalAdapter` (after `_save_session`):

```python
    # Set True by the pipeline/runner to persist untouched portal payloads.
    record_raw: bool = False

    def _begin_raw(self, bs) -> None:
        if self.record_raw:
            bs.start_raw_capture()

    def _finish_raw(self, bs, results) -> None:
        """Attach the session's recorded raw payloads to the first PlantData."""
        if not self.record_raw or not results:
            return
        from ..core.schema import RawPayload
        from ._browser import raw_label
        results[0].raw_payloads = [
            RawPayload(endpoint_label=raw_label(r.get("url", "")),
                       url=r.get("url", ""), method=r.get("method", "GET"),
                       status=r.get("status"), body=r.get("body"))
            for r in bs.raw_records()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_adapter_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/adapters/base.py tests/test_adapter_base.py
git commit -m "feat: adapter record_raw flag + begin/finish raw helpers"
```

---

### Task 5: Wire raw capture through pipeline, adapters, and runner

**Files:**
- Modify: `solaranalysis/pipeline.py`, `solaranalysis/adapters/solaredge.py`, `solaranalysis/adapters/growatt.py`, `solaranalysis/adapters/sma.py`, `solaranalysis/web/runner.py`
- Test: `tests/test_pipeline_record_raw.py`

**Interfaces:**
- Consumes: `adapter.record_raw`, `_begin_raw`, `_finish_raw` (Task 4).
- Produces: `run_pipeline(..., record_raw: bool = False)` sets `adapter.record_raw` on every adapter it builds.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_record_raw.py`:

```python
from solaranalysis.config import AppConfig, PlantConfig, AuthConfig
from solaranalysis.core.schema import PlantData
from solaranalysis.pipeline import run_pipeline


def _cfg():
    return AppConfig(plants=[PlantConfig(
        name="p", auth=AuthConfig("stub", username="u", password="x"))])


def test_run_pipeline_propagates_record_raw():
    seen = {}

    class FakeAdapter:
        record_raw = False
        def login(self): ...
        def fetch(self, time_range):
            seen["record_raw"] = self.record_raw
            return [PlantData("uid", "stub", "1", "S")]

    def factory(auth, ss):
        return FakeAdapter()

    from solaranalysis.core.schema import TimeRange
    run_pipeline(_cfg(), TimeRange.SNAPSHOT, session_store=None,
                 adapter_factory=factory, analyzer=lambda *a, **k: "ok",
                 record_raw=True)
    assert seen["record_raw"] is True


def test_run_pipeline_defaults_record_raw_false():
    seen = {}

    class FakeAdapter:
        record_raw = False
        def login(self): ...
        def fetch(self, time_range):
            seen["record_raw"] = self.record_raw
            return [PlantData("uid", "stub", "1", "S")]

    from solaranalysis.core.schema import TimeRange
    run_pipeline(_cfg(), TimeRange.SNAPSHOT, session_store=None,
                 adapter_factory=lambda a, s: FakeAdapter(),
                 analyzer=lambda *a, **k: "ok")
    assert seen["record_raw"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_record_raw.py -v`
Expected: FAIL — `TypeError: run_pipeline() got an unexpected keyword argument 'record_raw'`.

- [ ] **Step 3: Add `record_raw` to `run_pipeline`**

In `solaranalysis/pipeline.py`, change the signature:

```python
def run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store,
                 adapter_factory=get_adapter, analyzer=run_analysis,
                 progress=None, on_fetched=None, record_raw=False) -> dict:
```

Inside the plant loop, right after the adapter is built, set the flag:

```python
            adapter = adapter_factory(pc.auth, session_store)
            adapter.record_raw = record_raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_record_raw.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the three adapters**

In `solaranalysis/adapters/solaredge.py`, in `fetch`, immediately after `with _browser.BrowserSession(storage_state=state) as bs:` insert `self._begin_raw(bs)` (before `store = bs.capture([_SEARCH, _MEAS])`), and immediately before the final `return results` insert `self._finish_raw(bs, results)`.

In `solaranalysis/adapters/growatt.py`, in `_fetch_web`, after `with _browser.BrowserSession(storage_state=state) as bs:` insert `self._begin_raw(bs)` (before `store = bs.capture(["getPlantListTitle"])`), and before `return results` insert `self._finish_raw(bs, results)`.

In `solaranalysis/adapters/sma.py`, in `fetch`, after `with _browser.BrowserSession(storage_state=state) as bs:` insert `self._begin_raw(bs)` (before `self._authenticate(bs, had_state=bool(state))`), and before `return results` insert `self._finish_raw(bs, results)`.

- [ ] **Step 6: Turn on raw capture in the web runner**

In `solaranalysis/web/runner.py`, change the `run_pipeline` call in `run_analysis_job`:

```python
        res = run_pipeline(cfg, time_range, ss, progress=progress,
                           on_fetched=persist, record_raw=True)
```

- [ ] **Step 7: Run the adapter + pipeline suites to confirm no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_record_raw.py tests/test_adapter_base.py tests/test_browser.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add solaranalysis/pipeline.py solaranalysis/adapters/solaredge.py solaranalysis/adapters/growatt.py solaranalysis/adapters/sma.py solaranalysis/web/runner.py tests/test_pipeline_record_raw.py
git commit -m "feat: enable raw capture on web runs via record_raw"
```

---

### Task 6: Persist raw payloads in `save_measurements`

**Files:**
- Modify: `solaranalysis/core/measurements.py`
- Test: `tests/web/test_measurements_raw.py`

**Interfaces:**
- Consumes: `raw_payloads` table (Task 1); `PlantData.raw_payloads` of `RawPayload` (Task 2).
- Produces: one `raw_payloads` row per `RawPayload`, `body` stored as `zlib.compress(json)`; non-serializable bodies are skipped, not fatal.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_measurements_raw.py`:

```python
import json
import zlib

from solaranalysis.web import db
from solaranalysis.core import measurements
from solaranalysis.core.schema import PlantData, RawPayload, TimeRange


def _plant_with_raw(records):
    pd = PlantData("solaredge-2387929", "solaredge", "2387929", "Baram")
    pd.config_plant_id = 7
    pd.fetched_at_utc = "2026-07-23T03:00:00+00:00"
    pd.raw_payloads = records
    return pd


def test_save_measurements_persists_raw_payloads():
    conn = db.connect(":memory:")
    db.init_db(conn)
    pd = _plant_with_raw([
        RawPayload("sitesMeasurements", "https://h/s/sitesMeasurements",
                   "POST", 200, [{"energyToday": 5.0}]),
    ])
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=42)
    conn.commit()
    row = conn.execute(
        "SELECT run_id, plant_uid, platform, endpoint_label, method, status, payload_zjson"
        " FROM raw_payloads").fetchone()
    assert row["run_id"] == 42
    assert row["plant_uid"] == "solaredge-2387929"
    assert row["platform"] == "solaredge"
    assert row["endpoint_label"] == "sitesMeasurements"
    assert row["method"] == "POST"
    body = json.loads(zlib.decompress(row["payload_zjson"]).decode("utf-8"))
    assert body == [{"energyToday": 5.0}]


def test_save_measurements_skips_unserializable_body_non_fatal():
    conn = db.connect(":memory:")
    db.init_db(conn)
    pd = _plant_with_raw([
        RawPayload("bad", "https://h/s/bad", "GET", 200, {1, 2, 3}),  # set: not JSON
        RawPayload("good", "https://h/s/good", "GET", 200, {"ok": 1}),
    ])
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=1)
    conn.commit()
    labels = [r["endpoint_label"] for r in conn.execute(
        "SELECT endpoint_label FROM raw_payloads")]
    assert labels == ["good"]  # the unserializable one was skipped, not fatal
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_measurements_raw.py -v`
Expected: FAIL — no rows in `raw_payloads` (nothing persists yet).

- [ ] **Step 3: Implement raw persistence**

In `solaranalysis/core/measurements.py`, add `import zlib` at the top (with the other stdlib imports). In `save_measurements`, after the existing lines that build `kpis` and pop the timeseries:

```python
        kpis = pd.to_dict()
        kpis.pop("energy_timeseries", None)
        kpis.pop("power_timeseries", None)
        kpis.pop("raw_payloads", None)  # never store raw blobs inside the KPI JSON
```

(`to_dict` already excludes `raw_payloads`; the pop is defensive and harmless.)

Then, inside the `for pd in plants:` loop, after the `power_points` loop, add:

```python
        for r in pd.raw_payloads:
            try:
                blob = zlib.compress(
                    json.dumps(r.body, ensure_ascii=False).encode("utf-8"))
            except (TypeError, ValueError):
                continue  # a non-JSON-serializable body is skipped, never fatal
            conn.execute(
                "INSERT INTO raw_payloads"
                "(run_id, config_plant_id, plant_uid, platform, endpoint_label,"
                " url, method, status, fetched_at_utc, payload_zjson) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, pd.config_plant_id, pd.plant_id, pd.source_platform,
                 r.endpoint_label, r.url, r.method, r.status,
                 pd.fetched_at_utc or now, blob))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/web/test_measurements_raw.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/core/measurements.py tests/web/test_measurements_raw.py
git commit -m "feat: persist raw portal payloads (zlib) in save_measurements"
```

---

### Task 7: SolarEdge `energyYesterday` → daily energy point

**Files:**
- Modify: `solaranalysis/adapters/solaredge.py`
- Test: `tests/test_solaredge_daily.py`

**Interfaces:**
- Consumes: `sitesMeasurements` field `energyYesterday` (kWh).
- Produces: `map_solaredge_fleet(site, meas, env, live, today=None)` appends `EnergyPoint(<yesterday YYYY-MM-DD>, energyYesterday, "day")` to `pd.energy_timeseries` when present; nothing when absent. Existing 4-arg calls keep working (`today` defaults to `date.today()`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_solaredge_daily.py`:

```python
from datetime import date

from solaranalysis.adapters.solaredge import map_solaredge_fleet


def test_energy_yesterday_becomes_daily_point():
    site = {"solarFieldId": 2387929, "name": "Baram", "status": "ACTIVE"}
    meas = {"energyToday": 100.0, "energyYesterday": 1314.5,
            "energyMonthly": 5000.0, "energyLifeTime": 900000.0}
    pd = map_solaredge_fleet(site, meas, None, None, today=date(2026, 7, 23))
    days = [p for p in pd.energy_timeseries if p.granularity == "day"]
    assert len(days) == 1
    assert days[0].timestamp_local == "2026-07-22"
    assert days[0].energy_kwh == 1314.5


def test_no_daily_point_when_energy_yesterday_absent():
    site = {"solarFieldId": 1, "name": "S", "status": "ACTIVE"}
    pd = map_solaredge_fleet(site, {"energyToday": 10.0}, None, None,
                             today=date(2026, 7, 23))
    assert [p for p in pd.energy_timeseries if p.granularity == "day"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_solaredge_daily.py -v`
Expected: FAIL — `map_solaredge_fleet()` takes 4 positional args (no `today`), and emits no day point.

- [ ] **Step 3: Implement the yesterday point**

In `solaranalysis/adapters/solaredge.py`, change the signature of `map_solaredge_fleet`:

```python
def map_solaredge_fleet(site: dict, meas: dict, env: dict | None,
                        live: dict | None, today: "date | None" = None) -> PlantData:
```

At the end of `map_solaredge_fleet`, right before `return pd`, add:

```python
    # A clean, complete previous-day energy point so a daily series accumulates
    # even on snapshot runs (energy_timeseries is otherwise only filled for
    # ranged runs). energyYesterday is already kWh, like the other counters.
    ey = _num(meas.get("energyYesterday"))
    if ey is not None:
        yday = ((today or date.today()) - timedelta(days=1)).isoformat()
        pd.energy_timeseries.append(EnergyPoint(yday, ey, "day"))
```

(`date`, `timedelta`, and `EnergyPoint` are already imported at the top of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_solaredge_daily.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests plus the new ones; count = previous 348 + new).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/adapters/solaredge.py tests/test_solaredge_daily.py
git commit -m "feat: SolarEdge energyYesterday persisted as a daily energy point"
```

---

### Task 8 (ops, no code): enable the daily schedule

Not a code change — do this on the deployed server after the code ships and a manual snapshot run is verified to persist raw rows + a yesterday energy point.

- [ ] In the web UI (Settings/Schedules), create one schedule: time `06:00`, all days of week, time range `snapshot`, enabled. The existing scheduler turns it into a daily cron job that calls `start_run("scheduled", "snapshot")`.
- [ ] The next morning, confirm a new run appears in history and that `raw_payloads` has fresh rows and `energy_points` has a new `day` row per SolarEdge site (query `app.db`).

---

## Self-Review

**Spec coverage** (`specs/2026-07-23-daily-raw-persistence-design.md`):
- A1 daily schedule → Task 8 (ops).
- A2 raw capture: table (Task 1), `RawPayload`/field/`to_dict` (Task 2), `BrowserSession` capture (Task 3), base helpers (Task 4), wiring + runner enable (Task 5), persistence (Task 6). ✓
- A3 clean daily energy point → Task 7. ✓
- Migration additive + `SCHEMA_VERSION` bump → Task 1. ✓
- CLI unchanged (`record_raw` default False) → Tasks 4/5 defaults. ✓
- Testing items (raw round-trip, non-fatal skip, `energyYesterday` mapping, migration) → Tasks 1,6,7. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every test step shows the test and expected fail/pass.

**Type consistency:** `RawPayload(endpoint_label, url, method, status, body)` is defined in Task 2 and constructed identically in Tasks 4 and 6. `raw_records()` returns `list[dict]` with keys `url/method/status/body`, consumed with `.get(...)` in `_finish_raw` (Task 4) and produced in Task 3. `record_raw` defined in Task 4, set in Task 5. `run_pipeline(..., record_raw=False)` matches the runner call in Task 5.
