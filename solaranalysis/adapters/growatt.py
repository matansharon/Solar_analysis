from __future__ import annotations
import calendar
from datetime import date, timedelta

from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, Alert, AlertSeverity,
    EnergyPoint, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError
from ._common import safe_step, month_keys_for, year_keys_for, clip_series

# ---------------------------------------------------------------------------
# Growatt adapter — two auth modes:
#
#   mode=password (primary): headless-browser login to server.growatt.com, then
#     read the dashboard's internal JSON endpoints:
#       POST /index/getPlantListTitle              -> [{id, plantName, timezone}]
#       POST /panel/getPlantData?plantId=          -> metadata, tariffs, CO2, eTotal
#       POST /panel/max/getMAXTotalData?plantId=   -> eToday/eTotal (kWh), money
#       POST /panel/getDevicesByPlant?plantId=     -> {obj:{max:[[sn,alias,status]]}}
#     History/detail endpoints (verified live 2026-07-07; all take FORM-encoded
#     params — query strings return null — and values are already kWh / W):
#       POST /panel/max/getMAXMonthChart   form{plantId,date=YYYY-MM}
#            -> {obj:{energy:[kWh per day, 0.0 for future days]}}
#       POST /panel/max/getMAXYearChart    form{plantId,year=YYYY}
#            -> {obj:{energy:[12 monthly kWh]}}
#       POST /panel/max/getMAXTotalChart   form{plantId,year=YYYY}
#            -> {obj:{energy:[yearly kWh, window ending at `year`]}}
#       POST /panel/getDevicesByPlantList  form{plantId,currPage}
#            -> {obj:{pages,datas:[{sn,deviceModel,status,eToday,eMonth,eTotal,
#                pac (W), lastUpdateTime, ...}]}}
#       POST /log/getNewPlantFaultLog      form{plantId,date=YYYY-MM-DD,
#                toPageNum,type=1}
#            -> {obj:{pages,datas:[{eventId,eventName,time,sn,solution,
#                recoveryTime, ...}]}}  (page 1 = most recent)
#
#   mode=token: OpenAPI v1 REST via _growatt_v1.GrowattV1Client (kept as an
#     option; see map_growatt_v1 below).
# ---------------------------------------------------------------------------

_HOST = "https://server.growatt.com"

from . import _browser

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
    return units.to_float(x)


def _goto_retry(page, url, attempts: int = 3, per_attempt_ms: int = 20000,
                backoff_ms: int = 3000) -> None:
    """Navigate to ``url`` with a bounded retry.

    server.growatt.com intermittently stalls the navigation request itself
    (Chromium raises ``net::ERR_TIMED_OUT`` / connection reset), a partial-
    outage pattern that recovers within seconds — no ``wait_until`` strategy
    helps because the initial response never arrives. Each attempt uses a
    short per-attempt timeout so a stalled request is abandoned quickly and a
    healthy retry can succeed; the last failure propagates if all attempts do.
    """
    last = None
    for i in range(attempts):
        try:
            page.goto(url, wait_until="commit", timeout=per_attempt_ms)
            return
        except Exception as e:
            last = e
            if i < attempts - 1:
                page.wait_for_timeout(backoff_ms)
    raise last


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

    # Financials — lifetime money earned; today's revenue rides in extras.
    money = totals.get("mTotal")
    pd.revenue = Metric(_f(money), "currency",
                        data_source_status="ok" if money is not None else "not_exposed")
    m_today = _f(totals.get("mToday"))
    if m_today is not None:
        pd.extras["revenue_today"] = m_today

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
# mode=password history/detail mappers (all pure; payload shapes in header)
# ---------------------------------------------------------------------------

def map_growatt_month_chart(obj: dict, month_key: str) -> list[EnergyPoint]:
    """getMAXMonthChart ``obj`` -> daily EnergyPoints for 'YYYY-MM'.

    The array is always 31 long; entries beyond the month's real length are
    ignored. Future days arrive as 0.0 — callers clip to today (clip_series).
    """
    vals = (obj or {}).get("energy") or []
    try:
        y, m = int(month_key[:4]), int(month_key[5:7])
        ndays = calendar.monthrange(y, m)[1]
    except ValueError:
        return []
    out = []
    for i, v in enumerate(vals[:ndays]):
        f = _f(v)
        if f is not None:
            out.append(EnergyPoint(f"{month_key}-{i + 1:02d}", f, "day"))
    return out


def map_growatt_year_chart(obj: dict, year: str) -> list[EnergyPoint]:
    """getMAXYearChart ``obj`` -> monthly EnergyPoints for 'YYYY'."""
    vals = (obj or {}).get("energy") or []
    out = []
    for i, v in enumerate(vals[:12]):
        f = _f(v)
        if f is not None:
            out.append(EnergyPoint(f"{year}-{i + 1:02d}", f, "month"))
    return out


