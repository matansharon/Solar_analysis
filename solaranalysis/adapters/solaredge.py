from __future__ import annotations
from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, Alert, AlertSeverity, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError

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
#
# UNITS (verified against the live dashboard, 2026-07-02):
#   * searchSites.peakPower        -> already kW           (no conversion)
#   * sitesMeasurements.energy*    -> already kWh          (no conversion)
#   * live-power.currentAcPower    -> W                    (-> kW)
#   * environmental-benefits.lifetimeCO2EmissionSaved -> kg
# ---------------------------------------------------------------------------

_SEARCH = "sitelist/searchSites"
_MEAS = "sitelist/sitesMeasurements"
_BASE = "https://monitoring.solaredge.com"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _num2(x):
    """Numeric rounded to 2 dp (peakPower arrives as e.g. 208.39000000000001)."""
    v = _num(x)
    return None if v is None else round(v, 2)


def map_solaredge_fleet(site: dict, meas: dict, env: dict | None,
                        live: dict | None) -> PlantData:
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

    return pd


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

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        from ._browser import BrowserSession
        with BrowserSession() as bs:
            store = bs.capture([_SEARCH, _MEAS])
            bs.page.goto(f"{_BASE}/", wait_until="domcontentloaded")
            bs.page.get_by_role("button", name="Log in").click()
            bs.page.get_by_role("textbox", name="Email address").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Sign in").first.click()
            bs.page.wait_for_url("**/one#/site-list", timeout=45000)

            # Poll until both fleet responses have arrived, rather than a fixed
            # wait: a slow sitesMeasurements would otherwise leave energy null
            # with no signal. searchSites and sitesMeasurements are independent.
            for _ in range(40):  # up to ~20s
                if store.get(_SEARCH) and store.get(_MEAS):
                    break
                bs.page.wait_for_timeout(500)

            sites = (store.get(_SEARCH) or {}).get("page", [])
            if not sites:
                raise AdapterError("solaredge: site list did not load (no searchSites response)")
            meas_by_id = {m.get("solarFieldId"): m for m in (store.get(_MEAS) or [])}
            meas_loaded = bool(meas_by_id)

            results = []
            for site in sites:
                sid = site.get("solarFieldId")
                # Per-site enrichment is best-effort: a transient failure on one
                # site must not discard the whole account (the pipeline isolates
                # per account, not per site).
                try:
                    env = bs.get_json(f"{_BASE}/services/dashboard/environmental-benefits/sites/{sid}")
                except Exception:
                    env = None
                try:
                    live = bs.get_json(f"{_BASE}/services/dashboard/live-power/sites/{sid}")
                except Exception:
                    live = None
                pd = map_solaredge_fleet(site, meas_by_id.get(sid, {}), env, live)
                if not meas_loaded:
                    pd.data_quality_flags.append(
                        "solaredge: sitesMeasurements did not load in time; energy figures unavailable")
                results.append(pd)
            return results
