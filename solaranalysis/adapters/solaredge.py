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