def map_growatt_total_chart(obj: dict, end_year: int) -> list[EnergyPoint]:
    """getMAXTotalChart ``obj`` -> yearly EnergyPoints; the array is a window
    of consecutive years ending at ``end_year``."""
    vals = (obj or {}).get("energy") or []
    start = end_year - len(vals) + 1
    out = []
    for i, v in enumerate(vals):
        f = _f(v)
        if f is not None:
            out.append(EnergyPoint(str(start + i), f, "year"))
    return out


def map_growatt_faults(obj: dict) -> list[Alert]:
    """getNewPlantFaultLog ``obj`` -> Alerts (eventId like 'Warning 106')."""
    out = []
    for row in (obj or {}).get("datas") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("eventId") or "").strip()
        sev = (AlertSeverity.ERROR
               if code.lower().startswith(("fault", "error"))
               else AlertSeverity.WARNING)
        sn = row.get("sn") or row.get("deviceSn") or "growatt"
        out.append(Alert(
            alert_id=f"{sn}-{row.get('time') or 'unknown'}",
            severity=sev,
            code=code or None,
            message=row.get("eventName"),
            timestamp_local=row.get("time"),
            resolved=bool(row.get("recoveryTime")),
        ))
    return out


def map_growatt_device_rows(rows: list[dict]) -> list[Device]:
    """getDevicesByPlantList ``datas`` rows -> Devices with real per-device
    model / status / live power (pac is W) / lifetime energy / last-seen."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("sn"):
            continue
        out.append(Device(
            device_id=str(r.get("sn")),
            device_type="inverter",
            model=r.get("deviceModel") or r.get("alias"),
            manufacturer="Growatt",
            status=_WEB_STATUS.get(str(r.get("status", "")).strip(),
                                   DeviceStatus.UNKNOWN),
            current_power_kw=units.round_opt(units.w_to_kw(_f(r.get("pac"))), 3),
            energy_lifetime_kwh=_f(r.get("eTotal")),
            last_seen_local=r.get("lastUpdateTime"),
        ))
    return out


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


def map_growatt_v1_history(data: dict, granularity: str) -> list[EnergyPoint]:
    """v1 /plant/energy ``data`` -> EnergyPoints ({energys:[{date,energy}]})."""
    out = []
    for r in (data or {}).get("energys") or []:
        if not isinstance(r, dict):
            continue
        ts = str(r.get("date") or "").strip()
        f = _f(r.get("energy"))
        if ts and f is not None:
            out.append(EnergyPoint(ts, f, granularity))
    return sorted(out, key=lambda p: p.timestamp_local)


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

    def _authenticate(self, bs, had_state: bool) -> None:
        logged_in = False
        if had_state:
            _goto_retry(bs.page, f"{_HOST}/index")
            try:
                # wait_until="commit": the portal routinely never fires 'load'
                # (see _goto_retry); requiring it here misreads a valid
                # session as logged-out and forces a needless full login.
                bs.page.wait_for_url("**/index**", wait_until="commit",
                                     timeout=10000)
                logged_in = True
            except Exception:
                logged_in = False
        if not logged_in:
            _goto_retry(bs.page, f"{_HOST}/login")
            bs.page.get_by_role("textbox", name="User Name").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            # Clicking the cookie banner's Agree before its handler is bound
            # leaves consent pending, and the portal's Login button then
            # submits nothing at all (observed live: no login POST fires).
            # So retry the Agree+Login pair; the wait keys off the navigation
            # commit because this portal routinely never fires 'load'.
            last = None
            for attempt in range(3):
                try:
                    bs.page.get_by_role("button", name="Agree").click(timeout=2000)
                except Exception:
                    pass  # banner already dismissed or not shown
                bs.page.get_by_role("button", name="Login").click()
                try:
                    bs.page.wait_for_url("**/index**", wait_until="commit",
                                         timeout=15000)
                    return
                except Exception as e:
                    last = e
            raise last

    def verify_login(self) -> None:
        self.login()
        if self.auth.mode == "token":
            try:
                self._client.plant_list()
            except AdapterError:
                raise
            except Exception as e:
                raise AdapterError(f"growatt: token login failed ({e})")
            return
        state = self._load_session()
        try:
            with _browser.BrowserSession(storage_state=state) as bs:
                self._authenticate(bs, had_state=bool(state))
                self._save_session(bs)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"growatt: login failed ({e})")

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        if self.auth.mode == "token":
            return self._fetch_token(time_range)
        return self._fetch_web(time_range)

    def _fetch_web(self, time_range: TimeRange) -> list[PlantData]:
        state = self._load_session()
        with _browser.BrowserSession(storage_state=state) as bs:
            store = bs.capture(["getPlantListTitle"])
            self._authenticate(bs, had_state=bool(state))
            bs.page.wait_for_timeout(4000)

            plants = store.get("getPlantListTitle")
            if not plants:
                plants = bs.post_json(f"{_HOST}/index/getPlantListTitle") or []
            if not plants:
                raise AdapterError("growatt: plant list did not load")
            self._save_session(bs)

            results = []
            for pl in plants:
                if not isinstance(pl, dict):
                    continue  # defensive: unexpected entry shape in the list
                pid = pl.get("id")
                # Per-plant isolation: one plant's transient request failure
                # must not discard the whole account. Fall back to the plant's
                # list entry (name/id) with a data-quality flag.
                try:
                    details = (bs.post_json(f"{_HOST}/panel/getPlantData?plantId={pid}") or {}).get("obj", {})
                    totals = (bs.post_json(f"{_HOST}/panel/max/getMAXTotalData?plantId={pid}") or {}).get("obj", {})
                    devices = (bs.post_json(f"{_HOST}/panel/getDevicesByPlant?plantId={pid}") or {}).get("obj", {})
                    pd = map_growatt_web(pl, details, totals, devices)
                except Exception as e:
                    pd = map_growatt_web(pl, {}, {}, {})
                    pd.data_quality_flags.append(f"growatt: live fetch failed for this plant ({e})")
                    results.append(pd)
                    continue
                # Deep fetch (device detail, history charts, fault log) is
                # per-endpoint best-effort via safe_step — never fails the plant.
                self._enrich_web(bs, pd, pid, time_range)
                results.append(pd)
            return results

    # ---- password-mode deep fetch -------------------------------------

    def _device_rows(self, bs, pid) -> list[dict]:
        """All getDevicesByPlantList rows (paginated, bounded)."""
        rows, page, pages = [], 1, 1
        while page <= min(pages, 5):
            r = bs.post_json(f"{_HOST}/panel/getDevicesByPlantList",
                             form={"plantId": str(pid), "currPage": str(page)})
            obj = (r or {}).get("obj") or {}
            rows += [x for x in obj.get("datas") or [] if isinstance(x, dict)]
            try:
                pages = int(obj.get("pages") or 1)
            except (TypeError, ValueError):
                pages = 1
            page += 1
        return rows

    def _chart_obj(self, bs, pd, dtype: str, kind: str, form: dict):
        """POST one /panel/<type>/get<TYPE><kind> chart; None + flag on failure.

        The chart family is namespaced by device type (this account: 'max' ->
        getMAXMonthChart etc.), taken from the device list's deviceTypeName.
        """
        t = "".join(ch for ch in dtype if ch.isalnum()).lower() or "max"
        url = f"{_HOST}/panel/{t}/get{t.upper()}{kind}"

        def call():
            r = bs.post_json(url, form=form)
            obj = r.get("obj") if isinstance(r, dict) else None
            if not isinstance(obj, dict):
                raise AdapterError("empty response")
            return obj
        when = form.get("date") or form.get("year") or ""
        return safe_step(pd, f"growatt: {kind} {when}", call)

    def _enrich_web(self, bs, pd, pid, time_range: TimeRange) -> None:
        today = date.today()
        today_iso = today.isoformat()

        # Rich device rows: real model/status/last-seen per inverter, plus the
        # plant KPIs the snapshot endpoints lack (eMonth, pac).
        rows = safe_step(pd, "growatt: device list",
                         lambda: self._device_rows(bs, pid)) or []
        dtype = "max"
        if rows:
            dtype = str(rows[0].get("deviceTypeName") or "max")
            devs = map_growatt_device_rows(rows)
            if devs:
                pd.devices = devs
            month_vals = [v for v in (_f(r.get("eMonth")) for r in rows)
                          if v is not None]
            if month_vals:
                pd.energy_month_kwh = Metric(round(sum(month_vals), 1), "kWh",
                                             is_derived=len(rows) > 1)
            pac_vals = [v for v in (_f(r.get("pac")) for r in rows)
                        if v is not None]
            if pac_vals:
                pd.current_power_kw = Metric(
                    units.round_opt(units.w_to_kw(sum(pac_vals)), 3), "kW")
                pd.data_quality_flags.append(
                    "growatt: current power summed from per-device pac as of lastUpdateTime")

        def chart(kind: str, form: dict):
            return self._chart_obj(bs, pd, dtype, kind,
                                   {"plantId": str(pid), **form})

        # Current-year monthly chart fills the year KPI on every range.
        cur_year = str(today.year)
        year_obj = chart("YearChart", {"year": cur_year})
        cur_year_months = (map_growatt_year_chart(year_obj, cur_year)
                           if year_obj is not None else [])
        if cur_year_months:
            done = [p.energy_kwh for p in cur_year_months
                    if p.timestamp_local <= today_iso[:7]]
            pd.energy_year_kwh = Metric(round(sum(done), 1), "kWh",
                                        is_derived=True)

        # History series per range. Chart arrays pad future periods with 0.0
        # and pre-install periods with 0.0 as well, so clip to both bounds.
        install = (pd.install_date or "")[:10] or None
        if time_range == TimeRange.LAST_30D:
            pts = []
            for mk in month_keys_for(time_range, today):
                obj = chart("MonthChart", {"date": mk})
                if obj is not None:
                    pts += map_growatt_month_chart(obj, mk)
            start = (today - timedelta(days=29)).isoformat()
            if install and install > start:
                start = install
            pd.energy_timeseries = clip_series(pts, start, today_iso)
        elif time_range in (TimeRange.LAST_12MO, TimeRange.ALL):
            years = year_keys_for(time_range, today, pd.install_date)
            if time_range == TimeRange.ALL and not pd.install_date:
                # No install date: find production years from the total chart
                # (a multi-year window ending at the current year).
                tobj = chart("TotalChart", {"year": cur_year})
                if tobj is not None:
                    nz = [p.timestamp_local
                          for p in map_growatt_total_chart(tobj, today.year)
                          if p.energy_kwh]
                    years = sorted(set(nz) | {cur_year})
            pts = []
            for yk in years:
                if yk == cur_year:
                    pts += cur_year_months
                    continue
                obj = chart("YearChart", {"year": yk})
                if obj is not None:
                    pts += map_growatt_year_chart(obj, yk)
            if time_range == TimeRange.LAST_12MO:
                start = f"{today.year - 1:04d}-{today.month:02d}"
            else:
                start = install[:7] if install else (years[0] + "-01" if years else "0000-01")
            pd.energy_timeseries = clip_series(pts, start, today_iso[:7])

        # Fault/warning log: page 1 = the most recent events.
        def fault_page():
            r = bs.post_json(f"{_HOST}/log/getNewPlantFaultLog",
                             form={"plantId": str(pid), "date": today_iso,
                                   "toPageNum": "1", "type": "1"})
            obj = r.get("obj") if isinstance(r, dict) else None
            if not isinstance(obj, dict):
                raise AdapterError("empty response")
            return obj
        fl = safe_step(pd, "growatt: fault log", fault_page)
        if fl is not None:
            alerts = map_growatt_faults(fl)
            if alerts:
                pd.alerts = alerts
                try:
                    pages = int(fl.get("pages") or 1)
                except (TypeError, ValueError):
                    pages = 1
                if pages > 1:
                    pd.data_quality_flags.append(
                        f"growatt: fault log shows the most recent page only "
                        f"({pages} pages total)")

    def _fetch_token(self, time_range: TimeRange) -> list[PlantData]:
        pl = self._client.plant_list()
        if isinstance(pl, dict):
            plants = pl.get("plants") if pl.get("plants") is not None else pl.get("data", [])
        elif isinstance(pl, list):
            plants = pl
        else:
            plants = []
        results = []
        for plant in plants or []:
            if not isinstance(plant, dict):
                continue  # defensive: unexpected entry shape in plant/list
            pid = plant.get("plant_id") or plant.get("id")
            try:
                details = self._client.plant_details(pid)
                overview = self._client.plant_energy_overview(pid)
                devices = (self._client.device_list(pid) or {}).get("devices", [])
                pd = map_growatt_v1(details, overview, devices)
            except Exception as e:
                pd = map_growatt_v1({"plant_id": pid}, {}, [])
                pd.data_quality_flags.append(f"growatt: token fetch failed for this plant ({e})")
                results.append(pd)
                continue
            if time_range != TimeRange.SNAPSHOT:
                self._token_history(pd, pid, time_range)
            results.append(pd)
        return results

    def _token_history(self, pd: PlantData, pid, time_range: TimeRange) -> None:
        """Populate energy_timeseries from the v1 /plant/energy endpoint."""
        today = date.today()
        if time_range == TimeRange.LAST_30D:
            start, unit = today - timedelta(days=29), "day"
        elif time_range == TimeRange.LAST_12MO:
            start, unit = today.replace(year=today.year - 1), "month"
        else:
            first = (pd.install_date or "")[:10]
            try:
                start = date.fromisoformat(first)
            except ValueError:
                start = today.replace(year=today.year - 5)
            unit = "month"

        def call():
            data = self._client.plant_energy_history(
                pid, start.isoformat(), today.isoformat(), time_unit=unit)
            return map_growatt_v1_history(data, unit)
        pts = safe_step(pd, f"growatt: v1 energy history ({unit})", call)
        if pts:
            pd.energy_timeseries = pts
