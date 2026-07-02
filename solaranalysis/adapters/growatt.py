from __future__ import annotations
from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, TimeRange,
)
from .base import SolarPortalAdapter, AdapterError

# ---------------------------------------------------------------------------
# Growatt adapter — two auth modes:
#
#   mode=password (primary): headless-browser login to server.growatt.com, then
#     read the dashboard's internal JSON endpoints:
#       POST /index/getPlantListTitle              -> [{id, plantName, timezone}]
#       POST /panel/getPlantData?plantId=          -> metadata, tariffs, CO2, eTotal
#       POST /panel/max/getMAXTotalData?plantId=   -> eToday/eTotal (kWh), money
#       POST /panel/getDevicesByPlant?plantId=     -> {obj:{max:[[sn,alias,status]]}}
#     Energy fields are already in kWh. current power / monthly-yearly energy are
#     not exposed by these endpoints (marked not_exposed).
#
#   mode=token: OpenAPI v1 REST via _growatt_v1.GrowattV1Client (kept as an
#     option; see map_growatt_v1 below).
# ---------------------------------------------------------------------------

_HOST = "https://server.growatt.com"

# Best-effort decode of the terse device-status code in getDevicesByPlant tuples.
# "0" = waiting (e.g. night), "1" = normal/online, "-1" = disconnected/lost.
_WEB_STATUS = {
    "1": DeviceStatus.ONLINE,
    "0": DeviceStatus.STANDBY,
    "-1": DeviceStatus.OFFLINE,
    "2": DeviceStatus.FAULT,
    "3": DeviceStatus.FAULT,
}

# v1 token path status map.
_STATUS_MAP = {1: DeviceStatus.ONLINE, 0: DeviceStatus.OFFLINE}


def _f(x):
    """String/empty -> float, else None."""
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# mode=password mapper (server.growatt.com dashboard)
# ---------------------------------------------------------------------------

def map_growatt_web(plant: dict, details: dict, totals: dict,
                    devices_obj: dict) -> PlantData:
    """Pure mapper: one plant's dashboard payloads -> PlantData.

    * plant       : item from getPlantListTitle  {id, plantName, timezone}
    * details     : getPlantData ``obj``
    * totals      : getMAXTotalData ``obj``
    * devices_obj : getDevicesByPlant ``obj`` (has a ``max`` list of tuples)
    """
    plant = plant or {}
    details = details or {}
    totals = totals or {}
    devices_obj = devices_obj or {}

    pid = plant.get("id") or details.get("id")

    pd = PlantData(
        plant_id=f"growatt-{pid}",
        source_platform="growatt",
        source_plant_id=str(pid) if pid is not None else "",
        plant_name=details.get("plantName") or plant.get("plantName") or "Growatt plant",
        peak_power_kwp=Metric(_f(details.get("nominalPower")), "kWp"),
        location_address=details.get("city"),
        location_country=details.get("country"),
        latitude=_f(details.get("lat")),
        longitude=_f(details.get("lng")),
        timezone=plant.get("timezone") or details.get("timezone"),
        install_date=details.get("creatDate"),
        currency=details.get("moneyUnit"),
    )

    # Energy — already kWh. Today from getMAXTotalData; lifetime from either.
    pd.energy_today_kwh = Metric(_f(totals.get("eToday")), "kWh")
    lifetime = totals.get("eTotal") if totals.get("eTotal") is not None else details.get("eTotal")
    pd.energy_lifetime_kwh = Metric(_f(lifetime), "kWh")
    # Monthly/yearly energy are not returned by these endpoints.
    pd.energy_month_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(None, "kW", data_source_status="not_exposed")

    # Financials — lifetime money earned.
    money = totals.get("mTotal")
    pd.revenue = Metric(_f(money), "currency",
                        data_source_status="ok" if money is not None else "not_exposed")

    co2 = details.get("co2")
    pd.co2_avoided_kg = Metric(_f(co2), "kg",
                               data_source_status="ok" if co2 not in (None, "") else "not_exposed")
    tree = details.get("tree")
    pd.trees_equivalent = Metric(_f(tree), "count",
                                 data_source_status="ok" if tree not in (None, "") else "not_exposed")

    # Devices: obj.max is a list of [serial, alias, status_code].
    decoded = False
    for row in devices_obj.get("max") or []:
        if not row:
            continue
        sn = row[0]
        status_code = str(row[2]).strip() if len(row) > 2 else ""
        status = _WEB_STATUS.get(status_code, DeviceStatus.UNKNOWN)
        decoded = True
        pd.devices.append(Device(
            device_id=str(sn),
            device_type="inverter",
            model=str(row[1]) if len(row) > 1 else None,
            manufacturer="Growatt",
            status=status,
        ))
    if decoded:
        pd.data_quality_flags.append(
            "growatt: inverter status decoded best-effort from dashboard status code")

    # Dashboard device endpoint carries no per-device fault list.
    pd.alerts = []
    return pd


# ---------------------------------------------------------------------------
# mode=token mapper (OpenAPI v1) — retained option
# ---------------------------------------------------------------------------

