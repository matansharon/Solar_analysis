# Solar Analysis (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log into SolarEdge and Growatt solar-monitoring portals with the owner's email/password, normalize each plant's data into one cross-vendor schema, and have Claude produce a single styled-HTML analysis report comparing the plants across production, health, financial, and anomalies.

**Architecture:** One `SolarPortalAdapter` interface with a different extraction strategy per platform (Growatt = `growattServer` lib, SolarEdge = official API or one-time browser cookie-harvest). Python owns ALL arithmetic/normalization/validation; Claude only synthesizes narrative from pre-computed numbers. Output is a self-contained HTML report.

**Tech Stack:** Python 3.10, `growattServer`, `anthropic`, `requests`, `playwright` (SolarEdge cookie-harvest fallback only in Phase 1), `python-dotenv`, `PyYAML`, `markdown`, `pytest`.

## Global Constraints

- Python interpreter is `python` (NOT `python3`), at `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`. Target Python 3.10.
- Read-only: never write settings to any inverter/portal. Fetch only.
- Secrets (credentials, `ANTHROPIC_API_KEY`) live ONLY in `.env` (git-ignored). Never hardcode, never log, never commit.
- Git repo root is `AI/` (parent of this project). Work on branch `feat/solar-analysis`. Commits carry NO AI attribution (per user's `clean-commits` setup).
- **Python does all math; Claude never computes.** Every figure in the report must be traceable to a Python-computed value.
- Default model `claude-sonnet-5`; `claude-opus-4-8` for `12mo`/`all` ranges. Verify current model IDs/pricing via the `claude-api` skill before implementing `core/analyze.py`.
- All energy normalized to **kWh**, all power to **kW** — convert field-by-field at ingest (SolarEdge = watts everywhere; Growatt mixes W power + kWh energy).
- Every schema field is nullable; a missing value carries a `data_source_status` (`ok`/`not_exposed`/`not_configured`/`comms_gap`) so absence is never read as zero.
- All work happens under `AI/solar-analysis/`. Run pytest from that directory.

---

### Task 1: Project scaffold, dependencies, and config loader

**Files:**
- Create: `solar-analysis/requirements.txt`
- Create: `solar-analysis/.env.example`
- Create: `solar-analysis/.gitignore`
- Create: `solar-analysis/config.example.yaml`
- Create: `solar-analysis/solaranalysis/__init__.py`
- Create: `solar-analysis/solaranalysis/config.py`
- Test: `solar-analysis/tests/test_config.py`

**Interfaces:**
- Produces:
  - `AuthConfig(platform: str, mode: str, username: str|None, password: str|None, api_key: str|None, token: str|None)` — `mode ∈ {"password","api_key","token"}`.
  - `PlantConfig(name: str, auth: AuthConfig, tariff_per_kwh: float|None, currency: str|None)`.
  - `AppConfig(plants: list[PlantConfig], model: str|None, max_input_tokens: int, output_language: str)`.
  - `load_config(config_path: str, env_path: str|None = None) -> AppConfig` — reads YAML, resolves `${ENV_VAR}` references from `.env`/environment.

- [ ] **Step 1: Create `requirements.txt`**

```
growattServer>=2.2.0
anthropic>=0.40.0
requests>=2.31
playwright>=1.44
python-dotenv>=1.0
PyYAML>=6.0
markdown>=3.6
pytest>=8.0
```

- [ ] **Step 2: Create `.env.example`**

```
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# SolarEdge (email/password path)
SOLAREDGE_USERNAME=nadav@elcam.co.il
SOLAREDGE_PASSWORD=

# Growatt (email/password path)
GROWATT_USERNAME=nadavs
GROWATT_PASSWORD=
```

- [ ] **Step 3: Create `.gitignore`**

```
.env
output/
__pycache__/
*.pyc
.session_cache/
```

- [ ] **Step 4: Create `config.example.yaml`**

```yaml
model: null            # null -> auto (sonnet-5, opus for 12mo/all)
max_input_tokens: 60000
output_language: en    # en | he
plants:
  - name: SolarEdge Roof
    auth:
      platform: solaredge
      mode: password
      username: ${SOLAREDGE_USERNAME}
      password: ${SOLAREDGE_PASSWORD}
    tariff_per_kwh: 0.55
    currency: ILS
  - name: Growatt Roof
    auth:
      platform: growatt
      mode: password
      username: ${GROWATT_USERNAME}
      password: ${GROWATT_PASSWORD}
    tariff_per_kwh: 0.55
    currency: ILS
```

- [ ] **Step 5: Write the failing test** — `tests/test_config.py`

```python
import os
from solaranalysis.config import load_config

def test_load_config_resolves_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_USER", "alice@example.com")
    monkeypatch.setenv("SE_PASS", "secret")
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "model: null\n"
        "max_input_tokens: 1000\n"
        "output_language: en\n"
        "plants:\n"
        "  - name: Roof\n"
        "    auth:\n"
        "      platform: solaredge\n"
        "      mode: password\n"
        "      username: ${SE_USER}\n"
        "      password: ${SE_PASS}\n"
        "    tariff_per_kwh: 0.5\n"
        "    currency: ILS\n",
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    assert len(cfg.plants) == 1
    p = cfg.plants[0]
    assert p.name == "Roof"
    assert p.auth.platform == "solaredge"
    assert p.auth.username == "alice@example.com"
    assert p.auth.password == "secret"
    assert p.tariff_per_kwh == 0.5
    assert cfg.max_input_tokens == 1000

def test_missing_env_raises(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "plants:\n  - name: X\n    auth:\n      platform: growatt\n"
        "      mode: password\n      username: ${NOPE_MISSING}\n      password: x\n",
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(ValueError, match="NOPE_MISSING"):
        load_config(str(cfg_file))
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: solaranalysis.config`)

- [ ] **Step 7: Implement `solaranalysis/config.py`**

```python
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from dotenv import load_dotenv
import yaml

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")

@dataclass
class AuthConfig:
    platform: str
    mode: str = "password"
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    token: str | None = None

@dataclass
class PlantConfig:
    name: str
    auth: AuthConfig
    tariff_per_kwh: float | None = None
    currency: str | None = None

@dataclass
class AppConfig:
    plants: list[PlantConfig] = field(default_factory=list)
    model: str | None = None
    max_input_tokens: int = 60000
    output_language: str = "en"

def _resolve(value):
    if not isinstance(value, str):
        return value
    def repl(m):
        name = m.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(f"Environment variable {name} is not set (referenced in config)")
        return val
    return _ENV_REF.sub(repl, value)

def load_config(config_path: str, env_path: str | None = None) -> AppConfig:
    load_dotenv(env_path)  # loads .env from cwd if env_path is None
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    plants = []
    for p in raw.get("plants", []):
        a = p["auth"]
        auth = AuthConfig(
            platform=a["platform"],
            mode=a.get("mode", "password"),
            username=_resolve(a.get("username")),
            password=_resolve(a.get("password")),
            api_key=_resolve(a.get("api_key")),
            token=_resolve(a.get("token")),
        )
        plants.append(PlantConfig(
            name=p["name"], auth=auth,
            tariff_per_kwh=p.get("tariff_per_kwh"),
            currency=p.get("currency"),
        ))
    return AppConfig(
        plants=plants,
        model=raw.get("model"),
        max_input_tokens=raw.get("max_input_tokens", 60000),
        output_language=raw.get("output_language", "en"),
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add solar-analysis/requirements.txt solar-analysis/.env.example solar-analysis/.gitignore solar-analysis/config.example.yaml solar-analysis/solaranalysis/__init__.py solar-analysis/solaranalysis/config.py solar-analysis/tests/test_config.py
git commit -m "feat(solar-analysis): project scaffold + config loader"
```

---

### Task 2: Normalized schema (`core/schema.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/__init__.py`
- Create: `solar-analysis/solaranalysis/core/schema.py`
- Test: `solar-analysis/tests/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TimeRange` enum: `SNAPSHOT="snapshot"`, `LAST_30D="30d"`, `LAST_12MO="12mo"`, `ALL="all"`.
  - `DeviceStatus` enum: `ONLINE, OFFLINE, STANDBY, FAULT, UNKNOWN`.
  - `AlertSeverity` enum: `INFO, WARNING, ERROR, CRITICAL`.
  - Dataclasses `EnergyPoint(timestamp_local: str, energy_kwh: float|None, granularity: str)`, `PowerPoint(timestamp_local: str, power_kw: float|None)`, `Device`, `Alert`, `Metric(value: float|None, unit: str, is_derived: bool=False, data_source_status: str="ok")`, and `PlantData` aggregating all schema fields from spec §6.
  - `PlantData.to_dict() -> dict` (JSON-serializable).

- [ ] **Step 1: Write the failing test** — `tests/test_schema.py`

