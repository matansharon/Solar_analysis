from __future__ import annotations
from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, TimeRange,
)
from .base import SolarPortalAdapter, AdapterError

# ---------------------------------------------------------------------------
# Growatt OpenAPI v1 mapper
#
# Classic Growatt mobile login (newTwoLoginAPI.do) is 403-blocked, and the
# maintained growattServer 2.x library requires Python 3.11/3.12 (this
# codebase targets 3.10). This adapter instead talks to the OpenAPI v1 REST
# endpoints via `_growatt_v1.GrowattV1Client`, authenticating with a
# ShinePhone-app API token sent as the `token` HTTP header.
#
# CONFIRM LIVE — field names below are the best-known v1 field names from
# public docs/probes but have NOT been validated against a real token yet.
# Every assumed field + unit is called out at its use site. The mapper is
# written defensively (`.get()` everywhere, no KeyError risk) so it degrades
# to `None`/"not_exposed" rather than crashing if a name is wrong; the first
# live run against a real plant is the real validation and may require
# renaming a handful of `.get()` keys.
#
# plant/details (CONFIRM LIVE names):
#   - name                    -> plant name
#   - peak_power_actual       -> peak power; CONFIRM LIVE unit, assumed kWp
#                                (nominal_power is an alternate name seen in
#                                other Growatt API generations; not used here
#                                unless peak_power_actual is absent)
#   - city, country           -> location
#   - latitude, longitude     -> assumed decimal degrees as strings
#   - create_date             -> install date
#   - currency                -> ISO currency code
#
# plant/data (CONFIRM LIVE names):
#   - today_energy, monthly_energy, total_energy -> CONFIRM LIVE unit,
#                                assumed already kWh (no conversion applied)
#   - current_power           -> CONFIRM LIVE unit: assumed kW (NOT W); if a
#                                live call shows it's actually W this needs a
#                                units.w_to_kw() conversion added
#   - co2                     -> optional; treated "not_exposed" when absent
# ---------------------------------------------------------------------------

_STATUS_MAP = {1: DeviceStatus.ONLINE, 0: DeviceStatus.OFFLINE}  # CONFIRM LIVE: full status code table


def _f(x):
    """String/empty -> float, else None. Defensive against '' and missing fields."""
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def map_growatt_v1(details: dict, overview: dict, devices: list[dict]) -> PlantData:
    details = details or {}
    overview = overview or {}
    devices = devices or []

    peak_power = details.get("peak_power_actual")
    if peak_power is None:
        peak_power = details.get("nominal_power")  # CONFIRM LIVE: alternate field name

    pd = PlantData(
        plant_id=f"growatt-{details.get('plant_id')}",
        source_platform="growatt",
        source_plant_id=str(details.get("plant_id")) if details.get("plant_id") is not None else "",
        plant_name=details.get("name") or "Growatt plant",
        peak_power_kwp=Metric(_f(peak_power), "kWp"),  # CONFIRM LIVE unit
        location_country=details.get("country"),
        latitude=_f(details.get("latitude")),
        longitude=_f(details.get("longitude")),
        install_date=details.get("create_date") or None,
        currency=details.get("currency"),
    )

    pd.energy_today_kwh = Metric(_f(overview.get("today_energy")), "kWh")      # CONFIRM LIVE: already kWh
    pd.energy_month_kwh = Metric(_f(overview.get("monthly_energy")), "kWh")    # CONFIRM LIVE: already kWh
    pd.energy_lifetime_kwh = Metric(_f(overview.get("total_energy")), "kWh")   # CONFIRM LIVE: already kWh
    # Growatt v1 has no calendar-year total in plant/data.
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(_f(overview.get("current_power")), "kW")     # CONFIRM LIVE: unit assumed kW, not W

    co2_raw = overview.get("co2")
    pd.co2_avoided_kg = Metric(
        _f(co2_raw),
        "kg",
        data_source_status="ok" if co2_raw not in (None, "") else "not_exposed",
    )

    for d in devices:
        lost = bool(d.get("lost"))
        if lost:
            status = DeviceStatus.OFFLINE
        else:
            status_int = d.get("status")
            try:
                status = _STATUS_MAP.get(int(status_int), DeviceStatus.UNKNOWN)  # CONFIRM LIVE: full status table
            except (TypeError, ValueError):
                status = DeviceStatus.UNKNOWN
        pd.devices.append(Device(
            device_id=str(d.get("device_sn")),
            device_type="inverter",
            model=d.get("model"),
            manufacturer=d.get("manufacturer") or "Growatt",
            status=status,
            last_seen_local=d.get("last_update_time"),
        ))

    # v1 device/list carries no fault/warning payload in the confirmed shape;
    # alerts are not populated from this endpoint.
    pd.alerts = []

    return pd


class GrowattAdapter(SolarPortalAdapter):
    platform = "growatt"

    def __init__(self, auth, session_store, client=None):
        super().__init__(auth, session_store)
        self._client = client

    def login(self) -> None:
        if self.auth.mode != "token":
            raise AdapterError(
                "growatt: classic password login is blocked by Growatt; use mode=token "
                "with a ShinePhone API token"
            )
        if not self.auth.token:
            raise AdapterError("growatt: no token configured")
        if self._client is None:
            from ._growatt_v1 import GrowattV1Client
            self._client = GrowattV1Client(self.auth.token)

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        if self._client is None:
            self.login()

        pl = self._client.plant_list()
        if isinstance(pl, dict):
            plants = pl.get("plants") if pl.get("plants") is not None else pl.get("data", [])  # CONFIRM LIVE: response key
        elif isinstance(pl, list):
            plants = pl
        else:
            plants = []

        results = []
        for plant in plants or []:
            pid = plant.get("plant_id") or plant.get("id")
            details = self._client.plant_details(pid)
            overview = self._client.plant_energy_overview(pid)
            devices = (self._client.device_list(pid) or {}).get("devices", [])
            results.append(map_growatt_v1(details, overview, devices))
        return results
