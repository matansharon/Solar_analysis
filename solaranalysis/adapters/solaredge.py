from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta

from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, Alert, AlertSeverity,
    EnergyPoint, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError
from ._common import safe_step, clip_series

# ---------------------------------------------------------------------------
# SolarEdge adapter — authenticated-session scrape of monitoring.solaredge.com
#
# The public Monitoring API needs an installer-generated api_key that is not
# available for this account, so we log in with the site owner's e-mail and
# password (headless browser) and read the same internal JSON the dashboard
# fetches:
#   POST /services/sitelist/searchSites        -> fleet: metadata per site
#   POST /services/sitelist/sitesMeasurements  -> fleet: energy per site (kWh)
#   GET  /services/dashboard/environmental-benefits/sites/{id}  -> CO2, trees
#   GET  /services/dashboard/live-power/sites/{id}             -> current power (W)
# Per-site detail/history (verified live 2026-07-07):
#   GET /services/dashboard/v2/site-details/{id}/equipment
#        -> {inverters:{totalCount,details:[{modelName,serialNumbers[]}]},
#            optimizers:{totalCount}, ...}
#   GET /services/dashboard/site-details/{id}/communication
#        -> {connectivityStatus:"ONLINE",active:true}
#   GET /services/dashboard/alerts/sites/{id}
#        -> {totalAlertsCount, topAlerts:[...]}
#   GET /services/dashboard/comparative-energy/{id}/monthly
#        -> {janEnergyByYears:[{year,value(Wh)}], feb..., decEnergyByYears}
#   GET /services/dashboard/inverters/power/sites/{id}
#        ?chart-time-unit=hours&start-date&end-date&normalized=false&page-number=0
#        -> {invertersDatedPowerList:[{measurementTime,inverterPowerArray[W]}],
#            powerUnit}
#   GET /services/dashboard/data-availability/sites/{id}
#        -> {productionDataAvailableFrom:"YYYY-MM-DD", ...}
#   NOTE: /services/dashboard/energy/sites/{id} (the UI's own energy chart)
#   returns 500 server-side for this account — daily energy is therefore
#   INTEGRATED from the hourly inverter power series and flagged as derived.
#
# UNITS (verified against the live dashboard, 2026-07-02 & 2026-07-07):
#   * searchSites.peakPower        -> already kW           (no conversion)
#   * sitesMeasurements.energy*    -> already kWh          (no conversion)
#   * live-power.currentAcPower    -> W                    (-> kW)
#   * environmental-benefits.lifetimeCO2EmissionSaved -> kg
#   * comparative-energy values    -> Wh                   (-> kWh)
#   * inverters/power values       -> W (powerUnit field)  (-> kW)
# ---------------------------------------------------------------------------

_SEARCH = "sitelist/searchSites"
_MEAS = "sitelist/sitesMeasurements"
_BASE = "https://monitoring.solaredge.com"

from . import _browser


def _num(x):
    return units.to_float(x)


def _num2(x):
    """Numeric rounded to 2 dp (peakPower arrives as e.g. 208.39000000000001)."""
    v = _num(x)
    return None if v is None else round(v, 2)


