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
        if self._user_id is not None:
            return  # already authenticated in this process
        if not self.sessions.can_poll("growatt", 300):
            raise AdapterError("growatt: poll guard active (min 5 min between logins)")
        resp = client.login(self.auth.username, self.auth.password)
        self.sessions.mark_poll("growatt")
        if not resp or not resp.get("success"):
            raise AdapterError("growatt: login failed")
        self._user_id = resp["user"]["id"]

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