```python
from solaranalysis.core.schema import (
    PlantData, Device, Alert, Metric, EnergyPoint, PowerPoint,
    TimeRange, DeviceStatus, AlertSeverity,
)

def test_plantdata_minimal_construct_and_serialize():
    pd = PlantData(
        plant_id="se-1",
        source_platform="solaredge",
        source_plant_id="123",
        plant_name="Roof",
        peak_power_kwp=Metric(100.0, "kWp"),
        currency="ILS",
    )
    d = pd.to_dict()
    assert d["plant_id"] == "se-1"
    assert d["peak_power_kwp"]["value"] == 100.0
    assert d["peak_power_kwp"]["data_source_status"] == "ok"
    assert d["devices"] == []
    assert d["alerts"] == []

def test_metric_missing_marks_status():
    m = Metric(None, "kWh", data_source_status="not_exposed")
    assert m.value is None
    assert m.data_source_status == "not_exposed"

def test_enums_have_expected_values():
    assert TimeRange.LAST_12MO.value == "12mo"
    assert DeviceStatus.FAULT.value == "fault"
    assert AlertSeverity.CRITICAL.value == "critical"

def test_device_and_alert_serialize():
    dev = Device(device_id="SN1", device_type="inverter", status=DeviceStatus.ONLINE,
                 current_power_kw=3.2)
    al = Alert(alert_id="a1", severity=AlertSeverity.WARNING, code="W01",
               message="grid", timestamp_local="2026-06-01T10:00:00", resolved=False)
    pd = PlantData(plant_id="g-1", source_platform="growatt", source_plant_id="9",
                   plant_name="G", devices=[dev], alerts=[al])
    d = pd.to_dict()
    assert d["devices"][0]["status"] == "online"
    assert d["alerts"][0]["severity"] == "warning"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/schema.py`**

```python
from __future__ import annotations
import dataclasses
from dataclasses import dataclass, field
from enum import Enum

class TimeRange(str, Enum):
    SNAPSHOT = "snapshot"
    LAST_30D = "30d"
    LAST_12MO = "12mo"
    ALL = "all"

class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    STANDBY = "standby"
    FAULT = "fault"
    UNKNOWN = "unknown"

class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class Metric:
    value: float | None = None
    unit: str = ""
    is_derived: bool = False
    data_source_status: str = "ok"  # ok | not_exposed | not_configured | comms_gap

@dataclass
class EnergyPoint:
    timestamp_local: str
    energy_kwh: float | None
    granularity: str  # quarter_hour|hour|day|month|year

@dataclass
class PowerPoint:
    timestamp_local: str
    power_kw: float | None

@dataclass
class Device:
    device_id: str
    device_type: str = "inverter"
    model: str | None = None
    manufacturer: str | None = None
    status: DeviceStatus = DeviceStatus.UNKNOWN
    current_power_kw: float | None = None
    energy_lifetime_kwh: float | None = None
    temperature_c: float | None = None
    last_seen_local: str | None = None

@dataclass
class Alert:
    alert_id: str
    severity: AlertSeverity = AlertSeverity.INFO
    code: str | None = None
    message: str | None = None
    timestamp_local: str | None = None
    resolved: bool | None = None

@dataclass
class PlantData:
    # identity / metadata
    plant_id: str
    source_platform: str
    source_plant_id: str
    plant_name: str
    peak_power_kwp: Metric = field(default_factory=lambda: Metric(unit="kWp"))
    location_address: str | None = None
    location_country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    install_date: str | None = None
    currency: str | None = None
    reporting_timestamp_utc: str | None = None
    # production
    energy_today_kwh: Metric = field(default_factory=lambda: Metric(unit="kWh"))
    energy_month_kwh: Metric = field(default_factory=lambda: Metric(unit="kWh"))
    energy_year_kwh: Metric = field(default_factory=lambda: Metric(unit="kWh"))
    energy_lifetime_kwh: Metric = field(default_factory=lambda: Metric(unit="kWh"))
    current_power_kw: Metric = field(default_factory=lambda: Metric(unit="kW"))
    energy_timeseries: list[EnergyPoint] = field(default_factory=list)
    power_timeseries: list[PowerPoint] = field(default_factory=list)
    # derived KPIs
    specific_yield_kwh_per_kwp: Metric = field(default_factory=lambda: Metric(unit="kWh/kWp", is_derived=True))
    performance_ratio: Metric = field(default_factory=lambda: Metric(unit="ratio", is_derived=True))
    uptime_pct: Metric = field(default_factory=lambda: Metric(unit="%", is_derived=True))
    # inventory & events
    devices: list[Device] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    # financial / environmental
    revenue: Metric = field(default_factory=lambda: Metric(unit="currency"))
    savings: Metric = field(default_factory=lambda: Metric(unit="currency", is_derived=True))
    co2_avoided_kg: Metric = field(default_factory=lambda: Metric(unit="kg"))
    trees_equivalent: Metric = field(default_factory=lambda: Metric(unit="count"))
    # pipeline metadata
    data_quality_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def convert(o):
            if isinstance(o, Enum):
                return o.value
            if isinstance(o, list):
                return [convert(x) for x in o]
            if dataclasses.is_dataclass(o):
                return {f.name: convert(getattr(o, f.name)) for f in dataclasses.fields(o)}
            return o
        return convert(self)
```

Note: a single recursive walk that coerces every `Enum` to its `.value` at any depth and recurses nested dataclasses via `dataclasses.fields` — no `asdict`, so nested enums in lists (e.g. `Device.status`) are always coerced. Result is JSON-serializable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (4 passed). The test asserts nested enum coercion (`devices[0].status == "online"`), which the recursive `to_dict` walk handles directly.

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/__init__.py solar-analysis/solaranalysis/core/schema.py solar-analysis/tests/test_schema.py
git commit -m "feat(solar-analysis): normalized cross-vendor PlantData schema"
```

---

### Task 3: Unit conversion + derived metrics (`core/units.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/units.py`
- Test: `solar-analysis/tests/test_units.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all return `float|None`, propagating `None`):
  - `w_to_kw(watts) -> float|None`, `wh_to_kwh(wh) -> float|None`
  - `specific_yield(energy_kwh, kwp) -> float|None` (energy / kwp; None if kwp falsy)
  - `capacity_factor(energy_kwh, kwp, hours) -> float|None` (energy / (kwp*hours))
  - `money(energy_kwh, tariff_per_kwh) -> float|None`
  - `round_opt(x, ndigits=2) -> float|None`

- [ ] **Step 1: Write the failing test** — `tests/test_units.py`

```python
import math
from solaranalysis.core import units

def test_w_to_kw():
    assert units.w_to_kw(3200) == 3.2
    assert units.w_to_kw(None) is None

def test_wh_to_kwh():
    assert units.wh_to_kwh(1500) == 1.5
    assert units.wh_to_kwh(None) is None

def test_specific_yield():
    assert units.specific_yield(1200.0, 100.0) == 12.0
    assert units.specific_yield(1200.0, 0) is None
    assert units.specific_yield(None, 100.0) is None

def test_capacity_factor():
    cf = units.capacity_factor(240.0, 100.0, 24.0)
    assert math.isclose(cf, 0.1)
    assert units.capacity_factor(1.0, 0, 24.0) is None

def test_money():
    assert units.money(100.0, 0.55) == 55.0
    assert units.money(100.0, None) is None

def test_round_opt():
    assert units.round_opt(1.23456) == 1.23
    assert units.round_opt(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_units.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/units.py`**

```python
from __future__ import annotations

def w_to_kw(watts: float | None) -> float | None:
    return None if watts is None else watts / 1000.0

def wh_to_kwh(wh: float | None) -> float | None:
    return None if wh is None else wh / 1000.0

def specific_yield(energy_kwh: float | None, kwp: float | None) -> float | None:
    if energy_kwh is None or not kwp:
        return None
    return energy_kwh / kwp

def capacity_factor(energy_kwh: float | None, kwp: float | None, hours: float | None) -> float | None:
    if energy_kwh is None or not kwp or not hours:
        return None
    return energy_kwh / (kwp * hours)

def money(energy_kwh: float | None, tariff_per_kwh: float | None) -> float | None:
    if energy_kwh is None or tariff_per_kwh is None:
        return None
    return energy_kwh * tariff_per_kwh

def round_opt(x: float | None, ndigits: int = 2) -> float | None:
    return None if x is None else round(x, ndigits)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_units.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/units.py solar-analysis/tests/test_units.py
git commit -m "feat(solar-analysis): unit-conversion + derived-metric helpers"
```

---

### Task 4: Sanity gates / data-quality flags (`core/validate.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/validate.py`
- Test: `solar-analysis/tests/test_validate.py`

**Interfaces:**
- Consumes: `PlantData`, `Metric` from `core/schema.py`.
- Produces: `validate_plant(pd: PlantData) -> PlantData` — mutates and returns `pd` with `data_quality_flags` appended. Rules: negative energy anywhere → flag; night-time power (current_power_kw>0 while energy_today_kwh==0 is allowed, but negative power flagged); performance_ratio outside [0,1] → flag; energy_lifetime < energy_year (non-monotonic) → flag. Flags are human-readable strings prefixed with the field name.

- [ ] **Step 1: Write the failing test** — `tests/test_validate.py`