def map_solaredge_fleet(site: dict, meas: dict, env: dict | None,
                        live: dict | None, today: "date | None" = None) -> PlantData:
    """Pure mapper: one site's raw dashboard payloads -> PlantData."""
    site = site or {}
    meas = meas or {}
    env = env or {}
    live = live or {}

    sid = site.get("solarFieldId")
    active = str(site.get("status", "")).upper() == "ACTIVE"

    pd = PlantData(
        plant_id=f"solaredge-{sid}",
        source_platform="solaredge",
        source_plant_id=str(sid) if sid is not None else "",
        plant_name=site.get("name") or "SolarEdge site",
        peak_power_kwp=Metric(_num2(site.get("peakPower")), "kWp"),  # already kW
        location_address=site.get("address"),
        location_country=site.get("country"),
        latitude=_num(site.get("latitude")),
        longitude=_num(site.get("longitude")),
        timezone=site.get("timeZone"),
        install_date=site.get("installationDate"),
        reporting_timestamp_utc=meas.get("lastReportingTime"),
    )

    # Energy — sitesMeasurements is already in kWh.
    pd.energy_today_kwh = Metric(_num(meas.get("energyToday")), "kWh")
    pd.energy_month_kwh = Metric(_num(meas.get("energyMonthly")), "kWh")
    pd.energy_year_kwh = Metric(_num(meas.get("energyYearly")), "kWh")
    pd.energy_lifetime_kwh = Metric(_num(meas.get("energyLifeTime")), "kWh")

    # Current power from live-power (W -> kW).
    cur = live.get("currentAcPower")
    pd.current_power_kw = Metric(units.w_to_kw(_num(cur)), "kW",
                                 data_source_status="ok" if cur is not None else "not_exposed")

    # Performance ratio: exposed by the dashboard but frequently null.
    pr = meas.get("prMonthly")
    if pr is not None:
        pd.performance_ratio = Metric(_num(pr), "ratio", is_derived=False)

    # Environmental benefits (lifetime).
    co2 = env.get("lifetimeCO2EmissionSaved")
    pd.co2_avoided_kg = Metric(_num(co2), "kg",
                               data_source_status="ok" if co2 is not None else "not_exposed")
    trees = env.get("lifetimeEquivalentTreesPlanted")
    pd.trees_equivalent = Metric(_num(trees), "count",
                                 data_source_status="ok" if trees is not None else "not_exposed")

    # SolarEdge fleet endpoints expose neither per-inverter serials nor revenue.
    pd.revenue = Metric(None, "currency", data_source_status="not_exposed")

    # Devices: only a count is available at fleet level. Represent that many
    # inverters; status is INFERRED from the site being ACTIVE (it is
    # communicating and reporting), which we flag as a derived judgement.
    inv_count = site.get("inverterCount") or 0
    try:
        inv_count = int(inv_count)
    except (TypeError, ValueError):
        inv_count = 0
    dev_status = DeviceStatus.ONLINE if active else DeviceStatus.UNKNOWN
    for i in range(inv_count):
        pd.devices.append(Device(
            device_id=f"{sid}-inverter-{i + 1}",
            device_type="inverter",
            manufacturer="SolarEdge",
            status=dev_status,
        ))
    if inv_count and active:
        pd.data_quality_flags.append(
            "solaredge: inverter online-status inferred from site ACTIVE state (no per-device status in fleet view)")

    # Alerts: fleet view gives a count only.
    alert_count = site.get("alertsCount") or 0
    try:
        alert_count = int(alert_count)
    except (TypeError, ValueError):
        alert_count = 0
    for i in range(alert_count):
        pd.alerts.append(Alert(
            alert_id=f"{sid}-alert-{i + 1}",
            severity=AlertSeverity.WARNING,
            message="SolarEdge site alert (count from fleet view; open portal for detail)",
        ))

    # A clean, complete previous-day energy point so a daily series accumulates
    # even on snapshot runs (energy_timeseries is otherwise only filled for
    # ranged runs). energyYesterday is already kWh, like the other counters.
    ey = _num(meas.get("energyYesterday"))
    if ey is not None:
        # yday uses server-local date.today() (not the site's own timezone)
        # when `today` isn't passed in; fine at the intended ~06:00 daily-run
        # cadence, where server-local and site-local "yesterday" agree.
        yday = ((today or date.today()) - timedelta(days=1)).isoformat()
        pd.energy_timeseries.append(EnergyPoint(yday, ey, "day"))

    return pd


_MONTH_KEYS = ("jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec")


def map_solaredge_equipment(equip: dict, comm: dict | None) -> list[Device]:
    """v2 equipment payload -> real inverters (model + serial). Per-inverter
    status is not exposed; site connectivity stands in for all of them."""
    comm_status = str((comm or {}).get("connectivityStatus") or "").upper()
    status = DeviceStatus.ONLINE if comm_status == "ONLINE" else DeviceStatus.UNKNOWN
    devs = []
    inv = (equip or {}).get("inverters") or {}
    for det in inv.get("details") or []:
        if not isinstance(det, dict):
            continue
        serials = det.get("serialNumbers") or []
        for sn in serials:
            devs.append(Device(
                device_id=str(sn),
                device_type="inverter",
                model=det.get("modelName"),
                manufacturer="SolarEdge",
                status=status,
            ))
    return devs


def map_solaredge_alerts(payload: dict) -> list[Alert]:
    """dashboard alerts payload ({totalAlertsCount, topAlerts}) -> Alerts."""
    sev_map = {"critical": AlertSeverity.CRITICAL, "high": AlertSeverity.ERROR,
               "medium": AlertSeverity.WARNING, "low": AlertSeverity.INFO}
    out = []
    for i, al in enumerate((payload or {}).get("topAlerts") or []):
        if not isinstance(al, dict):
            continue
        sev_raw = str(al.get("severity") or al.get("impact") or "").lower()
        out.append(Alert(
            alert_id=str(al.get("alertId") or al.get("id") or f"alert-{i + 1}"),
            severity=sev_map.get(sev_raw, AlertSeverity.WARNING),
            code=str(al.get("alertType") or al.get("code") or "") or None,
            message=al.get("title") or al.get("description") or al.get("alertName"),
            timestamp_local=al.get("openedDate") or al.get("date"),
        ))
    return out


