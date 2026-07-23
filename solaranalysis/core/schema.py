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
class RawPayload:
    endpoint_label: str
    url: str
    method: str
    status: int | None
    body: object  # JSON-serializable portal response (dict/list/scalar)

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
    # portal-specific KPIs with no schema slot (flat scalars only, e.g. SMA
    # yesterday/last-month yields, Growatt revenue-today) — reaches the LLM
    # via analyze._summary and persists with the snapshot.
    extras: dict = field(default_factory=dict)
    # pipeline metadata
    fetched_at_utc: str | None = None  # when this run actually pulled the data
    config_plant_id: int | None = None  # web app's plants.id this fetch belongs to
    data_quality_flags: list[str] = field(default_factory=list)
    # untouched portal responses for this fetch, persisted verbatim when the
    # web runner enables raw capture; excluded from to_dict (never fed to the LLM).
    raw_payloads: list = field(default_factory=list)

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