```python
from solaranalysis.core.schema import PlantData, Metric
from solaranalysis.core.validate import validate_plant

def _plant(**kw):
    base = dict(plant_id="p", source_platform="growatt", source_plant_id="1", plant_name="P")
    base.update(kw)
    return PlantData(**base)

def test_negative_energy_flagged():
    pd = _plant(energy_today_kwh=Metric(-5.0, "kWh"))
    validate_plant(pd)
    assert any("energy_today_kwh" in f and "negative" in f for f in pd.data_quality_flags)

def test_pr_out_of_range_flagged():
    pd = _plant(performance_ratio=Metric(1.4, "ratio", is_derived=True))
    validate_plant(pd)
    assert any("performance_ratio" in f for f in pd.data_quality_flags)

def test_non_monotonic_lifetime_flagged():
    pd = _plant(energy_year_kwh=Metric(100.0, "kWh"),
                energy_lifetime_kwh=Metric(50.0, "kWh"))
    validate_plant(pd)
    assert any("lifetime" in f for f in pd.data_quality_flags)

def test_clean_plant_no_flags():
    pd = _plant(energy_today_kwh=Metric(10.0, "kWh"),
                energy_year_kwh=Metric(500.0, "kWh"),
                energy_lifetime_kwh=Metric(9000.0, "kWh"),
                performance_ratio=Metric(0.83, "ratio", is_derived=True))
    validate_plant(pd)
    assert pd.data_quality_flags == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_validate.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/validate.py`**

```python
from __future__ import annotations
from .schema import PlantData

_ENERGY_FIELDS = [
    "energy_today_kwh", "energy_month_kwh", "energy_year_kwh", "energy_lifetime_kwh",
]

def validate_plant(pd: PlantData) -> PlantData:
    flags = pd.data_quality_flags
    for name in _ENERGY_FIELDS:
        m = getattr(pd, name)
        if m.value is not None and m.value < 0:
            flags.append(f"{name}: negative energy value ({m.value})")
    if pd.current_power_kw.value is not None and pd.current_power_kw.value < 0:
        flags.append(f"current_power_kw: negative power value ({pd.current_power_kw.value})")
    pr = pd.performance_ratio.value
    if pr is not None and not (0.0 <= pr <= 1.0):
        flags.append(f"performance_ratio: out of range [0,1] ({pr})")
    yr = pd.energy_year_kwh.value
    life = pd.energy_lifetime_kwh.value
    if yr is not None and life is not None and life < yr:
        flags.append(f"energy_lifetime_kwh ({life}) < energy_year_kwh ({yr}): non-monotonic")
    for ep in pd.energy_timeseries:
        if ep.energy_kwh is not None and ep.energy_kwh < 0:
            flags.append(f"energy_timeseries@{ep.timestamp_local}: negative energy")
            break
    return pd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_validate.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/validate.py solar-analysis/tests/test_validate.py
git commit -m "feat(solar-analysis): data-quality sanity gates"
```

---

### Task 5: Rollups by time range (`core/rollup.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/rollup.py`
- Test: `solar-analysis/tests/test_rollup.py`

**Interfaces:**
- Consumes: `PlantData`, `EnergyPoint`, `TimeRange` from schema.
- Produces:
  - `rollup_energy(points: list[EnergyPoint], target_granularity: str) -> list[EnergyPoint]` — sums daily points into monthly (or monthly into yearly) buckets keyed by `YYYY-MM` / `YYYY`. `timestamp_local` of a bucket is the period key.
  - `worst_periods(points: list[EnergyPoint], n: int) -> list[EnergyPoint]` — the `n` lowest-energy non-null points, ascending.
  - `plan_rollup(pd: PlantData, time_range: TimeRange) -> dict` — returns `{"granularity": str, "series": list[EnergyPoint], "worst": list[EnergyPoint]}` per the range policy (snapshot→[], 30d→daily, 12mo→monthly+worst5, all→monthly+worst5).

- [ ] **Step 1: Write the failing test** — `tests/test_rollup.py`

```python
from solaranalysis.core.schema import EnergyPoint, PlantData, TimeRange
from solaranalysis.core.rollup import rollup_energy, worst_periods, plan_rollup

def _days():
    return [
        EnergyPoint("2025-01-01", 10.0, "day"),
        EnergyPoint("2025-01-02", 20.0, "day"),
        EnergyPoint("2025-02-01", 5.0, "day"),
        EnergyPoint("2025-02-03", 7.0, "day"),
    ]

def test_rollup_daily_to_monthly():
    out = rollup_energy(_days(), "month")
    by = {p.timestamp_local: p.energy_kwh for p in out}
    assert by["2025-01"] == 30.0
    assert by["2025-02"] == 12.0
    assert all(p.granularity == "month" for p in out)

def test_worst_periods():
    w = worst_periods(_days(), 2)
    assert [p.energy_kwh for p in w] == [5.0, 7.0]

def test_plan_rollup_12mo_uses_monthly_and_worst():
    pd = PlantData(plant_id="p", source_platform="growatt", source_plant_id="1",
                   plant_name="P", energy_timeseries=_days())
    res = plan_rollup(pd, TimeRange.LAST_12MO)
    assert res["granularity"] == "month"
    assert len(res["series"]) == 2
    assert len(res["worst"]) == 4  # only 4 days available; min(n, len)

def test_plan_rollup_snapshot_empty_series():
    pd = PlantData(plant_id="p", source_platform="growatt", source_plant_id="1",
                   plant_name="P", energy_timeseries=_days())
    res = plan_rollup(pd, TimeRange.SNAPSHOT)
    assert res["series"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rollup.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/rollup.py`**

```python
from __future__ import annotations
from collections import defaultdict
from .schema import EnergyPoint, PlantData, TimeRange

def rollup_energy(points: list[EnergyPoint], target_granularity: str) -> list[EnergyPoint]:
    key_len = 7 if target_granularity == "month" else 4  # YYYY-MM or YYYY
    buckets: dict[str, float] = defaultdict(float)
    seen_null: dict[str, bool] = defaultdict(bool)
    for p in points:
        key = p.timestamp_local[:key_len]
        if p.energy_kwh is None:
            seen_null[key] = True
            continue
        buckets[key] += p.energy_kwh
    out = []
    for key in sorted(buckets):
        out.append(EnergyPoint(key, round(buckets[key], 3), target_granularity))
    return out

def worst_periods(points: list[EnergyPoint], n: int) -> list[EnergyPoint]:
    valid = [p for p in points if p.energy_kwh is not None]
    return sorted(valid, key=lambda p: p.energy_kwh)[:n]

def plan_rollup(pd: PlantData, time_range: TimeRange) -> dict:
    pts = pd.energy_timeseries
    if time_range == TimeRange.SNAPSHOT:
        return {"granularity": "none", "series": [], "worst": []}
    if time_range == TimeRange.LAST_30D:
        return {"granularity": "day", "series": pts, "worst": worst_periods(pts, 5)}
    # 12mo and all -> monthly + worst 5
    monthly = rollup_energy(pts, "month")
    return {"granularity": "month", "series": monthly, "worst": worst_periods(pts, 5)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rollup.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/rollup.py solar-analysis/tests/test_rollup.py
git commit -m "feat(solar-analysis): time-range rollups + worst-period appendix"
```

---

### Task 6: Session store + rate-limit guard (`core/session_store.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/session_store.py`
- Test: `solar-analysis/tests/test_session_store.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `SessionStore(cache_dir: str, now_fn=time.time)` with:
    - `save(platform: str, data: dict, ttl_seconds: int) -> None` — writes JSON `{expires_at, data}` to `<cache_dir>/<platform>.json`.
    - `load(platform: str) -> dict|None` — returns `data` if not expired, else `None`.
    - `can_poll(platform: str, min_interval_s: int) -> bool` — True if `min_interval_s` elapsed since last `mark_poll`.
    - `mark_poll(platform: str) -> None`.
- `now_fn` is injected so tests use a fake clock (no real sleeping; Global Constraints forbid nothing here but the test must be deterministic).

- [ ] **Step 1: Write the failing test** — `tests/test_session_store.py`

```python
from solaranalysis.core.session_store import SessionStore

class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t