def map_solaredge_comparative_monthly(payload: dict) -> list[EnergyPoint]:
    """comparative-energy/monthly -> monthly EnergyPoints across all years.

    Values are Wh (verified: 2025 total 367,060,320 Wh on a 208 kWp site).
    Months before install / after today arrive as 0.0 — callers clip.
    """
    pts = []
    for i, mn in enumerate(_MONTH_KEYS):
        for e in (payload or {}).get(f"{mn}EnergyByYears") or []:
            if not isinstance(e, dict):
                continue
            v = units.wh_to_kwh(_num(e.get("value")))
            y = e.get("year")
            if v is None or not isinstance(y, int):
                continue
            pts.append(EnergyPoint(f"{y:04d}-{i + 1:02d}", round(v, 3), "month"))
    return sorted(pts, key=lambda p: p.timestamp_local)


def map_solaredge_inverter_power_daily(payload: dict) -> list[EnergyPoint]:
    """Hourly per-inverter power -> daily kWh (rectangle integration).

    The portal's own energy-chart endpoint 500s, so daily energy is derived:
    sum the inverters per hourly sample × 1 h. Days whose samples are all
    null (comms gap) are skipped rather than reported as zero production.
    """
    payload = payload or {}
    scale = (1.0 if str(payload.get("powerUnit") or "W").upper().startswith("K")
             else 1e-3)
    daily: dict[str, float] = defaultdict(float)
    has_data: set[str] = set()
    for row in payload.get("invertersDatedPowerList") or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("measurementTime") or "")[:10]
        if len(day) != 10:
            continue
        vals = [v for v in row.get("inverterPowerArray") or []
                if isinstance(v, (int, float))]
        if vals:
            has_data.add(day)
        daily[day] += sum(vals) * scale
    return [EnergyPoint(d, round(daily[d], 3), "day")
            for d in sorted(daily) if d in has_data]


