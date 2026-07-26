# Growatt Per-String Collector (Phase C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect every 5-minute sample from the Growatt MAX inverter into `app.db` — per-MPPT-input daily energy, per-channel intraday series, and inverter health/fault samples — with a backfill, so Phase C2 can calibrate anomaly thresholds against real stored data.

**Architecture:** A new `solaranalysis/strings/` package mirroring the existing `solaranalysis/optimizers/` split: a thin IO shell (`history_client`) that only does authenticated POSTs, pure fixture-testable mappers, a store of upserts against four new v7 tables, a collector with per-day failure isolation, and a CLI that reuses `GrowattAdapter` for login. Nothing in `adapters/growatt.py` changes.

**Tech Stack:** Python 3.10, sqlite3, Playwright (via the existing `_browser.BrowserSession`), pytest.

**Spec:** `specs/2026-07-26-growatt-string-collector-design.md` — read §2, §3 and §5 before starting. Phase C2 (analyze, report, email) is a separate plan; do not build it here.

## Global Constraints

- **Python invocation is `python`, never `python3`** (`python3` is a broken Windows Store alias on this machine). Interpreter: `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`; the project venv is `.venv`.
- Run tests as `.venv\Scripts\python.exe -m pytest` from the repo root.
- **The Growatt portal requires form-encoded POST bodies.** Query-string params return `null`. Always pass `form={...}` to `bs.post_json`.
- **`start` is a 0-based page index**, not a row offset. 80 rows per page, 288 rows per day, newest-first.
- **Parse the `time` field (`"YYYY-MM-DD HH:MM:SS"`), never the `calendar` object** — `calendar.month` is 0-based (July arrives as `6`).
- Timestamps in `time` are **plant-local**; day keys are plant-local. UTC is only used for `*_utc` audit columns, via `_now.now_utc()`.
- Units are already correct and need no conversion: energy kWh, power W, voltage V, current A, temperature °C, `pvIso` kΩ, `gfci` mA.
- Numeric fields arrive **inconsistently typed** — sometimes JSON numbers, sometimes strings (`"0"`). Every numeric read goes through a coercion helper that accepts both. Fault/warn codes are stored as TEXT to preserve the raw token.
- Commit after every task. No AI attribution in commit messages (see the `clean-commits` skill).
- New DDL is additive only (`CREATE TABLE IF NOT EXISTS`), matching the migration policy comment at the top of `solaranalysis/web/db.py`.

---

### Task 1: Schema v7 — four new tables

**Files:**
- Modify: `solaranalysis/web/db.py` (bump `SCHEMA_VERSION` at line 9; append DDL to `_DDL`, which currently ends at line 160 with `ix_optenergy_day`)
- Test: `tests/web/test_db_strings.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: tables `inverter_channels`, `channel_day_energy`, `channel_samples`, `inverter_samples`; `db.SCHEMA_VERSION == 7`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_db_strings.py`:

```python
from solaranalysis.web import db


def test_init_db_creates_string_tables_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"inverter_channels", "channel_day_energy",
            "channel_samples", "inverter_samples"} <= tables

    chan = {r["name"] for r in conn.execute("PRAGMA table_info(inverter_channels)")}
    assert {"device_sn", "channel_kind", "channel_no", "parent_channel_no",
            "group_voltage", "plant_uid", "lifetime_kwh",
            "first_seen_utc", "last_seen_utc"} <= chan

    energy = {r["name"] for r in conn.execute("PRAGMA table_info(channel_day_energy)")}
    assert {"device_sn", "channel_no", "day", "energy_kwh", "share_of_total",
            "peak_w", "peak_at", "producing_minutes", "updated_at_utc"} <= energy

    samples = {r["name"] for r in conn.execute("PRAGMA table_info(channel_samples)")}
    assert {"device_sn", "sampled_at", "day", "channel_kind", "channel_no",
            "power_w", "voltage_v", "current_a"} <= samples

    inv = {r["name"] for r in conn.execute("PRAGMA table_info(inverter_samples)")}
    assert {"device_sn", "sampled_at", "day", "pac_w", "e_ac_today_kwh",
            "temp_c", "temp2_c", "temp3_c", "temp4_c", "temp5_c",
            "pv_iso_kohm", "gfci_ma", "status", "derating_mode",
            "str_break", "str_unbalance", "str_unmatch",
            "warn_code", "fault_code1", "fault_code2", "fault_type"} <= inv

    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == "7"


def test_channel_kind_is_constrained():
    import sqlite3
    import pytest
    conn = db.connect(":memory:")
    db.init_db(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO inverter_channels(device_sn, channel_kind, channel_no,"
            " first_seen_utc, last_seen_utc) VALUES('SN','bogus',1,'t','t')")


def test_init_db_idempotent_with_string_tables():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.init_db(conn)  # must not raise
    assert conn.execute("SELECT COUNT(*) FROM channel_samples").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_db_strings.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: inverter_channels`.

- [ ] **Step 3: Write minimal implementation**

In `solaranalysis/web/db.py`, change line 9 from `SCHEMA_VERSION = 6` to:

```python
SCHEMA_VERSION = 7
```