def test_save_load_roundtrip(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    s.save("growatt", {"cookie": "abc"}, ttl_seconds=100)
    assert s.load("growatt") == {"cookie": "abc"}

def test_load_expired_returns_none(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    s.save("solaredge", {"c": 1}, ttl_seconds=10)
    clk.t += 11
    assert s.load("solaredge") is None

def test_missing_returns_none(tmp_path):
    s = SessionStore(str(tmp_path), now_fn=Clock())
    assert s.load("nope") is None

def test_poll_guard(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    assert s.can_poll("growatt", 300) is True   # never polled
    s.mark_poll("growatt")
    assert s.can_poll("growatt", 300) is False
    clk.t += 301
    assert s.can_poll("growatt", 300) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_store.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/session_store.py`**

```python
from __future__ import annotations
import json
import os
import time

class SessionStore:
    def __init__(self, cache_dir: str, now_fn=time.time):
        self.cache_dir = cache_dir
        self.now_fn = now_fn
        os.makedirs(cache_dir, exist_ok=True)
        self._last_poll: dict[str, float] = {}

    def _path(self, platform: str) -> str:
        return os.path.join(self.cache_dir, f"{platform}.json")

    def save(self, platform: str, data: dict, ttl_seconds: int) -> None:
        payload = {"expires_at": self.now_fn() + ttl_seconds, "data": data}
        with open(self._path(platform), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def load(self, platform: str) -> dict | None:
        path = self._path(platform)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if payload.get("expires_at", 0) <= self.now_fn():
            return None
        return payload.get("data")

    def can_poll(self, platform: str, min_interval_s: int) -> bool:
        last = self._last_poll.get(platform)
        return last is None or (self.now_fn() - last) >= min_interval_s

    def mark_poll(self, platform: str) -> None:
        self._last_poll[platform] = self.now_fn()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/session_store.py solar-analysis/tests/test_session_store.py
git commit -m "feat(solar-analysis): session cache + poll guard"
```

---

### Task 7: Adapter base interface (`adapters/base.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/adapters/__init__.py`
- Create: `solar-analysis/solaranalysis/adapters/base.py`
- Test: `solar-analysis/tests/test_adapter_base.py`

**Interfaces:**
- Consumes: `AuthConfig` (config.py), `SessionStore` (core/session_store.py), `PlantData`, `TimeRange` (core/schema.py).
- Produces:
  - `SolarPortalAdapter(ABC)` with `platform: str` class attr, `__init__(self, auth: AuthConfig, session_store: SessionStore)`, abstract `login() -> None`, abstract `fetch(time_range: TimeRange) -> list[PlantData]`.
  - `AdapterError(Exception)` base for adapter failures.
  - `get_adapter(auth: AuthConfig, session_store: SessionStore) -> SolarPortalAdapter` factory dispatching on `auth.platform`.

- [ ] **Step 1: Write the failing test** — `tests/test_adapter_base.py`

```python
import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import SolarPortalAdapter, get_adapter, AdapterError

def test_cannot_instantiate_abc(tmp_path):
    with pytest.raises(TypeError):
        SolarPortalAdapter(AuthConfig("x"), SessionStore(str(tmp_path)))

def test_factory_dispatch(tmp_path):
    ss = SessionStore(str(tmp_path))
    se = get_adapter(AuthConfig("solaredge", username="u", password="p"), ss)
    gw = get_adapter(AuthConfig("growatt", username="u", password="p"), ss)
    assert se.platform == "solaredge"
    assert gw.platform == "growatt"

def test_factory_unknown_raises(tmp_path):
    with pytest.raises(AdapterError, match="unknown"):
        get_adapter(AuthConfig("nope"), SessionStore(str(tmp_path)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_adapter_base.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/adapters/base.py`**

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from ..config import AuthConfig
from ..core.session_store import SessionStore
from ..core.schema import PlantData, TimeRange

class AdapterError(Exception):
    pass

class SolarPortalAdapter(ABC):
    platform: str = ""

    def __init__(self, auth: AuthConfig, session_store: SessionStore):
        self.auth = auth
        self.sessions = session_store

    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def fetch(self, time_range: TimeRange) -> list[PlantData]: ...

def get_adapter(auth: AuthConfig, session_store: SessionStore) -> SolarPortalAdapter:
    # imported here to avoid circular imports at module load
    from .solaredge import SolarEdgeAdapter
    from .growatt import GrowattAdapter
    registry = {"solaredge": SolarEdgeAdapter, "growatt": GrowattAdapter}
    cls = registry.get(auth.platform)
    if cls is None:
        raise AdapterError(f"unknown platform: {auth.platform!r}")
    return cls(auth, session_store)
```

Note: `get_adapter` imports the concrete adapters, so Tasks 8 and 9 must exist before this test passes. To keep TDD green now, create empty stub modules first:

- [ ] **Step 3b: Create stub adapter modules so the factory imports resolve**

`solaranalysis/adapters/growatt.py`:
```python
from .base import SolarPortalAdapter
from ..core.schema import PlantData, TimeRange
class GrowattAdapter(SolarPortalAdapter):
    platform = "growatt"
    def login(self) -> None: ...
    def fetch(self, time_range: TimeRange) -> list[PlantData]: return []
```

`solaranalysis/adapters/solaredge.py`:
```python
from .base import SolarPortalAdapter
from ..core.schema import PlantData, TimeRange
class SolarEdgeAdapter(SolarPortalAdapter):
    platform = "solaredge"
    def login(self) -> None: ...
    def fetch(self, time_range: TimeRange) -> list[PlantData]: return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_adapter_base.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/adapters/
git add solar-analysis/tests/test_adapter_base.py
git commit -m "feat(solar-analysis): adapter interface + factory (with stubs)"
```

---

### Task 8: Growatt adapter (`adapters/growatt.py`)

**Files:**
- Modify: `solar-analysis/solaranalysis/adapters/growatt.py` (replace stub)
- Create: `solar-analysis/tests/fixtures/growatt_plant.json`
- Test: `solar-analysis/tests/test_growatt_adapter.py`

**Interfaces:**
- Consumes: `SolarPortalAdapter`, `AuthConfig`, `SessionStore`, schema types, `core.units`.
- Produces: `GrowattAdapter` with a seam for injection: `__init__(self, auth, session_store, client=None)` where `client` is a `growattServer.GrowattApi`-like object (injected in tests). A pure mapping function `map_growatt_plant(plant_meta: dict, energy: dict, devices: list[dict], platform_status) -> PlantData` that tests call directly with fixture dicts.

**Growatt field facts (from research — map these exactly):**
- Energy `eToday`/`eMonth`/`eTotal` are already **kWh** (no conversion). `eTotal` is **lifetime**, not year → year derived elsewhere.
- Power `pac`/`currentPower`/`nominalPower` are **watts** → `w_to_kw`. `nominalPower/1000` = kWp.
- Device status integer: `1→online, 0/-1→offline, 2→standby, 3→fault` else `unknown`.
- Fault fields on device detail: `warnCode`/`warnText`, `faultCode`/`faultText`.
- `lat`/`lon` often empty; `co2` reduction may be absent.

- [ ] **Step 1: Create the fixture** — `tests/fixtures/growatt_plant.json`

```json
{
  "plant_meta": {"id": "9001", "plantName": "Growatt Roof", "nominalPower": "100000",
                 "city": "Karmiel", "country": "Israel", "lat": "", "lon": "",
                 "createDate": "2022-05-01", "currency": "ILS"},
  "energy": {"eToday": "42.5", "eMonth": "980.0", "eTotal": "125000.0",
             "currentPower": "63500"},
  "devices": [
    {"deviceSn": "INV-A", "deviceModel": "MIN 5000", "status": 1, "pac": "5000",
     "eTotal": "60000", "temperature": "41.2", "lastUpdateTime": "2026-07-01 12:00:00",
     "warnCode": "0", "warnText": ""},
    {"deviceSn": "INV-B", "deviceModel": "MIN 5000", "status": 3, "pac": "0",
     "eTotal": "58000", "temperature": "0", "lastUpdateTime": "2026-06-28 09:00:00",
     "warnCode": "203", "warnText": "PV isolation low"}
  ]
}
```

- [ ] **Step 2: Write the failing test** — `tests/test_growatt_adapter.py`

```python
import json
from pathlib import Path
from solaranalysis.adapters.growatt import map_growatt_plant
from solaranalysis.core.schema import DeviceStatus, AlertSeverity

FX = json.loads((Path(__file__).parent / "fixtures" / "growatt_plant.json").read_text(encoding="utf-8"))

def test_map_basic_metadata_and_units():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.source_platform == "growatt"
    assert pd.plant_name == "Growatt Roof"
    assert pd.peak_power_kwp.value == 100.0            # 100000 W -> kWp
    assert pd.energy_today_kwh.value == 42.5           # already kWh
    assert pd.energy_lifetime_kwh.value == 125000.0
    assert pd.current_power_kw.value == 63.5           # 63500 W -> kW

def test_map_year_is_not_exposed():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.energy_year_kwh.value is None
    assert pd.energy_year_kwh.data_source_status == "not_exposed"

def test_device_status_and_fault_alert():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    statuses = {d.device_id: d.status for d in pd.devices}
    assert statuses["INV-A"] == DeviceStatus.ONLINE
    assert statuses["INV-B"] == DeviceStatus.FAULT
    # a non-zero warnCode becomes an alert
    codes = {a.code for a in pd.alerts}
    assert "203" in codes
    assert any(a.severity in (AlertSeverity.ERROR, AlertSeverity.WARNING) for a in pd.alerts)

def test_empty_latlon_becomes_none():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.latitude is None and pd.longitude is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_growatt_adapter.py -v`
Expected: FAIL (`ImportError: cannot import name 'map_growatt_plant'`)

- [ ] **Step 4: Implement `solaranalysis/adapters/growatt.py`**

```python
from __future__ import annotations
from ..core.schema import (
    PlantData, Metric, Device, Alert, DeviceStatus, AlertSeverity, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError

_STATUS_MAP = {1: DeviceStatus.ONLINE, 0: DeviceStatus.OFFLINE, -1: DeviceStatus.OFFLINE,
               2: DeviceStatus.STANDBY, 3: DeviceStatus.FAULT}

def _f(x):
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None

def map_growatt_plant(plant_meta: dict, energy: dict, devices: list[dict]) -> PlantData:
    kwp = units.w_to_kw(_f(plant_meta.get("nominalPower")))
    pd = PlantData(
        plant_id=f"growatt-{plant_meta.get('id')}",
        source_platform="growatt",
        source_plant_id=str(plant_meta.get("id")),
        plant_name=plant_meta.get("plantName") or "Growatt plant",
        peak_power_kwp=Metric(kwp, "kWp"),
        location_country=plant_meta.get("country"),
        latitude=_f(plant_meta.get("lat")),
        longitude=_f(plant_meta.get("lon")),
        install_date=plant_meta.get("createDate") or None,
        currency=plant_meta.get("currency"),
    )
    pd.energy_today_kwh = Metric(_f(energy.get("eToday")), "kWh")       # already kWh
    pd.energy_month_kwh = Metric(_f(energy.get("eMonth")), "kWh")
    pd.energy_lifetime_kwh = Metric(_f(energy.get("eTotal")), "kWh")
    # Growatt has no calendar-year total; eTotal is lifetime.
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(units.w_to_kw(_f(energy.get("currentPower"))), "kW")
    for d in devices:
        status_int = d.get("status")
        try:
            status = _STATUS_MAP.get(int(status_int), DeviceStatus.UNKNOWN)
        except (TypeError, ValueError):
            status = DeviceStatus.UNKNOWN
        pd.devices.append(Device(
            device_id=str(d.get("deviceSn")),
            device_type="inverter",
            model=d.get("deviceModel"),
            manufacturer="Growatt",
            status=status,
            current_power_kw=units.w_to_kw(_f(d.get("pac"))),
            energy_lifetime_kwh=_f(d.get("eTotal")),  # per-device eTotal is kWh
            temperature_c=_f(d.get("temperature")),
            last_seen_local=d.get("lastUpdateTime"),
        ))
        warn = str(d.get("warnCode") or "0").strip()
        if warn not in ("", "0"):
            sev = AlertSeverity.ERROR if status == DeviceStatus.FAULT else AlertSeverity.WARNING
            pd.alerts.append(Alert(
                alert_id=f"{d.get('deviceSn')}-{warn}",
                severity=sev, code=warn,
                message=d.get("warnText") or d.get("faultText"),
                timestamp_local=d.get("lastUpdateTime"),
                resolved=False,
            ))
    pd.co2_avoided_kg = Metric(_f(energy.get("co2")), "kg",
                               data_source_status="ok" if energy.get("co2") else "not_exposed")
    return pd

class GrowattAdapter(SolarPortalAdapter):
    platform = "growatt"

    def __init__(self, auth, session_store, client=None):
        super().__init__(auth, session_store)
        self._client = client
        self._user_id = None

    def _ensure_client(self):
        if self._client is None:
            import growattServer  # imported lazily so tests don't need it
            self._client = growattServer.GrowattApi()
        return self._client

    def login(self) -> None:
        client = self._ensure_client()
        cached = self.sessions.load("growatt")
        if cached and cached.get("user_id"):
            self._user_id = cached["user_id"]
            return
        if not self.sessions.can_poll("growatt", 300):
            raise AdapterError("growatt: poll guard active (min 5 min between logins)")
        resp = client.login(self.auth.username, self.auth.password)
        self.sessions.mark_poll("growatt")
        if not resp or not resp.get("success"):
            raise AdapterError("growatt: login failed")
        self._user_id = resp["user"]["id"]
        self.sessions.save("growatt", {"user_id": self._user_id}, ttl_seconds=3600)

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        client = self._ensure_client()
        if self._user_id is None:
            self.login()
        plants = client.plant_list(self._user_id).get("data", [])
        results = []
        for p in plants:
            pid = p.get("plantId") or p.get("id")
            meta = client.plant_info(pid)
            energy = {
                "eToday": meta.get("eToday"), "eMonth": meta.get("eMonth"),
                "eTotal": meta.get("eTotal"), "currentPower": meta.get("currentPower"),
                "co2": meta.get("co2"),
            }
            plant_meta = {
                "id": pid, "plantName": p.get("plantName"),
                "nominalPower": meta.get("nominalPower"), "country": meta.get("country"),
                "city": meta.get("city"), "lat": meta.get("plant_lat"),
                "lon": meta.get("plant_lng"), "createDate": meta.get("createDate"),
                "currency": meta.get("currency"),
            }
            devices = client.device_list(pid).get("devices", []) if hasattr(client, "device_list") else []
            results.append(map_growatt_plant(plant_meta, energy, devices))
        return results
```

Note on `fetch`: the exact `plant_info`/`device_list` response keys from `growattServer` should be confirmed against the live library during a manual smoke test (research documented the method names but field keys vary by device family). The pure `map_growatt_plant` is what the tests lock down; `fetch` is glue verified by the manual smoke run in Task 13.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_growatt_adapter.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add solar-analysis/solaranalysis/adapters/growatt.py solar-analysis/tests/test_growatt_adapter.py solar-analysis/tests/fixtures/growatt_plant.json
git commit -m "feat(solar-analysis): Growatt adapter + mapping (fixture-tested)"
```

---

### Task 9: SolarEdge adapter (`adapters/solaredge.py`)

**Files:**
- Modify: `solar-analysis/solaranalysis/adapters/solaredge.py` (replace stub)
- Create: `solar-analysis/tests/fixtures/solaredge_overview.json`
- Create: `solar-analysis/tests/fixtures/solaredge_details.json`
- Create: `solar-analysis/tests/fixtures/solaredge_inventory.json`
- Test: `solar-analysis/tests/test_solaredge_adapter.py`

**Interfaces:**
- Consumes: schema types, `core.units`.
- Produces: `SolarEdgeAdapter` and pure mapping `map_solaredge_plant(details: dict, overview: dict, inventory: dict) -> PlantData`.

**SolarEdge field facts (from research — map these exactly):**
- ALL energy in **Wh**, ALL power in **W** → convert. `overview.lastDayData.energy`, `lastMonthData.energy`, `lastYearData.energy`, `lifeTimeData.energy` (Wh); `overview.currentPower.power` (W); `overview.revenue`.
- `details.peakPower` is already **kW** (→ store as kWp directly). `details.location.{country,city,address,timeZone}`, `details.currency`, `details.installationDate`.
- Official API exposes **no alerts and no CO2** → `alerts=[]`, `co2_avoided_kg` status `not_exposed`.
- `inventory.inverters[]`: `name`, `model`, `SN`, firmware; no per-inverter live status in inventory → device status `unknown` on official path.

- [ ] **Step 1: Create fixtures**

`tests/fixtures/solaredge_details.json`:
```json
{"details": {"id": 123, "name": "SolarEdge Roof", "peakPower": 90.0,
  "currency": "ILS", "installationDate": "2021-03-15",
  "location": {"country": "Israel", "city": "Karmiel", "address": "1 Sun St",
               "timeZone": "Asia/Jerusalem"}}}
```

`tests/fixtures/solaredge_overview.json`:
```json
{"overview": {"lastUpdateTime": "2026-07-01 12:00:00",
  "currentPower": {"power": 71000.0},
  "lastDayData": {"energy": 38000.0},
  "lastMonthData": {"energy": 910000.0},
  "lastYearData": {"energy": 9800000.0},
  "lifeTimeData": {"energy": 41000000.0},
  "measuredBy": "INVERTER", "revenue": 5390.0}}
```

`tests/fixtures/solaredge_inventory.json`:
```json
{"Inventory": {"inverters": [
  {"name": "Inverter 1", "model": "SE10K", "SN": "SE-INV-1", "cpuVersion": "4.1"},
  {"name": "Inverter 2", "model": "SE10K", "SN": "SE-INV-2", "cpuVersion": "4.1"}]}}
```

- [ ] **Step 2: Write the failing test** — `tests/test_solaredge_adapter.py`

```python
import json
from pathlib import Path
from solaranalysis.adapters.solaredge import map_solaredge_plant
from solaranalysis.core.schema import DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))

def test_maps_metadata_and_wh_conversion():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.source_platform == "solaredge"
    assert pd.plant_name == "SolarEdge Roof"
    assert pd.peak_power_kwp.value == 90.0             # already kW
    assert pd.timezone == "Asia/Jerusalem"
    assert pd.current_power_kw.value == 71.0           # 71000 W -> kW
    assert pd.energy_today_kwh.value == 38.0           # 38000 Wh -> kWh
    assert pd.energy_lifetime_kwh.value == 41000.0     # 41,000,000 Wh -> kWh

def test_alerts_and_co2_marked_not_exposed():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.alerts == []
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"

def test_inverters_listed_status_unknown_on_official_path():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    ids = {d.device_id for d in pd.devices}
    assert ids == {"SE-INV-1", "SE-INV-2"}
    assert all(d.status == DeviceStatus.UNKNOWN for d in pd.devices)

def test_revenue_mapped():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.revenue.value == 5390.0
    assert pd.currency == "ILS"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_solaredge_adapter.py -v`
Expected: FAIL (`ImportError: cannot import name 'map_solaredge_plant'`)

- [ ] **Step 4: Implement `solaranalysis/adapters/solaredge.py`**

```python
from __future__ import annotations
from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError

def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def map_solaredge_plant(details: dict, overview: dict, inventory: dict) -> PlantData:
    d = details.get("details", details)
    ov = overview.get("overview", overview)
    inv = inventory.get("Inventory", inventory)
    loc = d.get("location", {}) or {}
    pd = PlantData(
        plant_id=f"solaredge-{d.get('id')}",
        source_platform="solaredge",
        source_plant_id=str(d.get("id")),
        plant_name=d.get("name") or "SolarEdge site",
        peak_power_kwp=Metric(_num(d.get("peakPower")), "kWp"),  # already kW
        location_address=loc.get("address"),
        location_country=loc.get("country"),
        timezone=loc.get("timeZone"),
        install_date=d.get("installationDate"),
        currency=d.get("currency"),
        reporting_timestamp_utc=ov.get("lastUpdateTime"),
    )
    pd.current_power_kw = Metric(units.w_to_kw(_num((ov.get("currentPower") or {}).get("power"))), "kW")
    pd.energy_today_kwh = Metric(units.wh_to_kwh(_num((ov.get("lastDayData") or {}).get("energy"))), "kWh")
    pd.energy_month_kwh = Metric(units.wh_to_kwh(_num((ov.get("lastMonthData") or {}).get("energy"))), "kWh")
    pd.energy_year_kwh = Metric(units.wh_to_kwh(_num((ov.get("lastYearData") or {}).get("energy"))), "kWh")
    pd.energy_lifetime_kwh = Metric(units.wh_to_kwh(_num((ov.get("lifeTimeData") or {}).get("energy"))), "kWh")
    pd.revenue = Metric(_num(ov.get("revenue")), "currency")
    # Official Monitoring API exposes neither alerts nor CO2.
    pd.co2_avoided_kg = Metric(None, "kg", data_source_status="not_exposed")
    for iv in inv.get("inverters", []) or []:
        pd.devices.append(Device(
            device_id=str(iv.get("SN")),
            device_type="inverter",
            model=iv.get("model"),
            manufacturer="SolarEdge",
            status=DeviceStatus.UNKNOWN,  # official inventory has no live status
        ))
    if not pd.devices:
        pd.data_quality_flags.append("solaredge: no inverters in inventory payload")
    return pd

class SolarEdgeAdapter(SolarPortalAdapter):
    platform = "solaredge"

    OFFICIAL_BASE = "https://monitoringapi.solaredge.com"

    def __init__(self, auth, session_store, http=None):
        super().__init__(auth, session_store)
        self._http = http  # a requests.Session-like object; injectable for tests

    def login(self) -> None:
        # api_key mode needs no login. password mode = one-time headed Playwright
        # cookie-harvest (see README); harvested cookie is cached in session_store.
        if self.auth.mode == "api_key":
            if not self.auth.api_key:
                raise AdapterError("solaredge: mode=api_key but no api_key provided")
            return
        cached = self.sessions.load("solaredge")
        if cached and cached.get("cookie"):
            return
        raise AdapterError(
            "solaredge: no cached session cookie. Run `python -m solaranalysis.tools.se_login` "
            "to complete a one-time browser login (see README).")

    def _get(self, path: str, params: dict) -> dict:
        import requests
        http = self._http or requests
        if self.auth.mode == "api_key":
            params = {**params, "api_key": self.auth.api_key}
            r = http.get(f"{self.OFFICIAL_BASE}{path}", params=params, timeout=30)
        else:
            cached = self.sessions.load("solaredge") or {}
            r = http.get(f"{self.OFFICIAL_BASE}{path}", params=params,
                         cookies={"SPRING_SECURITY_REMEMBER_ME_COOKIE": cached.get("cookie", "")},
                         timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        if self._http is None and self.auth.mode == "api_key":
            self.login()
        sites = self._get("/sites/list", {}).get("sites", {}).get("site", [])
        results = []
        for s in sites:
            sid = s.get("id")
            details = self._get(f"/site/{sid}/details", {})
            overview = self._get(f"/site/{sid}/overview", {})
            inventory = self._get(f"/site/{sid}/Inventory", {})
            results.append(map_solaredge_plant(details, overview, inventory))
        return results
```

Note: `fetch`/`_get` are glue verified in the Task 13 smoke run; the pure `map_solaredge_plant` is the tested contract. The Playwright cookie-harvest helper (`solaranalysis/tools/se_login.py`) is built in Task 12 alongside the CLI.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_solaredge_adapter.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add solar-analysis/solaranalysis/adapters/solaredge.py solar-analysis/tests/test_solaredge_adapter.py solar-analysis/tests/fixtures/solaredge_*.json
git commit -m "feat(solar-analysis): SolarEdge adapter + mapping (fixture-tested)"
```

---

### Task 10: AI analysis layer (`core/analyze.py` + `prompts/system.txt`)

**Files:**
- Create: `solar-analysis/solaranalysis/prompts/system.txt`
- Create: `solar-analysis/solaranalysis/core/analyze.py`
- Test: `solar-analysis/tests/test_analyze.py`

**Interfaces:**
- Consumes: `PlantData` (+ `to_dict`), `TimeRange`, `core.units`, `core.rollup.plan_rollup`, `AppConfig`.
- Produces:
  - `build_data_block(plants: list[PlantData], time_range: TimeRange, meta: dict) -> str` — the serialized DATA block: compact JSON header + per-plant summary JSON + a CSV rollup table per plant. Deterministic ordering.
  - `pick_model(cfg: AppConfig, time_range: TimeRange) -> str` — cfg.model if set, else `claude-sonnet-5`, upgrading to `claude-opus-4-8` for `12mo`/`all`.
  - `verify_numbers(report_md: str, data_block: str) -> list[str]` — returns numbers appearing in the report that do NOT appear (as substrings, comma-stripped) in the data block (best-effort hallucination check).
  - `run_analysis(plants, time_range, cfg, client=None) -> str` — assembles messages, calls Claude (injected `client`), returns report markdown. Reads system prompt from `prompts/system.txt`.

- [ ] **Step 1: Create `solaranalysis/prompts/system.txt`**

```
You are a solar-PV fleet analyst. You write ONE report for an O&M operator comparing several plants.

Emit exactly these four section headers, verbatim and in this order:
## Production & Performance
## Health & Faults
## Financial / Savings
## Anomalies & Recommendations

GROUNDING CONTRACT (hard rules):
- Every numeric figure you state MUST be copied from, or arithmetically derived from, the DATA block. Never invent, estimate, or recall figures from training.
- If a metric is missing/null for a plant, write "not reported" — never fill a gap with a plausible number or a cross-plant average.
- When you compute a derived value (delta, ratio, %), show the two source numbers inline, e.g. "Plant A 1,240 kWh vs Plant B 980 kWh (+27%)".
- Units are fixed by the DATA block. Energy is kWh (cumulative over a period); power is kW (instantaneous). Never add kW to kWh or treat them as interchangeable.
- Timestamps are already normalized to each site's local timezone; do not convert them.

Compare the plants side by side in every section; name the best and worst performer and by how much, using only DATA figures. Prefer the normalized metric specific_yield_kwh_per_kwp for fair cross-plant comparison (it neutralizes plant size).

Surface any items in each plant's data_quality_flags in the Anomalies & Recommendations section. Recommendations must be concrete and each tied to the observation that triggered it.

Keep it concise and operator-facing: lead each section with the headline finding, then one small comparison table. No LaTeX.
```

- [ ] **Step 2: Write the failing test** — `tests/test_analyze.py`

```python
from solaranalysis.core.schema import PlantData, Metric, EnergyPoint, TimeRange
from solaranalysis.core.analyze import build_data_block, pick_model, verify_numbers, run_analysis
from solaranalysis.config import AppConfig

def _plant(name, kwp, life):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(kwp, "kWp"),
                     energy_lifetime_kwh=Metric(life, "kWh"),
                     energy_timeseries=[EnergyPoint("2025-01-01", 10.0, "day"),
                                        EnergyPoint("2025-02-01", 20.0, "day")])

def test_pick_model_defaults_and_upgrade():
    cfg = AppConfig()
    assert pick_model(cfg, TimeRange.SNAPSHOT) == "claude-sonnet-5"
    assert pick_model(cfg, TimeRange.LAST_12MO) == "claude-opus-4-8"
    assert pick_model(AppConfig(model="claude-haiku-4-5"), TimeRange.ALL) == "claude-haiku-4-5"

def test_build_data_block_contains_plants_and_csv():
    block = build_data_block([_plant("A", 100.0, 5000.0)], TimeRange.LAST_12MO,
                             {"currency": "ILS"})
    assert "A" in block
    assert "5000" in block
    # CSV header for the monthly rollup present
    assert "period,energy_kwh" in block

def test_verify_numbers_flags_hallucination():
    block = "plant A energy 5000 kWh"
    missing = verify_numbers("Plant A produced 5000 kWh, saving 9999.", block)
    assert "9999" in missing
    assert "5000" not in missing

def test_run_analysis_uses_injected_client():
    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": "## Production & Performance\nok"})()]
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw): return FakeMsg()
    out = run_analysis([_plant("A", 100.0, 5000.0)], TimeRange.SNAPSHOT,
                       AppConfig(), client=FakeClient())
    assert "Production & Performance" in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_analyze.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Implement `solaranalysis/core/analyze.py`**

```python
from __future__ import annotations
import io
import csv
import json
import re
from pathlib import Path
from .schema import PlantData, TimeRange
from .rollup import plan_rollup
from . import units
from ..config import AppConfig

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")

def pick_model(cfg: AppConfig, time_range: TimeRange) -> str:
    if cfg.model:
        return cfg.model
    if time_range in (TimeRange.LAST_12MO, TimeRange.ALL):
        return "claude-opus-4-8"
    return "claude-sonnet-5"

def _summary(pd: PlantData) -> dict:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    return {
        "plant_id": pd.plant_id,
        "plant_name": pd.plant_name,
        "vendor": pd.source_platform,
        "kwp": kwp,
        "energy_today_kwh": pd.energy_today_kwh.value,
        "energy_month_kwh": pd.energy_month_kwh.value,
        "energy_year_kwh": pd.energy_year_kwh.value,
        "energy_lifetime_kwh": life,
        "current_power_kw": pd.current_power_kw.value,
        "specific_yield_lifetime_kwh_per_kwp": units.round_opt(units.specific_yield(life, kwp)),
        "device_count": len(pd.devices),
        "devices_online": sum(1 for d in pd.devices if d.status.value == "online"),
        "alert_count": len(pd.alerts),
        "revenue": pd.revenue.value,
        "currency": pd.currency,
        "co2_avoided_kg": pd.co2_avoided_kg.value,
        "data_quality_flags": pd.data_quality_flags,
    }

def _csv_table(rollup: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["period", "energy_kwh"])
    for p in rollup["series"]:
        w.writerow([p.timestamp_local, p.energy_kwh])
    return buf.getvalue()

def build_data_block(plants: list[PlantData], time_range: TimeRange, meta: dict) -> str:
    parts = ["=== DATA (authoritative; do not go beyond it) ==="]
    parts.append("META: " + json.dumps({**meta, "range": time_range.value}, sort_keys=True))
    for pd in plants:
        parts.append(f"\n--- PLANT {pd.plant_id} ---")
        parts.append("SUMMARY: " + json.dumps(_summary(pd), sort_keys=True))
        roll = plan_rollup(pd, time_range)
        if roll["series"]:
            parts.append(f"SERIES ({roll['granularity']}):")
            parts.append(_csv_table(roll))
        if roll["worst"]:
            worst = ", ".join(f"{p.timestamp_local}={p.energy_kwh}" for p in roll["worst"])
            parts.append(f"WORST_PERIODS: {worst}")
    return "\n".join(parts)

def verify_numbers(report_md: str, data_block: str) -> list[str]:
    haystack = data_block.replace(",", "")
    missing = []
    for m in _NUM_RE.findall(report_md):
        norm = m.replace(",", "").rstrip(".")
        if norm in ("", "-"):
            continue
        if norm not in haystack:
            missing.append(norm)
    return missing

def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")

def run_analysis(plants, time_range, cfg: AppConfig, client=None) -> str:
    meta = {"currency": (plants[0].currency if plants else None)}
    data_block = build_data_block(plants, time_range, meta)
    model = pick_model(cfg, time_range)
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    lang = "Hebrew" if cfg.output_language == "he" else "English"
    user = (data_block + f"\n\nProduce the report in {lang} for time range: "
            f"{time_range.value}. Base every number on the DATA above.")
    msg = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": _system_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_analyze.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add solar-analysis/solaranalysis/prompts/system.txt solar-analysis/solaranalysis/core/analyze.py solar-analysis/tests/test_analyze.py
git commit -m "feat(solar-analysis): AI analysis layer (data block, model pick, verify)"
```

---

### Task 11: Styled HTML report (`core/report.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/core/report.py`
- Test: `solar-analysis/tests/test_report.py`

**Interfaces:**
- Consumes: `markdown` package.
- Produces:
  - `render_html(report_md: str, title: str, subtitle: str) -> str` — a self-contained HTML string (inline CSS, no external assets) wrapping the markdown-rendered analysis, with a header (title/subtitle) and generated-by footer.
  - `write_report(html: str, out_dir: str) -> str` — writes `out_dir/report.html`, returns path.

- [ ] **Step 1: Write the failing test** — `tests/test_report.py`

```python
from pathlib import Path
from solaranalysis.core.report import render_html, write_report

def test_render_html_is_self_contained():
    html = render_html("## Production & Performance\n\nPlant A leads.", "Solar Report", "12mo")
    assert "<style>" in html                 # inline CSS
    assert "http://" not in html and "https://" not in html  # no external assets
    assert "Production &amp; Performance" in html or "Production & Performance" in html
    assert "Solar Report" in html

def test_write_report_creates_file(tmp_path):
    html = render_html("## Health & Faults\n\nAll nominal.", "T", "snapshot")
    path = write_report(html, str(tmp_path))
    assert Path(path).exists()
    assert Path(path).name == "report.html"
    assert "Health" in Path(path).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/core/report.py`**

```python
from __future__ import annotations
import os
import markdown as md

_CSS = """
:root { --bg:#0f1720; --card:#16212e; --ink:#e7eef6; --muted:#8ba3ba; --accent:#f5b301; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width: 900px; margin: 0 auto; padding: 40px 24px 80px; }
header { border-bottom: 2px solid var(--accent); padding-bottom: 16px; margin-bottom: 32px; }
h1 { margin:0; font-size: 28px; }
.subtitle { color: var(--muted); margin-top: 6px; }
h2 { margin-top: 40px; font-size: 21px; color: var(--accent);
  border-left: 4px solid var(--accent); padding-left: 12px; }
table { width:100%; border-collapse: collapse; margin: 16px 0;
  background: var(--card); border-radius: 8px; overflow: hidden; }
th,td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #24344a; }
th { background:#1d2c3d; color: var(--ink); }
code { background:#0b1219; padding:2px 5px; border-radius:4px; }
footer { margin-top: 56px; color: var(--muted); font-size: 13px;
  border-top: 1px solid #24344a; padding-top: 16px; }
"""

_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body><div class="wrap">
<header><h1>{title}</h1><div class="subtitle">{subtitle}</div></header>
{body}
<footer>Generated by solar-analysis · figures computed in Python, narrative by Claude.</footer>
</div></body></html>"""

def render_html(report_md: str, title: str, subtitle: str) -> str:
    body = md.markdown(report_md, extensions=["tables", "fenced_code"])
    return _TEMPLATE.format(title=title, subtitle=subtitle, css=_CSS, body=body)

def write_report(html: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_report.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add solar-analysis/solaranalysis/core/report.py solar-analysis/tests/test_report.py
git commit -m "feat(solar-analysis): self-contained styled HTML report renderer"
```

---

### Task 12: CLI orchestration + SolarEdge login helper (`cli.py`, `tools/se_login.py`)

**Files:**
- Create: `solar-analysis/solaranalysis/pipeline.py`
- Create: `solar-analysis/solaranalysis/cli.py`
- Create: `solar-analysis/solaranalysis/tools/__init__.py`
- Create: `solar-analysis/solaranalysis/tools/se_login.py`
- Test: `solar-analysis/tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store, adapter_factory=get_adapter, analyzer=run_analysis) -> dict` — for each plant config: build adapter, `login()`, `fetch()`, normalize (specific_yield + validate), collect; then analyze; returns `{"report_md": str, "plants": list[PlantData], "verify_missing": list[str]}`. A per-plant fetch failure is caught and recorded as an unavailable plant (does not abort the run).
  - `cli.main(argv=None)` — argparse: `--config`, `--range {snapshot,30d,12mo,all}`, `--out`; wires real `SessionStore`, writes HTML, prints the output path.
  - `tools/se_login.py`: `harvest_cookie(username, password, session_store)` — drives a **headed** Playwright login to SolarEdge, stores the session cookie via `session_store.save("solaredge", {"cookie": ...}, ttl_seconds=~20 days)`.

- [ ] **Step 1: Write the failing test** — `tests/test_pipeline.py`

```python
from solaranalysis.config import AppConfig, PlantConfig, AuthConfig
from solaranalysis.core.schema import PlantData, Metric, TimeRange
from solaranalysis.core.session_store import SessionStore
from solaranalysis.pipeline import run_pipeline

class FakeAdapter:
    def __init__(self, pd): self._pd = pd
    def login(self): pass
    def fetch(self, tr): return [self._pd]

def _pd(name):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(100.0, "kWp"),
                     energy_lifetime_kwh=Metric(5000.0, "kWh"))

def test_run_pipeline_normalizes_and_analyzes(tmp_path):
    cfg = AppConfig(plants=[PlantConfig("A", AuthConfig("growatt", username="u", password="p"))])
    ss = SessionStore(str(tmp_path))
    def factory(auth, store): return FakeAdapter(_pd("A"))
    def analyzer(plants, tr, c, client=None): return "## Production & Performance\nok 50.0"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    assert "Production" in res["report_md"]
    # specific yield computed in Python: 5000 / 100 = 50.0
    assert res["plants"][0].specific_yield_kwh_per_kwp.value == 50.0

def test_pipeline_survives_one_plant_failure(tmp_path):
    class Boom:
        def login(self): raise RuntimeError("auth failed")
        def fetch(self, tr): raise RuntimeError("nope")
    cfg = AppConfig(plants=[
        PlantConfig("Bad", AuthConfig("growatt", username="bad", password="p")),
        PlantConfig("Good", AuthConfig("growatt", username="good", password="p")),
    ])
    ss = SessionStore(str(tmp_path))
    seq = [Boom(), FakeAdapter(_pd("Good"))]   # dispatched in plant order
    def factory(auth, store): return seq.pop(0)
    def analyzer(plants, tr, c, client=None): return "## Production & Performance\nok"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    names = [p.plant_name for p in res["plants"]]
    assert "Good" in names and "Bad" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implement `solaranalysis/pipeline.py`**

```python
from __future__ import annotations
from .config import AppConfig
from .core.schema import PlantData, TimeRange, Metric
from .core import units
from .core.validate import validate_plant
from .core.analyze import run_analysis, build_data_block, verify_numbers
from .adapters.base import get_adapter

def _normalize(pd: PlantData) -> PlantData:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    sy = units.specific_yield(life, kwp)
    pd.specific_yield_kwh_per_kwp = Metric(units.round_opt(sy), "kWh/kWp", is_derived=True)
    return validate_plant(pd)

def run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store,
                 adapter_factory=get_adapter, analyzer=run_analysis) -> dict:
    plants: list[PlantData] = []
    for pc in cfg.plants:
        try:
            adapter = adapter_factory(pc.auth, session_store)
            adapter.login()
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                plants.append(_normalize(pd))
        except Exception as e:  # isolate per-plant failures
            print(f"[warn] plant {pc.name!r} unavailable: {e}")
    report_md = analyzer(plants, time_range, cfg) if plants else "No plant data available."
    data_block = build_data_block(plants, time_range,
                                  {"currency": plants[0].currency if plants else None})
    return {"report_md": report_md, "plants": plants,
            "verify_missing": verify_numbers(report_md, data_block)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Implement `solaranalysis/cli.py`**

```python
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from .config import load_config
from .core.schema import TimeRange
from .core.session_store import SessionStore
from .core.report import render_html, write_report
from .pipeline import run_pipeline

def main(argv=None):
    p = argparse.ArgumentParser(prog="solar-analysis")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--range", default="30d", choices=[t.value for t in TimeRange])
    p.add_argument("--out", default=None)
    p.add_argument("--cache-dir", default=".session_cache")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    time_range = TimeRange(args.range)
    ss = SessionStore(args.cache_dir)
    res = run_pipeline(cfg, time_range, ss)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or f"output/{stamp}"
    title = "Solar Fleet Analysis"
    subtitle = f"{len(res['plants'])} plants · range {args.range} · {stamp} UTC"
    html = render_html(res["report_md"], title, subtitle)
    path = write_report(html, out_dir)
    if res["verify_missing"]:
        print(f"[warn] {len(res['verify_missing'])} report numbers not found in DATA: "
              f"{res['verify_missing'][:8]}", file=sys.stderr)
    print(f"Report written: {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Implement `solaranalysis/tools/se_login.py`**

```python
"""One-time headed browser login for SolarEdge to harvest a session cookie.

Usage: python -m solaranalysis.tools.se_login
Reads SOLAREDGE_USERNAME / SOLAREDGE_PASSWORD from .env, opens a real browser,
completes the login (solve any captcha/OTP manually if prompted), and caches the
session cookie for ~20 days so the adapter can replay data calls with plain requests.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from ..core.session_store import SessionStore

LOGIN_URL = "https://monitoring.solaredge.com/solaredge-apigw/api/login"
DASHBOARD_URL = "https://monitoring.solaredge.com/solaredge-web/p/home"
COOKIE_TTL = 20 * 24 * 3600  # ~20 days (community-reported validity)

def harvest_cookie(username: str, password: str, session_store: SessionStore) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # headed: user can solve challenges
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://monitoring.solaredge.com/")
        # Fill the login form (field names per research: j_username/j_password).
        page.fill("input[name='j_username'], input[type='email']", username)
        page.fill("input[name='j_password'], input[type='password']", password)
        page.click("button[type='submit'], input[type='submit']")
        page.wait_for_url("**/solaredge-web/**", timeout=120000)  # allow manual challenge
        cookies = ctx.cookies()
        browser.close()
    # Pick the session cookie (name drifts during migration; prefer known candidates).
    wanted = ("SPRING_SECURITY_REMEMBER_ME_COOKIE", "JSESSIONID", "SolarEdge_Session")
    value = next((c["value"] for name in wanted for c in cookies if c["name"] == name), None)
    if not value and cookies:
        value = cookies[0]["value"]
    if not value:
        raise RuntimeError("se_login: no cookie captured")
    session_store.save("solaredge", {"cookie": value}, ttl_seconds=COOKIE_TTL)
    return value

def main():
    load_dotenv()
    u = os.environ["SOLAREDGE_USERNAME"]
    p = os.environ["SOLAREDGE_PASSWORD"]
    ss = SessionStore(".session_cache")
    harvest_cookie(u, p, ss)
    print("SolarEdge session cookie cached (~20 days).")

if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 8: Commit**

```bash
git add solar-analysis/solaranalysis/pipeline.py solar-analysis/solaranalysis/cli.py solar-analysis/solaranalysis/tools/ solar-analysis/tests/test_pipeline.py
git commit -m "feat(solar-analysis): pipeline orchestration, CLI, SolarEdge login helper"
```

---

### Task 13: README, manual smoke test, and branch finish

**Files:**
- Create: `solar-analysis/README.md`
- Modify: `solar-analysis/.env` (local only, git-ignored — user fills real secrets)

**Interfaces:**
- Consumes: everything.
- Produces: docs + a verified end-to-end run.

- [ ] **Step 1: Write `solar-analysis/README.md`**

Content must cover: purpose; `pip install -r requirements.txt` + `playwright install chromium`; copy `.env.example`→`.env` and `config.example.yaml`→`config.yaml`; the one-time `python -m solaranalysis.tools.se_login` step for SolarEdge; run `python -m solaranalysis.cli --range 30d`; where the HTML lands (`output/<stamp>/report.html`); the "Python computes, Claude narrates" guarantee; rate-limit warnings (Growatt ≥5 min, SolarEdge 300/day); note SMA is Phase 2; token/api_key upgrade path.

- [ ] **Step 2: Manual smoke test — Growatt (real credentials, opt-in)**

Run:
```bash
cd solar-analysis
python -c "import growattServer; a=growattServer.GrowattApi(); import os; from dotenv import load_dotenv; load_dotenv(); r=a.login(os.environ['GROWATT_USERNAME'], os.environ['GROWATT_PASSWORD']); print('login success:', r.get('success')); print('keys:', list(a.plant_list(r['user']['id']).keys()))"
```
Expected: `login success: True`. If field keys differ from the fixture assumptions in `map_growatt_plant`, adjust the `fetch()` glue in `adapters/growatt.py` (NOT the tested mapping contract) to match, and re-run `python -m pytest tests/test_growatt_adapter.py`.

- [ ] **Step 3: Manual smoke test — SolarEdge cookie harvest**

Run: `python -m solaranalysis.tools.se_login`
Expected: a browser opens; complete login (solve any challenge); console prints "cookie cached". Then confirm a data call works:
```bash
python -c "from solaranalysis.config import load_config; from solaranalysis.core.session_store import SessionStore; from solaranalysis.adapters.base import get_adapter; from solaranalysis.core.schema import TimeRange; cfg=load_config('config.yaml'); ss=SessionStore('.session_cache'); a=get_adapter(cfg.plants[0].auth, ss); a.login(); print([p.plant_name for p in a.fetch(TimeRange.SNAPSHOT)])"
```
Expected: prints the site name(s). If the internal endpoint path/host differs (portal migration), capture the live path via the browser DevTools Network tab and update `SolarEdgeAdapter._get`/`fetch`.

- [ ] **Step 4: Full end-to-end run**

Run: `python -m solaranalysis.cli --range 30d`
Expected: `Report written: output/<stamp>/report.html`. Open it; confirm four sections, comparison tables, and no `[warn] ... numbers not found` (or investigate any listed).

- [ ] **Step 5: Commit docs**

```bash
git add solar-analysis/README.md
git commit -m "docs(solar-analysis): README + setup/run guide"
```

- [ ] **Step 6: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to decide merge/PR/cleanup for `feat/solar-analysis`.

---

## Self-Review

**Spec coverage:**
- §2 scope (SolarEdge+Growatt, SMA deferred) → Tasks 8, 9; SMA explicitly out (README note, Task 13). ✓
- §2 email/password auth, token-ready → Growatt `OpenApiV1` seam + SolarEdge `api_key` mode in adapters. ✓
- §4 architecture (adapters/core split) → Tasks 2–12 mirror the file tree. ✓
- §5 adapter interface → Task 7. ✓
- §6 normalized schema → Task 2. ✓
- §8 normalization (units, derived, sanity gates, nulls, rollups) → Tasks 3, 4, 5, 12(`_normalize`). ✓
- §10 AI layer (model pick, grounding prompt, JSON+CSV data block, verify, caching) → Task 10. ✓
- §11 styled HTML → Task 11. ✓
- §12 session/rate-limit → Task 6, used in adapters. ✓
- §13 error handling (per-plant isolation, manual-intervention login) → Task 12 pipeline + se_login. ✓
- §14 testing (unit, adapter contract via fixtures, numeric-verify, golden HTML) → every task. ✓
- Financial section needs tariffs (§16 open q) → `PlantConfig.tariff_per_kwh`/`currency` (Task 1), passed through pipeline. Note: `money()`/savings wiring into the summary is available via `units.money`; if the operator wants explicit savings in the data block, add `savings` to `_summary` using `tariff_per_kwh` — flagged here as the one optional enhancement not yet wired, to keep Phase 1 focused.

**Placeholder scan:** No TBD/TODO; every code step has complete code. Adapter `fetch()` glue carries an explicit "verify keys at smoke test" note (Task 8/9/13) — this is a real, bounded verification step, not a placeholder, because live field keys can only be confirmed against real accounts.

**Type consistency:** `Metric`/`Device`/`Alert`/`PlantData` field names are identical across Tasks 2, 4, 8, 9, 10, 12. `run_analysis(plants, time_range, cfg, client=None)` signature matches its call in `pipeline.run_pipeline` and the Task 10 test. `get_adapter(auth, session_store)` matches Task 7 + pipeline. `TimeRange` values (`snapshot/30d/12mo/all`) consistent across schema, analyze, CLI. ✓

**One correction folded in:** the financial `savings` figure is computable (`units.money`) but is not added to `_summary` in Task 10; noted above as the single optional follow-up so the plan doesn't silently imply savings appear without the tariff being wired into the data block.