def map_growatt_v1(details: dict, overview: dict, devices: list[dict]) -> PlantData:
    details = details or {}
    overview = overview or {}
    devices = devices or []

    peak_power = details.get("peak_power_actual")
    if peak_power is None:
        peak_power = details.get("nominal_power")

    pd = PlantData(
        plant_id=f"growatt-{details.get('plant_id')}",
        source_platform="growatt",
        source_plant_id=str(details.get("plant_id")) if details.get("plant_id") is not None else "",
        plant_name=details.get("name") or "Growatt plant",
        peak_power_kwp=Metric(_f(peak_power), "kWp"),
        location_country=details.get("country"),
        latitude=_f(details.get("latitude")),
        longitude=_f(details.get("longitude")),
        install_date=details.get("create_date") or None,
        currency=details.get("currency"),
    )
    pd.energy_today_kwh = Metric(_f(overview.get("today_energy")), "kWh")
    pd.energy_month_kwh = Metric(_f(overview.get("monthly_energy")), "kWh")
    pd.energy_lifetime_kwh = Metric(_f(overview.get("total_energy")), "kWh")
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(_f(overview.get("current_power")), "kW")

    co2_raw = overview.get("co2")
    pd.co2_avoided_kg = Metric(
        _f(co2_raw), "kg",
        data_source_status="ok" if co2_raw not in (None, "") else "not_exposed",
    )

    for d in devices:
        lost = bool(d.get("lost"))
        if lost:
            status = DeviceStatus.OFFLINE
        else:
            try:
                status = _STATUS_MAP.get(int(d.get("status")), DeviceStatus.UNKNOWN)
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
    pd.alerts = []
    return pd


class GrowattAdapter(SolarPortalAdapter):
    platform = "growatt"

    def __init__(self, auth, session_store, client=None):
        super().__init__(auth, session_store)
        self._client = client  # token-mode v1 client (injectable for tests)

    def login(self) -> None:
        if self.auth.mode == "password":
            if not self.auth.username or not self.auth.password:
                raise AdapterError("growatt: username/password not configured")
            return
        if self.auth.mode == "token":
            if not self.auth.token:
                raise AdapterError("growatt: mode=token but no token configured")
            if self._client is None:
                from ._growatt_v1 import GrowattV1Client
                self._client = GrowattV1Client(self.auth.token)
            return
        raise AdapterError(f"growatt: unsupported mode {self.auth.mode!r}")

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        if self.auth.mode == "token":
            return self._fetch_token()
        return self._fetch_web()

    def _fetch_web(self) -> list[PlantData]:
        from ._browser import BrowserSession
        with BrowserSession() as bs:
            store = bs.capture(["getPlantListTitle"])
            bs.page.goto(f"{_HOST}/login", wait_until="domcontentloaded")
            try:
                bs.page.get_by_role("button", name="Agree").click(timeout=4000)
            except Exception:
                pass
            bs.page.get_by_role("textbox", name="User Name").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Login").click()
            bs.page.wait_for_url("**/index**", timeout=45000)
            bs.page.wait_for_timeout(4000)

            plants = store.get("getPlantListTitle")
            if not plants:
                plants = bs.post_json(f"{_HOST}/index/getPlantListTitle") or []
            if not plants:
                raise AdapterError("growatt: plant list did not load")

            results = []
            for pl in plants:
                pid = pl.get("id")
                # Per-plant isolation: one plant's transient request failure
                # must not discard the whole account. Fall back to the plant's
                # list entry (name/id) with a data-quality flag.
                try:
                    details = (bs.post_json(f"{_HOST}/panel/getPlantData?plantId={pid}") or {}).get("obj", {})
                    totals = (bs.post_json(f"{_HOST}/panel/max/getMAXTotalData?plantId={pid}") or {}).get("obj", {})
                    devices = (bs.post_json(f"{_HOST}/panel/getDevicesByPlant?plantId={pid}") or {}).get("obj", {})
                    results.append(map_growatt_web(pl, details, totals, devices))
                except Exception as e:
                    pd = map_growatt_web(pl, {}, {}, {})
                    pd.data_quality_flags.append(f"growatt: live fetch failed for this plant ({e})")
                    results.append(pd)
            return results

    def _fetch_token(self) -> list[PlantData]:
        pl = self._client.plant_list()
        if isinstance(pl, dict):
            plants = pl.get("plants") if pl.get("plants") is not None else pl.get("data", [])
        elif isinstance(pl, list):
            plants = pl
        else:
            plants = []
        results = []
        for plant in plants or []:
            pid = plant.get("plant_id") or plant.get("id")
            try:
                details = self._client.plant_details(pid)
                overview = self._client.plant_energy_overview(pid)
                devices = (self._client.device_list(pid) or {}).get("devices", [])
                results.append(map_growatt_v1(details, overview, devices))
            except Exception as e:
                pd = map_growatt_v1({"plant_id": pid}, {}, [])
                pd.data_quality_flags.append(f"growatt: token fetch failed for this plant ({e})")
                results.append(pd)
        return results