Then append this to the `_DDL` string, immediately after the existing
`CREATE INDEX IF NOT EXISTS ix_optenergy_day ...;` line and before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS inverter_channels(
  device_sn TEXT NOT NULL,
  channel_kind TEXT NOT NULL CHECK (channel_kind IN ('mppt','string')),
  channel_no INTEGER NOT NULL,
  parent_channel_no INTEGER,        -- for 'string': its MPPT input; unverifiable
                                    -- on this hardware, so left NULL in C1
  group_voltage REAL,               -- vpvN for 'mppt', vStringN for 'string';
                                    -- exactly-equal values group parallel strings
  plant_uid TEXT,                   -- e.g. 'growatt-10950561'
  lifetime_kwh REAL,                -- epvNTotal; 0 => never produced => excluded
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_kind, channel_no)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS channel_day_energy(
  device_sn TEXT NOT NULL,
  channel_no INTEGER NOT NULL,      -- MPPT inputs only (the only tier with energy)
  day TEXT NOT NULL,                -- 'YYYY-MM-DD', plant-local
  energy_kwh REAL,
  share_of_total REAL,              -- of the plant's PV day total; weather-immune
  peak_w REAL,
  peak_at TEXT,
  producing_minutes INTEGER,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_no, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chanenergy_day ON channel_day_energy(device_sn, day);
CREATE TABLE IF NOT EXISTS channel_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,         -- 'YYYY-MM-DD HH:MM:SS', plant-local
  day TEXT NOT NULL,
  channel_kind TEXT NOT NULL CHECK (channel_kind IN ('mppt','string')),
  channel_no INTEGER NOT NULL,
  power_w REAL,                     -- NULL for 'string' (not reported)
  voltage_v REAL,
  current_a REAL,
  PRIMARY KEY (device_sn, sampled_at, channel_kind, channel_no)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chansamples_day ON channel_samples(device_sn, day);
CREATE TABLE IF NOT EXISTS inverter_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,
  day TEXT NOT NULL,
  pac_w REAL,
  e_ac_today_kwh REAL,
  temp_c REAL, temp2_c REAL, temp3_c REAL, temp4_c REAL, temp5_c REAL,
  pv_iso_kohm REAL,
  gfci_ma REAL,
  status TEXT,
  derating_mode TEXT,
  str_break TEXT, str_unbalance TEXT, str_unmatch TEXT,
  warn_code TEXT, fault_code1 TEXT, fault_code2 TEXT, fault_type TEXT,
  PRIMARY KEY (device_sn, sampled_at)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_invsamples_day ON inverter_samples(device_sn, day);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_db_strings.py tests/web/test_db_schema.py tests/web/test_db_optimizers.py -v`
Expected: PASS. Note `tests/web/test_db_schema.py` asserts `str(db.SCHEMA_VERSION)`, so it follows the bump automatically; `test_db_optimizers.py` hardcodes `"6"` and **will fail** — fix it in the next step.

- [ ] **Step 5: Fix the version assertion that hardcodes 6**

In `tests/web/test_db_optimizers.py`, replace the last two lines:

```python
    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == str(db.SCHEMA_VERSION)
```

(The previous `assert ver == "6"` pinned a version this task moves past; the
optimizer tables it really tests are unaffected. This mirrors commit `6a12a36`,
which made the same assertion version-agnostic for `raw_payloads`.)

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, count up from 417.

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/web/db.py tests/web/test_db_strings.py tests/web/test_db_optimizers.py
git commit -m "feat: per-string channel, energy and sample tables (schema v7)"
```

---

### Task 2: Package skeleton and `history_client`

**Files:**
- Create: `solaranalysis/strings/__init__.py` (empty), `solaranalysis/strings/_now.py`, `solaranalysis/strings/history_client.py`
- Test: `tests/strings/__init__.py` (empty), `tests/strings/test_history_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `strings._now.now_utc() -> str`
  - `strings.history_client.BASE == "https://server.growatt.com"`
  - `strings.history_client.PAGE_SIZE == 80`
  - `history_client.get_plant_list(bs) -> list`
  - `history_client.get_device_list(bs, plant_id) -> dict`
  - `history_client.get_history_page(bs, sn, day, page, datalog_sn="", sleep=time.sleep) -> dict`

- [ ] **Step 1: Write the failing test**

Create empty `tests/strings/__init__.py`, then create `tests/strings/test_history_client.py`:

```python
import pytest

from solaranalysis.strings import history_client as hc


class FakeBS:
    """Records form-encoded POSTs; returns a canned envelope."""
    def __init__(self, fail_times=0):
        self.posts = []
        self.fail_times = fail_times

    def post_json(self, url, **kw):
        self.posts.append((url, kw.get("form")))
        if len(self.posts) <= self.fail_times:
            raise RuntimeError("net::ERR_TIMED_OUT")
        return {"result": 1, "obj": {"datas": []}}


def test_get_plant_list_posts_to_the_index_endpoint():
    class ListBS(FakeBS):
        def post_json(self, url, **kw):
            self.posts.append((url, kw.get("form")))
            return [{"id": "10950561", "plantName": "Elcam Baram"}]

    bs = ListBS()
    out = hc.get_plant_list(bs)
    assert bs.posts[0][0] == "https://server.growatt.com/index/getPlantListTitle"
    assert out == [{"id": "10950561", "plantName": "Elcam Baram"}]


def test_get_plant_list_returns_empty_list_for_null_body():
    class NullBS:
        def post_json(self, url, **kw):
            return None
    assert hc.get_plant_list(NullBS()) == []


def test_get_device_list_posts_form_encoded():
    bs = FakeBS()
    hc.get_device_list(bs, "10950561")
    url, form = bs.posts[0]
    assert url == "https://server.growatt.com/panel/getDevicesByPlantList"
    assert form == {"plantId": "10950561", "currPage": "1"}


def test_get_history_page_sends_page_index_and_datalog():
    bs = FakeBS()
    hc.get_history_page(bs, "MZHRF6K002", "2026-07-25", 2, datalog_sn="XGD6CH21BK")
    url, form = bs.posts[0]
    assert url == "https://server.growatt.com/device/getMAXHistory"
    # start is a 0-based PAGE index, and start/end date bracket a single day
    assert form == {"maxSn": "MZHRF6K002", "startDate": "2026-07-25",
                    "endDate": "2026-07-25", "start": "2",
                    "allDatalogSns": "XGD6CH21BK"}


def test_get_history_page_retries_a_stalled_request():
    bs = FakeBS(fail_times=2)
    slept = []
    out = hc.get_history_page(bs, "SN", "2026-07-25", 0, sleep=slept.append)
    assert out == {"result": 1, "obj": {"datas": []}}
    assert len(bs.posts) == 3          # two failures then a success
    assert slept == [hc.RETRY_DELAY_S, hc.RETRY_DELAY_S]


def test_get_history_page_propagates_after_exhausting_retries():
    bs = FakeBS(fail_times=99)
    with pytest.raises(RuntimeError):
        hc.get_history_page(bs, "SN", "2026-07-25", 0, sleep=lambda s: None)
    assert len(bs.posts) == hc.RETRY_ATTEMPTS


def test_get_history_page_returns_empty_dict_for_null_body():
    class NullBS:
        def post_json(self, url, **kw):
            return None
    assert hc.get_history_page(NullBS(), "SN", "2026-07-25", 0) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_history_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.strings'`.

- [ ] **Step 3: Write minimal implementation**

Create empty `solaranalysis/strings/__init__.py`.

Create `solaranalysis/strings/_now.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

Create `solaranalysis/strings/history_client.py`:

```python
"""Thin authenticated POST wrappers for the Growatt device-history API.

All calls run inside an already-authenticated BrowserSession (cookies shared
via context.request). No parsing here — see mappers.py.

This portal requires FORM-encoded bodies; query strings return null (the same
constraint documented in adapters/growatt.py). It also intermittently stalls
the request itself (Chromium raises net::ERR_TIMED_OUT), a partial-outage
pattern that recovers within seconds — hence the bounded retry.
"""
from __future__ import annotations
import time

BASE = "https://server.growatt.com"
PAGE_SIZE = 80          # rows per page of getMAXHistory
RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 2.0


def get_plant_list(bs) -> list:
    """Every plant on the account: [{id, plantName, timezone}, ...]."""
    return bs.post_json(f"{BASE}/index/getPlantListTitle") or []


def get_device_list(bs, plant_id) -> dict:
    return bs.post_json(f"{BASE}/panel/getDevicesByPlantList",
                        form={"plantId": str(plant_id), "currPage": "1"}) or {}


def get_history_page(bs, sn: str, day: str, page: int,
                     datalog_sn: str = "", sleep=time.sleep) -> dict:
    """One page of 5-minute samples for a single plant-local day.

    `page` is a 0-based PAGE index (the portal's `start` param), not a row
    offset. start-date == end-date restricts the query to that one day, which
    makes page 0 row 0 the day's last sample.
    """
    form = {"maxSn": sn, "startDate": day, "endDate": day,
            "start": str(page), "allDatalogSns": datalog_sn}
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return bs.post_json(f"{BASE}/device/getMAXHistory", form=form) or {}
        except Exception as e:
            last = e
            if attempt < RETRY_ATTEMPTS - 1:
                sleep(RETRY_DELAY_S)
    raise last
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_history_client.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings tests/strings
git commit -m "feat: Growatt device-history client with bounded retry"
```

---

### Task 3: Test fixture and `parse_devices`

**Files:**
- Create: `tests/fixtures/growatt_max_history.json`
- Create: `solaranalysis/strings/mappers.py`
- Test: `tests/strings/test_mappers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `mappers.InverterInfo` dataclass: `serial: str`, `model: str | None`, `datalog_sn: str | None`, `nominal_power_w: float | None`, `plant_id: str | None`
  - `mappers.parse_devices(payload: dict) -> list[InverterInfo]`
  - `mappers.sample_day(row: dict) -> str | None`
  - `mappers.MPPT_MAX == 16`, `mappers.STRING_MAX == 32`, `mappers.SAMPLE_MINUTES == 5`
  - private helpers `mappers._num(x) -> float | None`, `mappers._text(x) -> str | None`

The fixture is reused by Tasks 4–7 and by Task 9's collector tests.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/growatt_max_history.json`. These are **real values captured
live on 2026-07-25/26** (serial anonymised). Three rows, newest-first, mirroring
the live envelope: end-of-day, solar peak, and a night sample. MPPT inputs 1–7 and
strings 1–16 only — the mappers must loop to 16/32 and tolerate absent keys.
`pvIso`/`gfci`/`status`/flag fields deliberately mix numbers and strings, because
the live payload is inconsistent about it.

```json
{
  "result": 1,
  "obj": {
    "endDate": "2026-07-25",
    "start": 1,
    "totalPageSize": 4,
    "totalRecord": 288,
    "haveNext": true,
    "datas": [
      {
        "serialNum": "SN-MAX-TEST",
        "time": "2026-07-25 23:57:09",
        "calendar": {"year": 2026, "month": 6, "dayOfMonth": 25,
                     "hourOfDay": 23, "minute": 57, "second": 9},
        "status": "0",
        "pac": 0.0, "ppv": 0.0, "eacToday": 294.6, "eacTotal": 4857.6,
        "epvTotal": 4832.0,
        "epv1Today": 57.3, "epv2Today": 60.4, "epv3Today": 55.5,
        "epv4Today": 37.3, "epv5Today": 40.9, "epv6Today": 41.2,
        "epv7Today": 0.0,
        "epv1Total": 940.8, "epv2Total": 999.3, "epv3Total": 917.2,
        "epv4Total": 616.2, "epv5Total": 678.5, "epv6Total": 680.0,
        "epv7Total": 0.0,
        "ipv1": 0.0, "ipv2": 0.0, "ipv3": 0.0, "ipv4": 0.0, "ipv5": 0.0,
        "ipv6": 0.0, "ipv7": 0.0,
        "vpv1": 0.0, "vpv2": 0.0, "vpv3": 0.0, "vpv4": 0.0, "vpv5": 0.0,
        "vpv6": 0.0, "vpv7": 0.0,
        "ppv1": 0.0, "ppv2": 0.0, "ppv3": 0.0, "ppv4": 0.0, "ppv5": 0.0,
        "ppv6": 0.0, "ppv7": 0.0,
        "currentString1": 0.0, "currentString2": 0.0, "currentString3": 0.0,
        "currentString4": 0.0, "currentString5": 0.0, "currentString6": 0.0,
        "currentString7": 0.0, "currentString8": 0.0, "currentString9": 0.0,
        "currentString10": 0.0, "currentString11": 0.0, "currentString12": 0.0,
        "currentString13": 0.0, "currentString14": 0.0, "currentString15": 0.0,
        "currentString16": 0.0,
        "vString1": 0.0, "vString2": 0.0, "vString3": 0.0, "vString4": 0.0,
        "vString5": 0.0, "vString6": 0.0, "vString7": 0.0, "vString8": 0.0,
        "vString9": 0.0, "vString10": 0.0, "vString11": 0.0, "vString12": 0.0,
        "vString13": 0.0, "vString14": 0.0, "vString15": 0.0, "vString16": 0.0,
        "temperature": 28.9, "temperature2": 30.1, "temperature3": 31.0,
        "temperature4": 0.0, "temperature5": 27.5,
        "pvIso": 288, "gfci": 0, "deratingMode": "0",
        "StrBreak": "0", "StrUnblance": "0", "StrUnmatch": "0",
        "warnCode": "0", "faultCode1": "0", "faultCode2": "0", "faultType": "0"
      },
      {
        "serialNum": "SN-MAX-TEST",
        "time": "2026-07-25 12:00:00",
        "calendar": {"year": 2026, "month": 6, "dayOfMonth": 25,
                     "hourOfDay": 12, "minute": 0, "second": 0},
        "status": 1,
        "pac": 40460.9, "ppv": 40905.8, "eacToday": 135.2, "eacTotal": 4698.2,
        "epvTotal": 4832.0,
        "epv1Today": 25.9, "epv2Today": 28.0, "epv3Today": 25.7,
        "epv4Today": 17.1, "epv5Today": 19.1, "epv6Today": 19.1,
        "epv7Today": 0.0,
        "epv1Total": 940.8, "epv2Total": 999.3, "epv3Total": 917.2,
        "epv4Total": 616.2, "epv5Total": 678.5, "epv6Total": 680.0,
        "epv7Total": 0.0,
        "ipv1": 19.5, "ipv2": 20.800001, "ipv3": 20.300001, "ipv4": 13.1,
        "ipv5": 13.400001, "ipv6": 14.0, "ipv7": 0.0,
        "vpv1": 409.0, "vpv2": 401.4, "vpv3": 384.7, "vpv4": 403.2,
        "vpv5": 426.7, "vpv6": 412.30002, "vpv7": 227.40001,
        "ppv1": 7975.5, "ppv2": 8349.1, "ppv3": 7809.4, "ppv4": 5281.9,
        "ppv5": 5717.7, "ppv6": 5772.2, "ppv7": 0.0,
        "currentString1": 8.900001, "currentString2": 10.6,
        "currentString3": 9.1, "currentString4": 11.6,
        "currentString5": 9.0, "currentString6": 10.5,
        "currentString7": 5.7000003, "currentString8": 7.0,
        "currentString9": 5.8, "currentString10": 7.0,
        "currentString11": 6.1, "currentString12": 7.4,
        "currentString13": 0.0, "currentString14": 0.0,
        "currentString15": 0.0, "currentString16": 0.0,
        "vString1": 408.30002, "vString2": 408.30002,
        "vString3": 402.7, "vString4": 402.7,
        "vString5": 384.80002, "vString6": 384.80002,
        "vString7": 397.4, "vString8": 397.4,
        "vString9": 429.0, "vString10": 429.0,
        "vString11": 414.1, "vString12": 414.1,
        "vString13": 228.6, "vString14": 228.6,
        "vString15": 228.6, "vString16": 228.6,
        "temperature": 43.4, "temperature2": 50.9, "temperature3": 52.3,
        "temperature4": 0.0, "temperature5": 42.0,
        "pvIso": "250", "gfci": "3", "deratingMode": "0",
        "StrBreak": 0, "StrUnblance": 0, "StrUnmatch": 0,
        "warnCode": "0", "faultCode1": "0", "faultCode2": "0", "faultType": "0"
      },
      {
        "serialNum": "SN-MAX-TEST",
        "time": "2026-07-25 00:02:56",
        "calendar": {"year": 2026, "month": 6, "dayOfMonth": 25,
                     "hourOfDay": 0, "minute": 2, "second": 56},
        "status": "0",
        "pac": 0.0, "ppv": 0.0, "eacToday": 0.0, "eacTotal": 4563.0,
        "epvTotal": 4832.0,
        "epv1Today": 0.0, "epv2Today": 0.0, "epv3Today": 0.0,
        "epv4Today": 0.0, "epv5Today": 0.0, "epv6Today": 0.0,
        "epv7Today": 0.0,
        "epv1Total": 940.8, "epv2Total": 999.3, "epv3Total": 917.2,
        "epv4Total": 616.2, "epv5Total": 678.5, "epv6Total": 680.0,
        "epv7Total": 0.0,
        "ipv1": 0.0, "ipv2": 0.0, "ipv3": 0.0, "ipv4": 0.0, "ipv5": 0.0,
        "ipv6": 0.0, "ipv7": 0.0,
        "vpv1": 0.0, "vpv2": 0.0, "vpv3": 0.0, "vpv4": 0.0, "vpv5": 0.0,
        "vpv6": 0.0, "vpv7": 0.0,
        "ppv1": 0.0, "ppv2": 0.0, "ppv3": 0.0, "ppv4": 0.0, "ppv5": 0.0,
        "ppv6": 0.0, "ppv7": 0.0,
        "currentString1": 0.0, "currentString2": 0.0, "currentString3": 0.0,
        "currentString4": 0.0, "currentString5": 0.0, "currentString6": 0.0,
        "currentString7": 0.0, "currentString8": 0.0, "currentString9": 0.0,
        "currentString10": 0.0, "currentString11": 0.0, "currentString12": 0.0,
        "currentString13": 0.0, "currentString14": 0.0, "currentString15": 0.0,
        "currentString16": 0.0,
        "vString1": 0.0, "vString2": 0.0, "vString3": 0.0, "vString4": 0.0,
        "vString5": 0.0, "vString6": 0.0, "vString7": 0.0, "vString8": 0.0,
        "vString9": 0.0, "vString10": 0.0, "vString11": 0.0, "vString12": 0.0,
        "vString13": 0.0, "vString14": 0.0, "vString15": 0.0, "vString16": 0.0,
        "temperature": 25.0, "temperature2": 26.2, "temperature3": 26.8,
        "temperature4": 0.0, "temperature5": 24.9,
        "pvIso": 263, "gfci": 0, "deratingMode": "0",
        "StrBreak": "0", "StrUnblance": "0", "StrUnmatch": "0",
        "warnCode": "0", "faultCode1": "0", "faultCode2": "0", "faultType": "0"
      }
    ]
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/strings/test_mappers.py`:

```python
import json
from pathlib import Path

from solaranalysis.strings import mappers as m

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def history_payload() -> dict:
    return json.loads((FIXTURES / "growatt_max_history.json").read_text(encoding="utf-8"))


def history_rows() -> list[dict]:
    return history_payload()["obj"]["datas"]


def device_payload() -> dict:
    return json.loads(
        (FIXTURES / "growatt_web_devices_list.json").read_text(encoding="utf-8"))


def test_parse_devices_reads_serial_model_and_datalogger():
    devs = m.parse_devices(device_payload())
    assert len(devs) == 1
    d = devs[0]
    assert isinstance(d, m.InverterInfo)
    assert d.serial == "SN-TEST-1"
    assert d.model == "MAX 70KTL3 LV"
    assert d.datalog_sn == "DL-TEST-1"
    assert d.nominal_power_w == 70000.0     # arrives as the string "70000.0"
    assert d.plant_id == "10950561"


def test_parse_devices_skips_rows_without_a_serial():
    payload = {"obj": {"datas": [{"deviceModel": "MAX"}, {"sn": "OK"}, "junk"]}}
    assert [d.serial for d in m.parse_devices(payload)] == ["OK"]


def test_parse_devices_empty():
    assert m.parse_devices({}) == []
    assert m.parse_devices({"obj": {}}) == []


def test_sample_day_parses_time_not_the_zero_based_calendar():
    rows = history_rows()
    # calendar.month is 6 for July -- using it would yield '2026-06-25'
    assert rows[0]["calendar"]["month"] == 6
    assert m.sample_day(rows[0]) == "2026-07-25"
    assert m.sample_day({}) is None
    assert m.sample_day({"time": "nope"}) is None


def test_num_coerces_strings_and_rejects_junk():
    assert m._num(1) == 1.0
    assert m._num("70000.0") == 70000.0
    assert m._num("") is None
    assert m._num(None) is None
    assert m._num("abc") is None


def test_text_preserves_the_raw_token():
    assert m._text("0") == "0"
    assert m._text(0) == "0"
    assert m._text(None) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.strings.mappers'`.

- [ ] **Step 4: Write minimal implementation**

Create `solaranalysis/strings/mappers.py`:

```python
"""Pure mappers: Growatt getMAXHistory / getDevicesByPlantList payloads ->
plain records.

No IO, no browser, no clock — fixture-testable. The IO shell (history_client)
fetches the raw dicts; these turn them into rows the store persists.

Two granularity tiers exist in a sample row, and only the first has energy:
  * MPPT inputs 1..16  -- ipvN (A), vpvN (V), ppvN (W), epvNToday/epvNTotal (kWh)
  * individual strings 1..32 -- currentStringN (A), vStringN (V), no energy
Parallel strings on one MPPT necessarily share a voltage, so exactly-equal
vStringN values group them; see spec section 3(b).
"""
from __future__ import annotations
from dataclasses import dataclass

MPPT_MAX = 16
STRING_MAX = 32
SAMPLE_MINUTES = 5      # the portal's fixed 5-minute logging cadence


def _num(x):
    """JSON number or numeric string -> float; anything else -> None.

    The portal is inconsistent about which fields are quoted, so every numeric
    read goes through here.
    """
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _text(x):
    """Preserve a code/flag token verbatim (stored as TEXT), or None."""
    return None if x is None else str(x)


@dataclass
class InverterInfo:
    serial: str
    model: str | None = None
    datalog_sn: str | None = None
    nominal_power_w: float | None = None
    plant_id: str | None = None


def parse_devices(payload: dict) -> list[InverterInfo]:
    """getDevicesByPlantList payload -> the inverters on the plant."""
    out: list[InverterInfo] = []
    for r in ((payload or {}).get("obj") or {}).get("datas") or []:
        if not isinstance(r, dict) or not r.get("sn"):
            continue
        out.append(InverterInfo(
            serial=str(r["sn"]),
            model=r.get("deviceModel") or r.get("alias"),
            datalog_sn=r.get("datalogSn"),
            nominal_power_w=_num(r.get("nominalPower")),
            plant_id=None if r.get("plantId") is None else str(r["plantId"]),
        ))
    return out


def sample_day(row: dict) -> str | None:
    """Plant-local 'YYYY-MM-DD' from the row's `time` field.

    Deliberately ignores the sibling `calendar` object, whose `month` is
    0-based (July arrives as 6).
    """
    t = str((row or {}).get("time") or "")
    day = t[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        return None
    return day
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/growatt_max_history.json solaranalysis/strings/mappers.py tests/strings/test_mappers.py
git commit -m "feat: Growatt device-list mapper and day-key parsing"
```

---

### Task 4: `channel_inventory` — discover live MPPT inputs and strings

**Files:**
- Modify: `solaranalysis/strings/mappers.py`
- Test: `tests/strings/test_mappers.py` (append)

**Interfaces:**
- Consumes: `mappers._num`, `mappers.MPPT_MAX`, `mappers.STRING_MAX`, the fixture helpers in `tests/strings/test_mappers.py`.
- Produces:
  - `mappers.ChannelInfo` dataclass: `kind: str` (`'mppt'`/`'string'`), `no: int`, `parent_no: int | None`, `group_voltage: float | None`, `lifetime_kwh: float | None`
  - `mappers.channel_inventory(rows: list[dict]) -> list[ChannelInfo]`
  - `mappers.best_row(rows: list[dict]) -> dict` — the highest-`pac` row (used wherever a daylight reading is needed)

Activity rules, from real data (spec §3(a),(b)): an **MPPT input** is live when
`epvNTotal > 0` — 6 of 16 here, which correctly rejects PV7's stray 227 V
open-circuit reading. An **individual string** is live when its
`currentStringN` at the day's peak row is `> 0` — 12 of 32 here, which correctly
rejects strings 13–16 sitting on the never-produced MPPT7.

- [ ] **Step 1: Write the failing test**

Append to `tests/strings/test_mappers.py`:

```python
def test_channel_inventory_finds_six_live_mppt_inputs():
    chans = m.channel_inventory(history_rows())
    mppt = [c for c in chans if c.kind == "mppt"]
    assert [c.no for c in mppt] == [1, 2, 3, 4, 5, 6]
    by_no = {c.no: c for c in mppt}
    assert isinstance(by_no[1], m.ChannelInfo)
    assert by_no[1].lifetime_kwh == 940.8
    assert by_no[4].lifetime_kwh == 616.2
    # group_voltage comes from the daylight (peak) row, not the night rows
    assert by_no[1].group_voltage == 409.0
    assert by_no[6].group_voltage == 412.30002


def test_channel_inventory_excludes_never_produced_inputs():
    chans = m.channel_inventory(history_rows())
    # PV7 reads ~227 V open-circuit with zero current and epv7Total == 0:
    # an unused input, not a fault. It must not appear as a live channel.
    assert 7 not in [c.no for c in chans if c.kind == "mppt"]


def test_channel_inventory_finds_twelve_live_strings_grouped_by_voltage():
    chans = m.channel_inventory(history_rows())
    strings = [c for c in chans if c.kind == "string"]
    assert [c.no for c in strings] == list(range(1, 13))
    by_no = {c.no: c for c in strings}
    # parallel strings on one MPPT share an exactly-equal voltage
    assert by_no[1].group_voltage == by_no[2].group_voltage == 408.30002
    assert by_no[3].group_voltage == by_no[4].group_voltage == 402.7
    assert by_no[11].group_voltage == by_no[12].group_voltage == 414.1
    # strings 13-16 sit at 228.6 V on the never-produced MPPT7 -> excluded
    assert 13 not in by_no


def test_channel_inventory_leaves_parent_no_unset():
    # Pair-current sums do not cleanly biject to ipvN on this hardware, so the
    # string->MPPT link is deliberately not guessed (spec section 3(b)).
    chans = m.channel_inventory(history_rows())
    assert all(c.parent_no is None for c in chans)


def test_channel_inventory_lifetime_only_for_mppt():
    chans = m.channel_inventory(history_rows())
    assert all(c.lifetime_kwh is None for c in chans if c.kind == "string")


def test_best_row_picks_the_highest_pac_sample():
    rows = history_rows()
    assert m.best_row(rows)["time"] == "2026-07-25 12:00:00"
    assert m.best_row([]) == {}


def test_channel_inventory_empty_and_night_only():
    assert m.channel_inventory([]) == []
    night = [r for r in history_rows() if r["time"].endswith("00:02:56")]
    chans = m.channel_inventory(night)
    # lifetime counters are present even at night, so MPPT inputs are still
    # discovered; strings need daylight current and so are not.
    assert [c.no for c in chans if c.kind == "mppt"] == [1, 2, 3, 4, 5, 6]
    assert [c for c in chans if c.kind == "string"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -k inventory -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'channel_inventory'`.

- [ ] **Step 3: Write minimal implementation**

Append to `solaranalysis/strings/mappers.py`:

```python
@dataclass
class ChannelInfo:
    kind: str                        # 'mppt' | 'string'
    no: int
    parent_no: int | None = None     # string -> MPPT input; see below
    group_voltage: float | None = None
    lifetime_kwh: float | None = None   # MPPT inputs only (epvNTotal)


def best_row(rows: list[dict]) -> dict:
    """The day's highest-`pac` sample — the reading closest to solar peak.

    Night rows carry zeros for every voltage and current, so anything that
    needs a live electrical picture keys off this row rather than the first.
    """
    best, best_pac = {}, None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        pac = _num(r.get("pac"))
        if pac is not None and (best_pac is None or pac > best_pac):
            best, best_pac = r, pac
    if not best:
        for r in rows or []:
            if isinstance(r, dict):
                return r
    return best


def channel_inventory(rows: list[dict]) -> list[ChannelInfo]:
    """Which channels are live, from a day's sample rows.

    An MPPT input counts as live once it has produced anything ever
    (`epvNTotal > 0`); this rejects unused inputs that still float an
    open-circuit voltage. An individual string counts as live when it carries
    current at the day's peak; this rejects strings hanging off a dead input.

    `parent_no` (string -> MPPT) is left None on purpose: pair-current sums do
    not cleanly biject to `ipvN` on this hardware, so any mapping would be a
    guess. Grouping parallel strings by exactly-equal `group_voltage` is
    reliable and is what the Phase C2 imbalance rule uses.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []
    peak = best_row(rows)
    out: list[ChannelInfo] = []

    for n in range(1, MPPT_MAX + 1):
        lifetime = None
        for r in rows:                      # any row carries the lifetime counter
            lifetime = _num(r.get(f"epv{n}Total"))
            if lifetime is not None:
                break
        if not lifetime:                    # None or 0.0 -> never produced
            continue
        out.append(ChannelInfo(kind="mppt", no=n,
                               group_voltage=_num(peak.get(f"vpv{n}")),
                               lifetime_kwh=lifetime))

    for n in range(1, STRING_MAX + 1):
        current = _num(peak.get(f"currentString{n}"))
        if not current:                     # None or 0.0 -> not carrying current
            continue
        out.append(ChannelInfo(kind="string", no=n,
                               group_voltage=_num(peak.get(f"vString{n}"))))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/mappers.py tests/strings/test_mappers.py
git commit -m "feat: discover live MPPT inputs and strings from a day's samples"
```

---

### Task 5: `map_day_energy` — per-input daily energy, share, peak

**Files:**
- Modify: `solaranalysis/strings/mappers.py`
- Test: `tests/strings/test_mappers.py` (append)

**Interfaces:**
- Consumes: `mappers._num`, `mappers.MPPT_MAX`, `mappers.SAMPLE_MINUTES`, `mappers.sample_day`.
- Produces:
  - `mappers.ChannelDayEnergy` dataclass: `channel_no: int`, `energy_kwh: float | None`, `share_of_total: float | None`, `peak_w: float | None`, `peak_at: str | None`, `producing_minutes: int`
  - `mappers.map_day_energy(rows: list[dict]) -> list[ChannelDayEnergy]`

Key decisions, both from spec §10:
- Energy is the day's **maximum** `epvNToday`, not blindly the last sample — robust if a datalogger reconnect ever restates the counter mid-day.
- A row is emitted for every input with `epvNTotal > 0`, so a channel that dies gets a genuine `0.0` row rather than vanishing. `share_of_total` denominates against the summed channel energies (not `eacToday`, which is post-inverter AC), so the shares sum to exactly 1.0.

- [ ] **Step 1: Write the failing test**

Append to `tests/strings/test_mappers.py`:

```python
import pytest


def test_map_day_energy_uses_the_days_max_counter():
    rows = m.map_day_energy(history_rows())
    assert [r.channel_no for r in rows] == [1, 2, 3, 4, 5, 6]
    by = {r.channel_no: r for r in rows}
    assert isinstance(by[1], m.ChannelDayEnergy)
    assert by[1].energy_kwh == 57.3      # 23:57 row, not the 12:00 row's 25.9
    assert by[2].energy_kwh == 60.4
    assert by[4].energy_kwh == 37.3
    assert by[6].energy_kwh == 41.2


def test_map_day_energy_shares_sum_to_one():
    rows = m.map_day_energy(history_rows())
    total = 57.3 + 60.4 + 55.5 + 37.3 + 40.9 + 41.2      # 292.6
    by = {r.channel_no: r for r in rows}
    assert by[1].share_of_total == pytest.approx(57.3 / total)
    assert by[4].share_of_total == pytest.approx(37.3 / total)
    assert sum(r.share_of_total for r in rows) == pytest.approx(1.0)


def test_map_day_energy_records_peak_power_and_time():
    by = {r.channel_no: r for r in m.map_day_energy(history_rows())}
    assert by[1].peak_w == 7975.5
    assert by[1].peak_at == "2026-07-25 12:00:00"
    assert by[4].peak_w == 5281.9
    # only one of the three fixture rows is producing -> one 5-minute slot
    assert by[1].producing_minutes == m.SAMPLE_MINUTES


def test_map_day_energy_excludes_never_produced_inputs():
    assert 7 not in [r.channel_no for r in m.map_day_energy(history_rows())]


def test_map_day_energy_emits_a_real_zero_for_a_channel_that_stopped():
    # epv3Total > 0 (it has produced before) but today it made nothing.
    rows = [dict(r) for r in history_rows()]
    for r in rows:
        r["epv3Today"] = 0.0
        r["ppv3"] = 0.0
    by = {x.channel_no: x for x in m.map_day_energy(rows)}
    assert 3 in by                       # present, not silently dropped
    assert by[3].energy_kwh == 0.0
    assert by[3].share_of_total == 0.0
    assert by[3].producing_minutes == 0


def test_map_day_energy_share_is_none_when_nothing_produced():
    night = [r for r in history_rows() if r["time"].endswith("00:02:56")]
    rows = m.map_day_energy(night)
    assert [r.channel_no for r in rows] == [1, 2, 3, 4, 5, 6]
    assert all(r.energy_kwh == 0.0 for r in rows)
    assert all(r.share_of_total is None for r in rows)   # 0/0 is not a share
    assert all(r.peak_at is None for r in rows)


def test_map_day_energy_empty():
    assert m.map_day_energy([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -k day_energy -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'map_day_energy'`.

- [ ] **Step 3: Write minimal implementation**

Append to `solaranalysis/strings/mappers.py`:

```python
@dataclass
class ChannelDayEnergy:
    channel_no: int
    energy_kwh: float | None = None
    share_of_total: float | None = None
    peak_w: float | None = None
    peak_at: str | None = None
    producing_minutes: int = 0


def map_day_energy(rows: list[dict]) -> list[ChannelDayEnergy]:
    """A day's sample rows -> one record per MPPT input that has ever produced.

    `epvNToday` is a counter that resets at midnight, so the day's total is its
    MAXIMUM over the day rather than blindly the newest sample — that stays
    correct if a datalogger reconnect ever restates it.

    Channels with `epvNTotal == 0` never produced and are skipped entirely; a
    channel that HAS produced before but made nothing today still gets an
    explicit 0.0 row, because a missing row is not a zero row.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []

    out: list[ChannelDayEnergy] = []
    for n in range(1, MPPT_MAX + 1):
        lifetime = next((v for v in (_num(r.get(f"epv{n}Total")) for r in rows)
                         if v is not None), None)
        if not lifetime:
            continue

        energy, peak_w, peak_at, producing = None, None, None, 0
        for r in rows:
            e = _num(r.get(f"epv{n}Today"))
            if e is not None and (energy is None or e > energy):
                energy = e
            p = _num(r.get(f"ppv{n}"))
            if p is not None and p > 0:
                producing += 1
                if peak_w is None or p > peak_w:
                    peak_w, peak_at = p, r.get("time")
        out.append(ChannelDayEnergy(
            channel_no=n, energy_kwh=energy, peak_w=peak_w, peak_at=peak_at,
            producing_minutes=producing * SAMPLE_MINUTES))

    total = sum(r.energy_kwh for r in out if r.energy_kwh is not None)
    for r in out:
        if total and r.energy_kwh is not None:
            r.share_of_total = r.energy_kwh / total
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: PASS (21 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/mappers.py tests/strings/test_mappers.py
git commit -m "feat: per-input daily energy, share-of-total and peak power"
```

---

### Task 6: `map_channel_samples` — the intraday per-channel series

**Files:**
- Modify: `solaranalysis/strings/mappers.py`
- Test: `tests/strings/test_mappers.py` (append)

**Interfaces:**
- Consumes: `mappers.ChannelInfo`, `mappers._num`, `mappers.sample_day`.
- Produces:
  - `mappers.ChannelSample` dataclass: `sampled_at: str`, `day: str`, `kind: str`, `no: int`, `power_w: float | None`, `voltage_v: float | None`, `current_a: float | None`
  - `mappers.map_channel_samples(rows: list[dict], channels: list[ChannelInfo]) -> list[ChannelSample]`

**`channels` is a required filter, not an optimisation.** The payload carries all
16 MPPT and all 32 string keys with zero values, so emitting unconditionally
would store 48 × 288 = 13,824 rows/day. Filtering to the live inventory gives
18 × 288 = 5,184 — the figure the spec budgets for.

- [ ] **Step 1: Write the failing test**

Append to `tests/strings/test_mappers.py`:

```python
def test_map_channel_samples_covers_live_channels_only():
    rows = history_rows()
    chans = m.channel_inventory(rows)
    samples = m.map_channel_samples(rows, chans)
    # 6 MPPT + 12 strings across 3 rows -- NOT 48 x 3, which is what emitting
    # every present-but-zero key would give
    assert len(samples) == 18 * 3
    assert {(s.kind, s.no) for s in samples} == {(c.kind, c.no) for c in chans}


def test_map_channel_samples_carries_the_electrical_triple():
    rows = history_rows()
    samples = m.map_channel_samples(rows, m.channel_inventory(rows))
    peak = {(s.kind, s.no): s for s in samples
            if s.sampled_at == "2026-07-25 12:00:00"}
    mppt1 = peak[("mppt", 1)]
    assert isinstance(mppt1, m.ChannelSample)
    assert mppt1.power_w == 7975.5
    assert mppt1.voltage_v == 409.0
    assert mppt1.current_a == 19.5
    assert mppt1.day == "2026-07-25"


def test_map_channel_samples_strings_have_no_power():
    rows = history_rows()
    samples = m.map_channel_samples(rows, m.channel_inventory(rows))
    peak = {(s.kind, s.no): s for s in samples
            if s.sampled_at == "2026-07-25 12:00:00"}
    s1, s2 = peak[("string", 1)], peak[("string", 2)]
    assert s1.power_w is None and s2.power_w is None
    assert s1.current_a == 8.900001 and s2.current_a == 10.6
    # the same MPPT, so an exactly-equal voltage -- and a 16% current imbalance
    assert s1.voltage_v == s2.voltage_v == 408.30002


def test_map_channel_samples_skips_rows_without_a_parseable_time():
    rows = [{"time": "garbage", "ppv1": 1.0}]
    chans = [m.ChannelInfo(kind="mppt", no=1, lifetime_kwh=5.0)]
    assert m.map_channel_samples(rows, chans) == []


def test_map_channel_samples_empty():
    assert m.map_channel_samples([], []) == []
    assert m.map_channel_samples(history_rows(), []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -k channel_samples -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'map_channel_samples'`.

- [ ] **Step 3: Write minimal implementation**

Append to `solaranalysis/strings/mappers.py`:

```python
@dataclass
class ChannelSample:
    sampled_at: str
    day: str
    kind: str
    no: int
    power_w: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None


_FIELDS = {
    "mppt": ("ppv{n}", "vpv{n}", "ipv{n}"),
    "string": (None, "vString{n}", "currentString{n}"),   # no per-string power
}


def map_channel_samples(rows: list[dict],
                        channels: list[ChannelInfo]) -> list[ChannelSample]:
    """A day's rows x the live inventory -> the intraday per-channel series.

    `channels` is a required filter: the payload carries all 16 MPPT and all 32
    string keys with zero values, so emitting them all would store 48 rows per
    sample instead of the 18 that are real hardware.
    """
    out: list[ChannelSample] = []
    live = [c for c in (channels or []) if c.kind in _FIELDS]
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        day = sample_day(r)
        at = r.get("time")
        if not day or not at:
            continue
        for c in live:
            p_key, v_key, i_key = _FIELDS[c.kind]
            out.append(ChannelSample(
                sampled_at=str(at), day=day, kind=c.kind, no=c.no,
                power_w=None if p_key is None else _num(r.get(p_key.format(n=c.no))),
                voltage_v=_num(r.get(v_key.format(n=c.no))),
                current_a=_num(r.get(i_key.format(n=c.no)))))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: PASS (26 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/mappers.py tests/strings/test_mappers.py
git commit -m "feat: intraday per-channel sample series, filtered to live hardware"
```

---

### Task 7: `map_inverter_samples` — health and native fault flags

**Files:**
- Modify: `solaranalysis/strings/mappers.py`
- Test: `tests/strings/test_mappers.py` (append)

**Interfaces:**
- Consumes: `mappers._num`, `mappers._text`, `mappers.sample_day`.
- Produces:
  - `mappers.InverterSample` dataclass with fields `sampled_at`, `day`, `pac_w`, `e_ac_today_kwh`, `temp_c`, `temp2_c`, `temp3_c`, `temp4_c`, `temp5_c`, `pv_iso_kohm`, `gfci_ma`, `status`, `derating_mode`, `str_break`, `str_unbalance`, `str_unmatch`, `warn_code`, `fault_code1`, `fault_code2`, `fault_type`
  - `mappers.map_inverter_samples(rows: list[dict]) -> list[InverterSample]`

Note the source-field spelling: Growatt writes `StrUnblance` (sic). The record
field is the corrected `str_unbalance`.

- [ ] **Step 1: Write the failing test**

Append to `tests/strings/test_mappers.py`:

```python
def test_map_inverter_samples_reads_health_and_flags():
    samples = m.map_inverter_samples(history_rows())
    assert len(samples) == 3
    by = {s.sampled_at: s for s in samples}
    peak = by["2026-07-25 12:00:00"]
    assert isinstance(peak, m.InverterSample)
    assert peak.day == "2026-07-25"
    assert peak.pac_w == 40460.9
    assert peak.e_ac_today_kwh == 135.2
    assert peak.temp_c == 43.4 and peak.temp3_c == 52.3 and peak.temp5_c == 42.0
    assert peak.pv_iso_kohm == 250.0     # arrives as the string "250"
    assert peak.gfci_ma == 3.0           # arrives as the string "3"
    assert peak.status == "1"            # arrives as the number 1


def test_map_inverter_samples_preserves_flag_tokens_as_text():
    by = {s.sampled_at: s for s in m.map_inverter_samples(history_rows())}
    peak = by["2026-07-25 12:00:00"]
    night = by["2026-07-25 00:02:56"]
    # the live payload quotes these inconsistently; both must land as "0"
    assert peak.str_break == "0" and night.str_break == "0"
    assert peak.str_unbalance == "0"     # source field is spelled StrUnblance
    assert peak.str_unmatch == "0"
    assert peak.warn_code == "0"
    assert peak.fault_code1 == "0" and peak.fault_code2 == "0"
    assert peak.derating_mode == "0"


def test_map_inverter_samples_flags_a_real_string_fault():
    rows = [dict(r) for r in history_rows()]
    rows[1]["StrBreak"] = "4"
    rows[1]["StrUnblance"] = 1
    by = {s.sampled_at: s for s in m.map_inverter_samples(rows)}
    assert by["2026-07-25 12:00:00"].str_break == "4"
    assert by["2026-07-25 12:00:00"].str_unbalance == "1"
    assert by["2026-07-25 23:57:09"].str_break == "0"


def test_map_inverter_samples_skips_unparseable_rows():
    assert m.map_inverter_samples([{"pac": 1.0}]) == []      # no time
    assert m.map_inverter_samples([]) == []
    assert m.map_inverter_samples(["junk"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -k inverter_samples -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'map_inverter_samples'`.

- [ ] **Step 3: Write minimal implementation**

Append to `solaranalysis/strings/mappers.py`:

```python
@dataclass
class InverterSample:
    sampled_at: str
    day: str
    pac_w: float | None = None
    e_ac_today_kwh: float | None = None
    temp_c: float | None = None
    temp2_c: float | None = None
    temp3_c: float | None = None
    temp4_c: float | None = None
    temp5_c: float | None = None
    pv_iso_kohm: float | None = None
    gfci_ma: float | None = None
    status: str | None = None
    derating_mode: str | None = None
    str_break: str | None = None
    str_unbalance: str | None = None
    str_unmatch: str | None = None
    warn_code: str | None = None
    fault_code1: str | None = None
    fault_code2: str | None = None
    fault_type: str | None = None


def map_inverter_samples(rows: list[dict]) -> list[InverterSample]:
    """A day's rows -> whole-inverter health and native string diagnostics.

    `StrBreak` / `StrUnblance` (Growatt's spelling) / `StrUnmatch` are the
    inverter's OWN string-fault verdicts, so they are authoritative when
    non-zero. Codes are kept as text to preserve the raw token.
    """
    out: list[InverterSample] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        day = sample_day(r)
        at = r.get("time")
        if not day or not at:
            continue
        out.append(InverterSample(
            sampled_at=str(at), day=day,
            pac_w=_num(r.get("pac")),
            e_ac_today_kwh=_num(r.get("eacToday")),
            temp_c=_num(r.get("temperature")),
            temp2_c=_num(r.get("temperature2")),
            temp3_c=_num(r.get("temperature3")),
            temp4_c=_num(r.get("temperature4")),
            temp5_c=_num(r.get("temperature5")),
            pv_iso_kohm=_num(r.get("pvIso")),
            gfci_ma=_num(r.get("gfci")),
            status=_text(r.get("status")),
            derating_mode=_text(r.get("deratingMode")),
            str_break=_text(r.get("StrBreak")),
            str_unbalance=_text(r.get("StrUnblance")),
            str_unmatch=_text(r.get("StrUnmatch")),
            warn_code=_text(r.get("warnCode")),
            fault_code1=_text(r.get("faultCode1")),
            fault_code2=_text(r.get("faultCode2")),
            fault_type=_text(r.get("faultType")),
        ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_mappers.py -v`
Expected: PASS (30 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/mappers.py tests/strings/test_mappers.py
git commit -m "feat: inverter health and native string-fault sample mapper"
```

---

### Task 8: `store.py` — upserts and window loaders

**Files:**
- Create: `solaranalysis/strings/store.py`
- Test: `tests/strings/test_store.py`

**Interfaces:**
- Consumes: `web.db.connect`/`init_db` (Task 1), all mapper record types (Tasks 4–7).
- Produces:
  - `store.save_channels(conn, device_sn, plant_uid, channels, now) -> None`
  - `store.save_day_energy(conn, device_sn, day, rows, now) -> None`
  - `store.save_channel_samples(conn, device_sn, samples) -> None`
  - `store.save_inverter_samples(conn, device_sn, samples) -> None`
  - `store.load_channels(conn, device_sn) -> list[dict]`
  - `store.load_day_energy(conn, device_sn, day) -> list[dict]`
  - `store.load_energy_window(conn, device_sn, since_day) -> list[dict]`
  - `store.load_channel_samples(conn, device_sn, day) -> list[dict]`
  - `store.load_inverter_samples(conn, device_sn, day) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/strings/test_store.py`:

```python
from solaranalysis.web import db
from solaranalysis.strings import store
from solaranalysis.strings.mappers import (
    ChannelInfo, ChannelDayEnergy, ChannelSample, InverterSample)


def _conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def test_save_channels_upserts_and_preserves_first_seen():
    conn = _conn()
    chans = [ChannelInfo(kind="mppt", no=1, group_voltage=409.0, lifetime_kwh=940.8),
             ChannelInfo(kind="string", no=1, group_voltage=408.30002)]
    store.save_channels(conn, "SN-A", "growatt-10950561", chans,
                        now="2026-07-10T00:00:00+00:00")
    chans[0].lifetime_kwh = 998.1
    store.save_channels(conn, "SN-A", "growatt-10950561", chans,
                        now="2026-07-26T00:00:00+00:00")
    rows = store.load_channels(conn, "SN-A")
    assert len(rows) == 2
    mppt = next(r for r in rows if r["channel_kind"] == "mppt")
    assert mppt["channel_no"] == 1
    assert mppt["group_voltage"] == 409.0
    assert mppt["lifetime_kwh"] == 998.1                        # refreshed
    assert mppt["plant_uid"] == "growatt-10950561"
    assert mppt["parent_channel_no"] is None
    assert mppt["first_seen_utc"] == "2026-07-10T00:00:00+00:00"  # preserved
    assert mppt["last_seen_utc"] == "2026-07-26T00:00:00+00:00"   # refreshed


def test_mppt_and_string_channel_one_do_not_collide():
    conn = _conn()
    store.save_channels(conn, "SN-A", "uid", [
        ChannelInfo(kind="mppt", no=1, lifetime_kwh=1.0),
        ChannelInfo(kind="string", no=1)], now="n")
    kinds = sorted(r["channel_kind"] for r in store.load_channels(conn, "SN-A"))
    assert kinds == ["mppt", "string"]


def test_save_day_energy_upserts_latest_value():
    conn = _conn()
    store.save_day_energy(conn, "SN-A", "2026-07-25", [
        ChannelDayEnergy(1, 57.3, 0.1958, 7975.5, "2026-07-25 12:00:00", 780)],
        now="2026-07-26T00:00:00+00:00")
    store.save_day_energy(conn, "SN-A", "2026-07-25", [
        ChannelDayEnergy(1, 57.9, 0.1961, 8010.0, "2026-07-25 12:05:00", 785)],
        now="2026-07-26T06:00:00+00:00")
    rows = store.load_day_energy(conn, "SN-A", "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["energy_kwh"] == 57.9
    assert rows[0]["peak_w"] == 8010.0
    assert rows[0]["peak_at"] == "2026-07-25 12:05:00"
    assert rows[0]["producing_minutes"] == 785
    assert rows[0]["updated_at_utc"] == "2026-07-26T06:00:00+00:00"


def test_load_energy_window_filters_by_day_and_device():
    conn = _conn()
    for day in ("2026-07-11", "2026-07-18", "2026-07-25"):
        store.save_day_energy(conn, "SN-A", day,
                              [ChannelDayEnergy(1, 50.0, 0.2, 7000.0, None, 700)],
                              now="n")
    store.save_day_energy(conn, "SN-B", "2026-07-25",
                          [ChannelDayEnergy(1, 10.0, 0.5, 100.0, None, 60)],
                          now="n")
    rows = store.load_energy_window(conn, "SN-A", "2026-07-18")
    assert sorted(r["day"] for r in rows) == ["2026-07-18", "2026-07-25"]
    assert all(r["device_sn"] == "SN-A" for r in rows)


def test_save_channel_samples_is_idempotent():
    conn = _conn()
    s = [ChannelSample("2026-07-25 12:00:00", "2026-07-25", "mppt", 1,
                       7975.5, 409.0, 19.5),
         ChannelSample("2026-07-25 12:00:00", "2026-07-25", "string", 1,
                       None, 408.30002, 8.900001)]
    store.save_channel_samples(conn, "SN-A", s)
    store.save_channel_samples(conn, "SN-A", s)      # re-run the same day
    rows = store.load_channel_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 2
    mppt = next(r for r in rows if r["channel_kind"] == "mppt")
    assert mppt["power_w"] == 7975.5 and mppt["current_a"] == 19.5
    string = next(r for r in rows if r["channel_kind"] == "string")
    assert string["power_w"] is None and string["voltage_v"] == 408.30002


def test_save_inverter_samples_is_idempotent_and_keeps_flags():
    conn = _conn()
    s = [InverterSample("2026-07-25 12:00:00", "2026-07-25", pac_w=40460.9,
                        e_ac_today_kwh=135.2, temp_c=43.4, pv_iso_kohm=250.0,
                        gfci_ma=3.0, status="1", str_break="0",
                        str_unbalance="0", str_unmatch="0", warn_code="0")]
    store.save_inverter_samples(conn, "SN-A", s)
    store.save_inverter_samples(conn, "SN-A", s)
    rows = store.load_inverter_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["pac_w"] == 40460.9
    assert rows[0]["pv_iso_kohm"] == 250.0
    assert rows[0]["status"] == "1"
    assert rows[0]["str_unbalance"] == "0"


def test_save_helpers_tolerate_empty_input():
    conn = _conn()
    store.save_channels(conn, "SN-A", "uid", [], now="n")
    store.save_day_energy(conn, "SN-A", "2026-07-25", [], now="n")
    store.save_channel_samples(conn, "SN-A", [])
    store.save_inverter_samples(conn, "SN-A", [])
    assert store.load_channels(conn, "SN-A") == []
    assert store.load_channel_samples(conn, "SN-A", "2026-07-25") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.strings.store'`.

- [ ] **Step 3: Write minimal implementation**

Create `solaranalysis/strings/store.py`:

```python
"""Persist Growatt channel inventory, daily energy and 5-minute samples into
app.db (schema v7 tables). Every write is an upsert, so re-collecting a day is
safe and idempotent."""
from __future__ import annotations
import sqlite3

from .mappers import (ChannelInfo, ChannelDayEnergy, ChannelSample,
                      InverterSample)


def save_channels(conn: sqlite3.Connection, device_sn: str, plant_uid: str | None,
                  channels: list[ChannelInfo], now: str) -> None:
    conn.executemany(
        "INSERT INTO inverter_channels"
        "(device_sn, channel_kind, channel_no, parent_channel_no, group_voltage,"
        " plant_uid, lifetime_kwh, first_seen_utc, last_seen_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, channel_kind, channel_no) DO UPDATE SET "
        "parent_channel_no=excluded.parent_channel_no, "
        "group_voltage=excluded.group_voltage, plant_uid=excluded.plant_uid, "
        "lifetime_kwh=excluded.lifetime_kwh, last_seen_utc=excluded.last_seen_utc",
        [(device_sn, c.kind, c.no, c.parent_no, c.group_voltage, plant_uid,
          c.lifetime_kwh, now, now) for c in channels])


def save_day_energy(conn: sqlite3.Connection, device_sn: str, day: str,
                    rows: list[ChannelDayEnergy], now: str) -> None:
    conn.executemany(
        "INSERT INTO channel_day_energy"
        "(device_sn, channel_no, day, energy_kwh, share_of_total, peak_w,"
        " peak_at, producing_minutes, updated_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, channel_no, day) DO UPDATE SET "
        "energy_kwh=excluded.energy_kwh, share_of_total=excluded.share_of_total, "
        "peak_w=excluded.peak_w, peak_at=excluded.peak_at, "
        "producing_minutes=excluded.producing_minutes, "
        "updated_at_utc=excluded.updated_at_utc",
        [(device_sn, r.channel_no, day, r.energy_kwh, r.share_of_total, r.peak_w,
          r.peak_at, r.producing_minutes, now) for r in rows])


def save_channel_samples(conn: sqlite3.Connection, device_sn: str,
                         samples: list[ChannelSample]) -> None:
    conn.executemany(
        "INSERT INTO channel_samples"
        "(device_sn, sampled_at, day, channel_kind, channel_no, power_w,"
        " voltage_v, current_a) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, sampled_at, channel_kind, channel_no) DO UPDATE SET "
        "power_w=excluded.power_w, voltage_v=excluded.voltage_v, "
        "current_a=excluded.current_a",
        [(device_sn, s.sampled_at, s.day, s.kind, s.no, s.power_w, s.voltage_v,
          s.current_a) for s in samples])


_INV_COLS = ("pac_w", "e_ac_today_kwh", "temp_c", "temp2_c", "temp3_c",
             "temp4_c", "temp5_c", "pv_iso_kohm", "gfci_ma", "status",
             "derating_mode", "str_break", "str_unbalance", "str_unmatch",
             "warn_code", "fault_code1", "fault_code2", "fault_type")


def save_inverter_samples(conn: sqlite3.Connection, device_sn: str,
                          samples: list[InverterSample]) -> None:
    sets = ", ".join(f"{c}=excluded.{c}" for c in _INV_COLS)
    cols = ", ".join(_INV_COLS)
    marks = ",".join("?" * (3 + len(_INV_COLS)))
    conn.executemany(
        f"INSERT INTO inverter_samples(device_sn, sampled_at, day, {cols}) "
        f"VALUES ({marks}) "
        f"ON CONFLICT(device_sn, sampled_at) DO UPDATE SET {sets}",
        [(device_sn, s.sampled_at, s.day, *(getattr(s, c) for c in _INV_COLS))
         for s in samples])


def load_channels(conn: sqlite3.Connection, device_sn: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM inverter_channels WHERE device_sn=? "
        "ORDER BY channel_kind, channel_no", (device_sn,))]


def load_day_energy(conn: sqlite3.Connection, device_sn: str, day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_day_energy WHERE device_sn=? AND day=? "
        "ORDER BY channel_no", (device_sn, day))]


def load_energy_window(conn: sqlite3.Connection, device_sn: str,
                       since_day: str) -> list[dict]:
    """All daily energy rows on/after `since_day`, oldest channel/day first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_day_energy WHERE device_sn=? AND day>=? "
        "ORDER BY channel_no, day", (device_sn, since_day))]


def load_channel_samples(conn: sqlite3.Connection, device_sn: str,
                         day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_samples WHERE device_sn=? AND day=? "
        "ORDER BY sampled_at, channel_kind, channel_no", (device_sn, day))]


def load_inverter_samples(conn: sqlite3.Connection, device_sn: str,
                          day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM inverter_samples WHERE device_sn=? AND day=? "
        "ORDER BY sampled_at", (device_sn, day))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_store.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/store.py tests/strings/test_store.py
git commit -m "feat: idempotent store for channel inventory, energy and samples"
```

---

### Task 9: `collector.py` — page walk with per-day isolation

**Files:**
- Create: `solaranalysis/strings/collector.py`
- Test: `tests/strings/test_collector.py`

**Interfaces:**
- Consumes: `history_client.get_history_page`, `mappers.*`, `store.*`.
- Produces:
  - `collector.PAGES_PER_DAY == 4`
  - `collector.day_range(target: date, count: int) -> list[str]`
  - `collector.fetch_day_rows(bs, sn, day, datalog_sn) -> tuple[list[dict], int, bool]` — `(rows, pages_ok, complete)`
  - `collector.collect_day(bs, conn, inv, day, now) -> dict`
  - `collector.collect(conn, inv, days, now, bs=None, bs_for=None) -> list[dict]`

Failure semantics from spec §8: **page 0 carries end-of-day energy, so page 0
failing fails the day** (the exception propagates, `collect` rolls back and moves
on). Pages 1–3 failing still stores the day's energy, with the day marked
`partial`. An empty day (pre-install, or beyond the portal's ~90-day rejection
window) stores nothing and is **not** an error.

**`complete` is driven by *why the walk stopped*, not by how many pages it
fetched.** A day that ends because `haveNext` went false is complete however few
pages that took — the 149-row commissioning day of 2026-07-10 needs only two.
`complete` goes false in exactly two cases: a page after 0 raised, or all
`PAGES_PER_DAY` pages were consumed while `haveNext` was still true (which would
mean the portal had more than 288 samples and we truncated — surface it rather
than hide it).

- [ ] **Step 1: Write the failing test**

Create `tests/strings/test_collector.py`:

```python
import json
from datetime import date
from pathlib import Path

import pytest

from solaranalysis.web import db
from solaranalysis.strings import collector, store
from solaranalysis.strings.mappers import InverterInfo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INV = InverterInfo(serial="SN-A", model="MAX 70KTL3 LV", datalog_sn="DL-1",
                   nominal_power_w=70000.0, plant_id="10950561")


def _rows():
    payload = json.loads(
        (FIXTURES / "growatt_max_history.json").read_text(encoding="utf-8"))
    return payload["obj"]["datas"]


class FakeBS:
    """Serves the fixture rows as page 0, then an empty page — a complete
    short day, which is what the fixture's 3 rows represent."""
    def __init__(self, pages=None, fail_page=None):
        self.pages = pages if pages is not None else [_rows(), [], [], []]
        self.fail_page = fail_page
        self.requested = []

    def post_json(self, url, **kw):
        page = int(kw["form"]["start"])
        self.requested.append(page)
        if page == self.fail_page:
            raise RuntimeError("net::ERR_TIMED_OUT")
        datas = self.pages[page] if page < len(self.pages) else []
        return {"result": 1, "obj": {"datas": datas,
                                     "haveNext": page + 1 < len(self.pages)}}


def test_day_range_counts_back_from_target():
    assert collector.day_range(date(2026, 7, 25), 1) == ["2026-07-25"]
    assert collector.day_range(date(2026, 7, 25), 3) == [
        "2026-07-23", "2026-07-24", "2026-07-25"]


def test_fetch_day_rows_walks_all_four_pages():
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()])
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert bs.requested == [0, 1, 2, 3]
    assert pages_ok == 4 and complete is True
    assert len(rows) == 12                      # 3 fixture rows x 4 pages


def test_fetch_day_rows_stops_when_have_next_is_false():
    bs = FakeBS(pages=[_rows()])                # haveNext False after page 0
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert bs.requested == [0]
    # a short day is still a COMPLETE day -- the portal said there was no more
    assert pages_ok == 1 and complete is True and len(rows) == 3


def test_fetch_day_rows_raises_when_page_zero_fails():
    bs = FakeBS(fail_page=0)
    with pytest.raises(RuntimeError):
        collector.fetch_day_rows(bs, "SN-A", "2026-07-25", "DL-1")


def test_fetch_day_rows_tolerates_a_later_page_failing():
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()], fail_page=2)
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert pages_ok == 2 and complete is False   # pages 0 and 1 landed
    assert len(rows) == 6


def test_fetch_day_rows_flags_truncation_past_the_page_cap():
    # Five pages of data: the walk stops at the cap with haveNext still true,
    # which must be reported rather than passed off as a complete day.
    bs = FakeBS(pages=[_rows()] * 5)
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert pages_ok == collector.PAGES_PER_DAY
    assert complete is False


def test_collect_day_persists_inventory_energy_and_samples():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_day(FakeBS(), conn, INV, "2026-07-25", now="NOW")
    assert res["day"] == "2026-07-25"
    assert res["channels"] == 18                # 6 MPPT + 12 strings
    assert res["energy_rows"] == 6
    assert res["samples"] == 54                 # 18 channels x 3 rows
    assert res["partial"] is False               # stopped on an empty page

    chans = store.load_channels(conn, "SN-A")
    assert len(chans) == 18
    assert chans[0]["plant_uid"] == "growatt-10950561"

    energy = {r["channel_no"]: r for r in
              store.load_day_energy(conn, "SN-A", "2026-07-25")}
    assert energy[1]["energy_kwh"] == 57.3
    assert energy[1]["peak_w"] == 7975.5

    assert len(store.load_channel_samples(conn, "SN-A", "2026-07-25")) == 54
    assert len(store.load_inverter_samples(conn, "SN-A", "2026-07-25")) == 3


def test_collect_day_marks_a_partial_day():
    conn = db.connect(":memory:"); db.init_db(conn)
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()], fail_page=1)
    res = collector.collect_day(bs, conn, INV, "2026-07-25", now="NOW")
    assert res["partial"] is True and res["pages_ok"] == 1
    # the day's energy still landed -- page 0 is what carries it
    assert len(store.load_day_energy(conn, "SN-A", "2026-07-25")) == 6


def test_collect_day_treats_an_empty_day_as_normal():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_day(FakeBS(pages=[[]]), conn, INV, "2026-07-05",
                                now="NOW")
    assert res["empty"] is True
    assert "error" not in res
    assert store.load_day_energy(conn, "SN-A", "2026-07-05") == []
    assert store.load_channels(conn, "SN-A") == []


def test_collect_isolates_a_failing_day_and_rolls_it_back():
    conn = db.connect(":memory:"); db.init_db(conn)
    results = collector.collect(
        conn=conn, inv=INV, days=["2026-07-24", "2026-07-25"], now="NOW",
        bs_for={"2026-07-24": FakeBS(fail_page=0), "2026-07-25": FakeBS()})
    by = {r["day"]: r for r in results}
    assert "error" in by["2026-07-24"]
    assert by["2026-07-25"]["energy_rows"] == 6
    # the failing day wrote nothing; the good day is committed
    assert store.load_day_energy(conn, "SN-A", "2026-07-24") == []
    assert len(store.load_day_energy(conn, "SN-A", "2026-07-25")) == 6
    assert len(store.load_channels(conn, "SN-A")) == 18


def test_collect_uses_one_session_for_every_day():
    conn = db.connect(":memory:"); db.init_db(conn)
    bs = FakeBS()
    results = collector.collect(conn=conn, inv=INV,
                                days=["2026-07-24", "2026-07-25"], now="NOW",
                                bs=bs)
    assert len(results) == 2
    assert all("error" not in r for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.strings.collector'`.

- [ ] **Step 3: Write minimal implementation**

Create `solaranalysis/strings/collector.py`:

```python
"""Orchestrate per-day Growatt string collection: walk the day's pages, map,
store. Pure of login concerns — the CLI supplies an authenticated
BrowserSession."""
from __future__ import annotations
from datetime import date, timedelta

from . import history_client as hc
from . import mappers, store

PAGES_PER_DAY = 4          # 288 samples/day at 80 rows/page


def day_range(target: date, count: int) -> list[str]:
    """`count` consecutive ISO days ending at `target` (oldest first)."""
    count = max(1, count)
    return [(target - timedelta(days=n)).isoformat()
            for n in range(count - 1, -1, -1)]


def fetch_day_rows(bs, sn: str, day: str,
                   datalog_sn: str) -> tuple[list[dict], int, bool]:
    """Every 5-minute sample for `day`: (rows, pages_fetched, complete).

    Page 0 holds the day's last sample, and therefore its per-channel energy
    counters, so a page-0 failure propagates and fails the whole day. Pages 1-3
    are intraday enrichment: if one fails we stop and keep what we have.

    `complete` reflects WHY the walk stopped, not how far it got — a short day
    whose last page says `haveNext: false` is complete. It is false only when a
    later page raised, or when the page cap was hit with `haveNext` still true
    (i.e. the portal had more than 288 samples and we truncated).
    """
    rows: list[dict] = []
    pages_ok, complete = 0, False
    for page in range(PAGES_PER_DAY):
        try:
            obj = (hc.get_history_page(bs, sn, day, page, datalog_sn)
                   or {}).get("obj") or {}
        except Exception:
            if page == 0:
                raise
            break                     # complete stays False
        datas = [r for r in (obj.get("datas") or []) if isinstance(r, dict)]
        rows += datas
        pages_ok += 1
        if not datas or not obj.get("haveNext"):
            complete = True
            break
    return rows, pages_ok, complete


def collect_day(bs, conn, inv: mappers.InverterInfo, day: str, now: str) -> dict:
    """Fetch, map and persist one plant-local day for one inverter."""
    rows, pages_ok, complete = fetch_day_rows(bs, inv.serial, day,
                                              inv.datalog_sn or "")
    if not rows:
        # Pre-install days (and days beyond the portal's ~90-day rejection
        # window) answer with an empty list. That is normal, not a failure.
        return {"day": day, "empty": True, "pages_ok": pages_ok}

    channels = mappers.channel_inventory(rows)
    plant_uid = f"growatt-{inv.plant_id}" if inv.plant_id else None
    store.save_channels(conn, inv.serial, plant_uid, channels, now)

    energy = mappers.map_day_energy(rows)
    store.save_day_energy(conn, inv.serial, day, energy, now)

    samples = mappers.map_channel_samples(rows, channels)
    store.save_channel_samples(conn, inv.serial, samples)
    inv_samples = mappers.map_inverter_samples(rows)
    store.save_inverter_samples(conn, inv.serial, inv_samples)

    return {"day": day, "empty": False, "pages_ok": pages_ok,
            "partial": not complete,
            "channels": len(channels), "energy_rows": len(energy),
            "samples": len(samples), "inverter_samples": len(inv_samples)}


def collect(conn, inv: mappers.InverterInfo, days: list[str], now: str,
            bs=None, bs_for: dict | None = None) -> list[dict]:
    """Collect every day. Provide either one `bs` (production) or a
    `bs_for` {day: bs} mapping (tests). Per-day failures are isolated:
    logged into the result list and skipped, never aborting the run."""
    results = []
    for day in days:
        session = bs_for[day] if bs_for is not None else bs
        try:
            res = collect_day(session, conn, inv, day, now)
            conn.commit()
        except Exception as e:            # isolate per-day failure
            conn.rollback()
            res = {"day": day, "error": str(e)}
        results.append(res)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_collector.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/strings/collector.py tests/strings/test_collector.py
git commit -m "feat: per-day Growatt string collection with failure isolation"
```

---

### Task 10: CLI entry point

**Files:**
- Create: `solaranalysis/strings/cli.py`, `solaranalysis/strings/__main__.py`
- Test: `tests/strings/test_cli.py`

**Interfaces:**
- Consumes: `collector.*`, `mappers.parse_devices`, `history_client.get_device_list`, `_now.now_utc`, `web.db`, `web.repo`, `web.crypto`, `web.paths.Paths`, `adapters.growatt.GrowattAdapter`, `core.session_store.SessionStore`.
- Produces:
  - `cli.resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]`
  - `cli.growatt_plant_id(conn) -> int | None` — the web app's `plants.id`
  - `cli.first_plant_id(plants: list) -> str | None` — the portal's own plant id
  - `cli._build_parser() -> argparse.ArgumentParser`
  - `cli.main(argv=None, today=None) -> int`

Note the two different "plant id"s: `growatt_plant_id` returns the row id in the
web app's `plants` table (used to load credentials), while `first_plant_id`
returns the portal-side id such as `"10950561"` (used in API calls).

Exit codes match the optimizer CLI's contract: **2** when no enabled Growatt
plant is configured, **3** when the device list comes back empty (almost always
a failed or unauthorized fetch rather than a genuinely deviceless plant), **0**
otherwise. `--no-email` is deliberately **not** added here — Phase C2 owns
analysis, reporting and email.

- [ ] **Step 1: Write the failing test**

Create `tests/strings/test_cli.py`:

```python
from datetime import date

from solaranalysis.web import db
from solaranalysis.strings import cli


def test_resolve_days_default_is_yesterday():
    assert cli.resolve_days(None, 1, today=date(2026, 7, 26)) == ["2026-07-25"]


def test_resolve_days_explicit_date():
    assert cli.resolve_days("2026-07-11", 1, today=date(2026, 7, 26)) == ["2026-07-11"]


def test_resolve_days_backfill_counts_back_from_target():
    assert cli.resolve_days("2026-07-12", 3, today=date(2026, 7, 26)) == [
        "2026-07-10", "2026-07-11", "2026-07-12"]


def test_resolve_days_backfill_from_yesterday():
    got = cli.resolve_days(None, 90, today=date(2026, 7, 26))
    assert len(got) == 90 and got[-1] == "2026-07-25" and got[0] == "2026-04-27"


def test_growatt_plant_id_picks_the_enabled_growatt_plant():
    conn = db.connect(":memory:"); db.init_db(conn)
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('SE', 'solaredge', 1)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-off', 'growatt', 0)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-on', 'growatt', 1)")
    conn.commit()
    pid = cli.growatt_plant_id(conn)
    assert conn.execute("SELECT name FROM plants WHERE id=?",
                        (pid,)).fetchone()[0] == "G-on"


def test_growatt_plant_id_none_when_only_disabled_or_other_platforms():
    conn = db.connect(":memory:"); db.init_db(conn)
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-off', 'growatt', 0)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('SE', 'solaredge', 1)")
    conn.commit()
    assert cli.growatt_plant_id(conn) is None


def test_first_plant_id_reads_the_portal_side_id():
    assert cli.first_plant_id(
        [{"id": "10950561", "plantName": "Elcam Baram"}]) == "10950561"
    # coerced to str, since the portal is inconsistent about quoting it
    assert cli.first_plant_id([{"id": 10950561}]) == "10950561"


def test_first_plant_id_skips_junk_and_returns_none_when_empty():
    assert cli.first_plant_id([]) is None
    assert cli.first_plant_id(["junk", {}, {"id": None}, {"id": "7"}]) == "7"
    assert cli.first_plant_id(None) is None


def test_parser_requires_data_and_app_dirs():
    parsed = cli._build_parser().parse_args(
        ["--data-dir", "d", "--app-dir", "a", "--backfill", "17"])
    assert parsed.data_dir == "d" and parsed.app_dir == "a"
    assert parsed.backfill == 17 and parsed.date is None


def test_main_exits_2_without_an_enabled_growatt_plant(tmp_path, capsys):
    rc = cli.main(["--data-dir", str(tmp_path / "data"),
                   "--app-dir", str(tmp_path / "app")])
    assert rc == 2
    assert "growatt" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.strings.cli'`.

- [ ] **Step 3: Write minimal implementation**

Create `solaranalysis/strings/cli.py`:

```python
"""Standalone Growatt per-string collector entry point.

    python -m solaranalysis.strings --data-dir DIR --app-dir DIR
        [--date YYYY-MM-DD]   # default: yesterday
        [--backfill N]        # N days ending at --date (default 1)

Loads the enabled Growatt plant's credentials from app.db, authenticates via
the Growatt adapter (reusing its session cache), and collects per-MPPT-input
daily energy plus the 5-minute channel/inverter series into app.db.

Analysis, reporting and email are Phase C2 and are not wired here.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta

from dotenv import load_dotenv

from ..adapters._browser import BrowserSession
from ..adapters.growatt import GrowattAdapter
from ..core.session_store import SessionStore
from ..web import db, repo, crypto
from ..web.paths import Paths
from . import collector, history_client, mappers
from ._now import now_utc


def resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]:
    target = date.fromisoformat(date_arg) if date_arg else (today - timedelta(days=1))
    return collector.day_range(target, backfill)


def growatt_plant_id(conn) -> int | None:
    """The web app's `plants.id` for the enabled Growatt plant (credentials)."""
    for p in repo.list_plants(conn):
        if p["platform"] == "growatt" and p["enabled"]:
            return p["id"]
    return None


def first_plant_id(plants) -> str | None:
    """The portal's own plant id (e.g. '10950561') from getPlantListTitle."""
    for p in plants or []:
        if isinstance(p, dict) and p.get("id") is not None:
            return str(p["id"])
    return None


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="solaranalysis.strings")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=1)
    return ap


def main(argv=None, today=None) -> int:
    args = _build_parser().parse_args(argv)

    paths = Paths.create(args.data_dir, args.app_dir)
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    db.init_db(conn)

    plant_id = growatt_plant_id(conn)
    if plant_id is None:
        print("no enabled Growatt plant configured in app.db")
        conn.close()
        return 2

    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    days = resolve_days(args.date, args.backfill, today or date.today())

    adapter = GrowattAdapter(auth, SessionStore(paths.session_cache_dir))
    adapter.login()
    state = adapter._load_session()
    with BrowserSession(storage_state=state) as bs:
        adapter._authenticate(bs, had_state=bool(state))
        adapter._save_session(bs)

        source_plant_id = first_plant_id(history_client.get_plant_list(bs))
        if source_plant_id is None:
            print("no Growatt plants on the account (plant list empty or unauthorized)")
            conn.close()
            return 3

        devices = mappers.parse_devices(
            history_client.get_device_list(bs, source_plant_id))
        if not devices:
            # An empty device list is almost always a failed/unauthorized
            # fetch, not a genuinely deviceless plant -- don't exit 0 silently.
            print(f"no devices found on plant {source_plant_id} "
                  "(device list empty or unauthorized)")
            conn.close()
            return 3

        now = now_utc()
        all_results = []
        for inv in devices:
            print(f"inverter {inv.serial} ({inv.model}) — {len(days)} day(s)")
            all_results.append(
                (inv, collector.collect(conn, inv, days, now, bs=bs)))

    for inv, results in all_results:
        for r in results:
            if "error" in r:
                print(f"{inv.serial} {r['day']}: ERROR {r['error']}")
            elif r.get("empty"):
                print(f"{inv.serial} {r['day']}: no data (pre-install or out of range)")
            else:
                flag = " PARTIAL" if r.get("partial") else ""
                print(f"{inv.serial} {r['day']}: {r['channels']} channels, "
                      f"{r['energy_rows']} energy rows, {r['samples']} samples, "
                      f"{r['pages_ok']}/{collector.PAGES_PER_DAY} pages{flag}")
    conn.close()
    return 0
```

Create `solaranalysis/strings/__main__.py`:

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/strings/test_cli.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/strings/cli.py solaranalysis/strings/__main__.py tests/strings/test_cli.py
git commit -m "feat: Growatt string collector CLI entry point"
```

---

### Task 11: Live backfill run and the C2 calibration handoff

This is the task that makes Phase C2 possible: it puts real data in the DB and
writes down what that data actually says, so C2's thresholds are calibrated
rather than guessed.

**Files:**
- Create: `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md` (the observed-baseline notes C2 consumes)
- Modify: `README.md` (the module map around line 179–185, and the "Data captured" section around line 138)
- Modify: `NextTODO.md`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces: populated `inverter_channels` / `channel_day_energy` / `channel_samples` / `inverter_samples` tables, plus written baseline statistics.

- [ ] **Step 1: Confirm the Growatt plant exists in `app.db`**

The CLI reads credentials from `app.db`, not `config.yaml`. Check the web app
has an enabled Growatt plant:

```bash
.venv/Scripts/python.exe -c "from solaranalysis.web import db; c=db.connect('data/app.db'); print([(r['id'],r['name'],r['platform'],r['enabled']) for r in c.execute('SELECT id,name,platform,enabled FROM plants')])"
```

Expected: a row with `platform='growatt'` and `enabled=1`. If it is missing, add
it through the web UI's Plants page first — exit code 2 means exactly this.

- [ ] **Step 2: Dry-run a single day**

```bash
.venv/Scripts/python.exe -m solaranalysis.strings --data-dir data --app-dir . --backfill 1
```

Expected: exit 0, and a line like
`MZHRF6K002 2026-07-25: 18 channels, 6 energy rows, 5184 samples, 4/4 pages`.
`18 channels` and `4/4 pages` are the two numbers that confirm the whole chain
works. If you see `no data`, the target day predates the inverter's 2026-07-10
install — pass `--date` with a day inside the window.

- [ ] **Step 3: Backfill everything the portal will give**

```bash
.venv/Scripts/python.exe -m solaranalysis.strings --data-dir data --app-dir . --backfill 90
```

Expected: ~17 days of real rows and ~73 `no data` lines for pre-install days,
exit 0. Pre-install days are normal — they must not print `ERROR`.

- [ ] **Step 4: Verify what landed**

```bash
.venv/Scripts/python.exe -c "from solaranalysis.web import db; c=db.connect('data/app.db'); print('channels', [(r['channel_kind'],r['channel_no'],r['lifetime_kwh']) for r in c.execute('SELECT * FROM inverter_channels ORDER BY channel_kind, channel_no')]); print('days', c.execute('SELECT COUNT(DISTINCT day) FROM channel_day_energy').fetchone()[0]); print('samples', c.execute('SELECT COUNT(*) FROM channel_samples').fetchone()[0]); print('flags', [dict(r) for r in c.execute(\"SELECT DISTINCT str_break, str_unbalance, str_unmatch, warn_code, fault_code1 FROM inverter_samples\")])"
```

Expected: 6 `mppt` + 12 `string` channels; ~17 distinct days; roughly
17 × 5,184 ≈ 88,000 channel samples; and a single all-`"0"` flag combination
(no faults in the window).

- [ ] **Step 5: Write the observed baseline for C2**

Compute the statistics C2's thresholds will be set against and record them.

```bash
.venv/Scripts/python.exe -c "import statistics as st; from solaranalysis.web import db; c=db.connect('data/app.db'); rows=[dict(r) for r in c.execute('SELECT channel_no, day, energy_kwh, share_of_total FROM channel_day_energy ORDER BY channel_no, day')]; per={}; [per.setdefault(r['channel_no'],[]).append(r) for r in rows]; [print(f\"PV{n}: days={len(v)} energy {min(x['energy_kwh'] for x in v):.1f}-{max(x['energy_kwh'] for x in v):.1f} kWh  share median={st.median(x['share_of_total'] for x in v if x['share_of_total'] is not None):.4f} min={min((x['share_of_total'] for x in v if x['share_of_total'] is not None)):.4f} max={max((x['share_of_total'] for x in v if x['share_of_total'] is not None)):.4f}\") for n,v in sorted(per.items())]"
```

Create `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md` recording,
for each of the 6 live inputs: day count, energy range, and the median / min /
max `share_of_total`. Also note the widest single-day swing in any channel's
share, and the observed spread of `pv_iso_kohm` and peak `temp3_c`.

**Why this matters for C2:** the share-of-total spread observed here *is* the
noise floor. A "share collapse" threshold must sit outside it, or the rule will
fire on ordinary weather. Record the numbers rather than eyeballing them.

- [ ] **Step 6: Update `README.md`**

The "Project structure" tree currently ends at line 185 with
`└── prompts/system.txt`, and has **no `optimizers/` entry** — it was never added
when Phase B1 landed. Replace these two lines:

```
│   └── sma.py             # SMA Sunny Portal (table read)
└── prompts/system.txt     # Grounding contract for Claude
```

with:

```
│   └── sma.py             # SMA Sunny Portal (table read)
├── optimizers/            # SolarEdge per-optimizer collector + anomaly report
├── strings/               # Growatt per-string collector (MPPT + string level)
│   ├── history_client.py  # authenticated POSTs to /device/getMAXHistory
│   ├── mappers.py         # pure: 5-minute samples -> channel/energy records
│   ├── store.py           # schema v7 tables in app.db
│   ├── collector.py       # per-day page walk with failure isolation
│   └── cli.py             # python -m solaranalysis.strings
└── prompts/system.txt     # Grounding contract for Claude
```

In the "Data captured" list (around line 138), extend the **Growatt** bullet:

```markdown
- **Growatt** — energy today & lifetime (kWh), lifetime revenue, CO₂, trees,
  inverter status (decoded best-effort), plus monthly/yearly energy and current
  power derived from the per-device rows. A separate per-string collector
  (`python -m solaranalysis.strings`) stores per-MPPT-input daily energy and the
  full 5-minute per-channel series, including the inverter's own
  `StrBreak`/`StrUnblance`/`StrUnmatch` diagnostics.
```

While in that section, delete the now-stale claim under "Time series" that
"Growatt/SMA need their history endpoints wired" — Growatt's are wired.

- [ ] **Step 7: Update `NextTODO.md`**

Add a "Done this session" entry naming Phase C1, the four v7 tables, the 17-day
backfill result, and the baseline document. Add to **Next**:

- Phase C2 — analyze + report + email, thresholds calibrated from
  `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md`
- `DEPLOYMENT.md` §12 — the daily Growatt string collector Scheduled Task
- retention prune for `channel_samples` / `inverter_samples` (joining the
  existing `optimizer_energy` / `raw_payloads` prune item)

- [ ] **Step 8: Run the full suite once more**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add README.md NextTODO.md docs/superpowers/plans/2026-07-26-growatt-string-baseline.md
git commit -m "docs: Growatt string baseline from the 17-day backfill; C1 handoff"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| §2 endpoints, form-encoding, paging, day semantics | 2, 9 |
| §2 field families, unit trust, mixed types | 3–7 |
| §3(a) never-produced inputs excluded | 4, 5 |
| §3(b) exact-voltage string grouping, nullable `parent_no` | 4 |
| §3(c) share-of-total instead of peer median | 5 |
| §3(d) 17-day reality, empty days are normal | 9, 11 |
| §4 package layout, module responsibilities | 2–10 |
| §5 four v7 tables | 1 |
| §6 thresholds calibrated on real data | 11 (C2 consumes) |
| §7 report & email | **Phase C2 — not this plan** |
| §8 CLI, exit codes, failure isolation, backfill | 9, 10, 11 |
| §9 testing | every task |
| §10 max-counter energy, `producing_minutes`, serial-keyed rows | 5, 8 |