class SolarEdgeAdapter(SolarPortalAdapter):
    """Headless-browser login to monitoring.solaredge.com, then read the
    dashboard's internal JSON endpoints for every site on the account."""

    platform = "solaredge"

    def __init__(self, auth, session_store):
        super().__init__(auth, session_store)

    def login(self) -> None:
        if self.auth.mode != "password":
            raise AdapterError(
                "solaredge: only mode=password is supported (no api_key available); "
                f"got mode={self.auth.mode!r}")
        if not self.auth.username or not self.auth.password:
            raise AdapterError("solaredge: username/password not configured")

    def _authenticate(self, bs, had_state: bool) -> None:
        bs.page.goto(f"{_BASE}/", wait_until="domcontentloaded")
        logged_in = False
        if had_state:
            try:
                bs.page.wait_for_url("**/one#/site-list", timeout=10000)
                logged_in = True
            except Exception:
                logged_in = False
        if not logged_in:
            bs.page.get_by_role("button", name="Log in").click()
            bs.page.get_by_role("textbox", name="Email address").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Sign in").first.click()
            bs.page.wait_for_url("**/one#/site-list", timeout=45000)

    def verify_login(self) -> None:
        self.login()
        state = self._load_session()
        try:
            with _browser.BrowserSession(storage_state=state) as bs:
                self._authenticate(bs, had_state=bool(state))
                self._save_session(bs)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"solaredge: login failed ({e})")

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        state = self._load_session()
        with _browser.BrowserSession(storage_state=state) as bs:
            self._begin_raw(bs)
            store = bs.capture([_SEARCH, _MEAS])
            self._authenticate(bs, had_state=bool(state))

            # Poll until both fleet responses have arrived, rather than a fixed
            # wait: a slow sitesMeasurements would otherwise leave energy null
            # with no signal. searchSites and sitesMeasurements are independent.
            for _ in range(40):  # up to ~20s
                if store.get(_SEARCH) and store.get(_MEAS):
                    break
                bs.page.wait_for_timeout(500)

            search = store.get(_SEARCH)
            sites = search.get("page", []) if isinstance(search, dict) else []
            if not sites:
                raise AdapterError("solaredge: site list did not load (no searchSites response)")
            self._save_session(bs)
            raw_meas = store.get(_MEAS)
            meas_list = raw_meas if isinstance(raw_meas, list) else []
            meas_by_id = {m.get("solarFieldId"): m for m in meas_list if isinstance(m, dict)}
            meas_loaded = bool(meas_by_id)

            results = []
            for site in sites:
                if not isinstance(site, dict):
                    continue  # defensive: unexpected entry shape in the fleet list
                sid = site.get("solarFieldId")
                # Per-site enrichment AND mapping are best-effort: one site's
                # malformed payload must not discard the whole account (the
                # pipeline isolates per account, not per site).
                try:
                    env = bs.get_json(f"{_BASE}/services/dashboard/environmental-benefits/sites/{sid}")
                    live = bs.get_json(f"{_BASE}/services/dashboard/live-power/sites/{sid}")
                    env = env if isinstance(env, dict) else None
                    live = live if isinstance(live, dict) else None
                    pd = map_solaredge_fleet(site, meas_by_id.get(sid, {}), env, live)
                except Exception as e:
                    pd = map_solaredge_fleet(site, meas_by_id.get(sid, {}), None, None)
                    pd.data_quality_flags.append(
                        f"solaredge: per-site enrichment failed for this site ({e})")
                if not meas_loaded:
                    pd.data_quality_flags.append(
                        "solaredge: sitesMeasurements did not load in time; energy figures unavailable")
                # Deep fetch (real devices/alerts, history) — per-endpoint
                # best-effort via safe_step; never fails the site.
                self._enrich_site(bs, pd, sid, time_range)
                results.append(pd)
            self._finish_raw(bs, results)
            return results

    # ---- per-site deep fetch -------------------------------------------

    def _get_dict(self, bs, pd: PlantData, label: str, url: str):
        def call():
            r = bs.get_json(url)
            if not isinstance(r, dict):
                raise AdapterError("empty response")
            return r
        return safe_step(pd, label, call)

    def _enrich_site(self, bs, pd: PlantData, sid, time_range: TimeRange) -> None:
        dash = f"{_BASE}/services/dashboard"

        # Real inverter inventory (equipment) + site connectivity for status.
        equip = self._get_dict(bs, pd, "solaredge: equipment",
                               f"{dash}/v2/site-details/{sid}/equipment")
        comm = self._get_dict(bs, pd, "solaredge: communication",
                              f"{dash}/site-details/{sid}/communication")
        if equip is not None:
            devs = map_solaredge_equipment(equip, comm)
            if devs:
                pd.devices = devs
                pd.data_quality_flags.append(
                    "solaredge: inverter status taken from site connectivity "
                    "(no per-inverter status exposed)")
            opt = (equip.get("optimizers") or {}).get("totalCount")
            if opt is not None:
                pd.extras["optimizer_count"] = opt

        # Real alert detail replaces the count-based placeholders.
        al = self._get_dict(bs, pd, "solaredge: alerts",
                            f"{dash}/alerts/sites/{sid}")
        if al is not None:
            pd.alerts = map_solaredge_alerts(al)
            total = al.get("totalAlertsCount")
            if isinstance(total, int) and total > len(pd.alerts):
                pd.data_quality_flags.append(
                    f"solaredge: alert list shows top {len(pd.alerts)} of {total}")

        if time_range == TimeRange.SNAPSHOT:
            return

        today = date.today()
        today_iso = today.isoformat()
        avail = self._get_dict(bs, pd, "solaredge: data availability",
                               f"{dash}/data-availability/sites/{sid}")
        floor = str((avail or {}).get("productionDataAvailableFrom")
                    or pd.install_date or "")[:10] or None

        if time_range == TimeRange.LAST_30D:
            # The portal's energy chart endpoint is broken (500); integrate
            # the hourly inverter power series into daily kWh instead.
            start = (today - timedelta(days=29)).isoformat()
            if floor and floor > start:
                start = floor
            power = self._get_dict(
                bs, pd, "solaredge: inverter power history",
                f"{dash}/inverters/power/sites/{sid}?chart-time-unit=hours"
                f"&start-date={start}&end-date={today_iso}"
                f"&normalized=false&page-number=0")
            if power is not None:
                pts = map_solaredge_inverter_power_daily(power)
                if pts:
                    pd.energy_timeseries = clip_series(pts, start, today_iso)
                    pd.data_quality_flags.append(
                        "solaredge: daily energy integrated from hourly "
                        "inverter power (approximate; portal energy chart "
                        "endpoint unavailable)")
        else:  # 12mo / all — monthly history across years
            cm = self._get_dict(bs, pd, "solaredge: comparative energy",
                                f"{dash}/comparative-energy/{sid}/monthly")
            if cm is not None:
                pts = map_solaredge_comparative_monthly(cm)
                if time_range == TimeRange.LAST_12MO:
                    start = f"{today.year - 1:04d}-{today.month:02d}"
                    if floor and floor[:7] > start:
                        start = floor[:7]
                else:
                    start = floor[:7] if floor else "0000-01"
                pd.energy_timeseries = clip_series(pts, start, today_iso[:7])
