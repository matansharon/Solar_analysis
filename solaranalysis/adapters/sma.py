from __future__ import annotations
import os
import re
from datetime import date, timedelta

from ..core.schema import (
    PlantData, Metric, Device, DeviceStatus, Alert, AlertSeverity,
    EnergyPoint, TimeRange,
)
from ..core import units
from .base import SolarPortalAdapter, AdapterError
from ._common import safe_step, month_keys_for, clip_series

# ---------------------------------------------------------------------------
# SMA Sunny Portal (Classic) adapter.
#
# Sunny Portal Classic exposes no public JSON API. We log in via SMA ID
# (Keycloak SSO) with the owner's e-mail/password and read, within the
# authenticated session:
#
#   GET /Plants/GetPlantList?...   (verified 2026-07-07) -> DataTables JSON:
#        aaData:[{PlantOid,PlantName,PeakPowerValue,YieldTodayValue,
#                 YieldYesterdayValue,YieldMonthValue,YieldPreMonthValue,
#                 YieldTotalValue,PerformanceMonthValue,PerformanceYearValue}]
#        (kW / kWh / kWh-per-kWp; the numeric *Value twins avoid locale
#        parsing entirely). Falls back to scraping the HTML "PV System List
#        (Extended)" table if the JSON endpoint goes away.
#
# Per-plant drilldown (best-effort; set SOLAR_SMA_DRILLDOWN=0 to disable):
#   * /RedirectToPlant/<oid> selects the plant into the WebForms session.
#   * /FixedPages/EnergyAndPower.aspx renders the history chart as a
#     server-side PNG (no JSON), but its Download button returns a CSV of the
#     visible view. Tabs (Day/Month/Year/Total) switch granularity and the
#     prev-arrow steps one period back:
#       Month view CSV -> "M/D/YY;kWh" per day (rows in day order)
#       Year view CSV  -> "Jan YY;kWh" per month (rows in month order)
#       Total view CSV -> "YYYY;kWh" per year
#     Row order (not the locale-formatted date) is used for period keys.
#   * /Plants/<oid>/Log -> server-rendered logbook table
#     (table.plantLog: type-icon | time | device | description | confirmed).
#   * Left-menu "Device Overview" postback -> Templates/DeviceProperties.aspx
#     with table#DeviceDatagrid (device name | serial | product group).
#
# Extended-list columns (verified 2026-07-02), by position — fallback only:
#   0 name (+ /RedirectToPlant/<guid> link)   5 total yield this month [kWh]
#   1 PV system power [kW]                      6 total yield lifetime (meter) [kWh]
#   2 total yield yesterday [kWh]              7 specific yield this month [kWh/kWp]
#   3 total yield today [kWh]                  8 specific yield this year [kWh/kWp]
#   4 total yield last month [kWh]
# Cells read "No data" when a value is unavailable.
# ---------------------------------------------------------------------------

_HOST = "https://www.sunnyportal.com"
_PLANTS_URL = f"{_HOST}/Plants"
_LOGIN_BUTTON = "#ctl00_ContentPlaceHolder1_Logincontrol1_SmaIdLoginButton"
_EAP = "ctl00_ContentPlaceHolder1_UserControlShowEnergyAndPower1"
_DL_BUTTON = f"#{_EAP}_ImageButtonDownload"
_PREV_BUTTON = f"#{_EAP}_btn_prev"

from . import _browser


def _num(x):
    # Sunny Portal list renders '.'-decimal with ',' thousands separators for
    # this account's locale (verified 2026-07-02); a decimal-comma locale would
    # need different handling.
    return units.to_float(x, strip_commas=True, none_tokens=("no data",))


def _drilldown_enabled() -> bool:
    return os.environ.get("SOLAR_SMA_DRILLDOWN", "1").strip().lower() \
        not in ("0", "false", "no")


def _apply_sma_extras(pd: PlantData, *, yesterday, lastmonth, sy_month, sy_year):
    """Shared extras/derived-KPI fill for both list sources."""
    if yesterday is not None:
        pd.extras["yield_yesterday_kwh"] = yesterday
    if lastmonth is not None:
        pd.extras["yield_lastmonth_kwh"] = lastmonth
    if sy_month is not None:
        pd.extras["specific_yield_month_kwh_per_kwp"] = sy_month
    if sy_year is not None:
        pd.extras["specific_yield_year_kwh_per_kwp"] = sy_year
    kwp = pd.peak_power_kwp.value
    if sy_year is not None and kwp:
        # The list exposes only specific yield for the year; energy = sy × kWp.
        pd.energy_year_kwh = Metric(round(sy_year * kwp, 1), "kWh",
                                    is_derived=True)
        pd.data_quality_flags.append(
            "sma: year energy derived from specific yield × kWp")


def map_sma_row(row: dict) -> PlantData:
    """Pure mapper: one parsed PV-System-List HTML row -> PlantData."""
    row = row or {}
    guid = row.get("guid")
    pd = PlantData(
        plant_id=f"sma-{guid}",
        source_platform="sma",
        source_plant_id=str(guid) if guid is not None else "",
        plant_name=row.get("name") or "SMA plant",
        peak_power_kwp=Metric(_num(row.get("power_kw")), "kWp"),
    )
    pd.energy_today_kwh = Metric(_num(row.get("yield_today")), "kWh")
    pd.energy_month_kwh = Metric(_num(row.get("yield_thismonth")), "kWh")
    pd.energy_lifetime_kwh = Metric(_num(row.get("lifetime")), "kWh")
    # Sunny Portal's list gives only specific-yield (not energy) for the year;
    # _apply_sma_extras derives it when the column parses.
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(None, "kW", data_source_status="not_exposed")
    # No financials or CO2 in the list view.
    pd.co2_avoided_kg = Metric(None, "kg", data_source_status="not_exposed")
    pd.revenue = Metric(None, "currency", data_source_status="not_exposed")
    pd.data_quality_flags.append(
        "sma: values read from PV System List (Extended); today/month columns are position-based")
    _apply_sma_extras(pd,
                      yesterday=_num(row.get("yield_yesterday")),
                      lastmonth=_num(row.get("yield_lastmonth")),
                      sy_month=_num(row.get("sy_thismonth")),
                      sy_year=_num(row.get("sy_year")))
    return pd


def map_sma_list_row(item: dict) -> PlantData:
    """Pure mapper: one GetPlantList JSON row -> PlantData (preferred source:
    the *Value fields are numeric, no locale parsing)."""
    item = item or {}
    guid = item.get("PlantOid") or item.get("DT_RowId")
    pd = PlantData(
        plant_id=f"sma-{guid}",
        source_platform="sma",
        source_plant_id=str(guid) if guid is not None else "",
        plant_name=item.get("PlantName") or "SMA plant",
        peak_power_kwp=Metric(_num(item.get("PeakPowerValue")), "kWp"),
    )
    pd.energy_today_kwh = Metric(_num(item.get("YieldTodayValue")), "kWh")
    pd.energy_month_kwh = Metric(_num(item.get("YieldMonthValue")), "kWh")
    pd.energy_lifetime_kwh = Metric(_num(item.get("YieldTotalValue")), "kWh")
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(None, "kW", data_source_status="not_exposed")
    pd.co2_avoided_kg = Metric(None, "kg", data_source_status="not_exposed")
    pd.revenue = Metric(None, "currency", data_source_status="not_exposed")
    _apply_sma_extras(pd,
                      yesterday=_num(item.get("YieldYesterdayValue")),
                      lastmonth=_num(item.get("YieldPreMonthValue")),
                      sy_month=_num(item.get("PerformanceMonthValue")),
                      sy_year=_num(item.get("PerformanceYearValue")))
    return pd


# ---------------------------------------------------------------------------
# CSV / page mappers (pure)
# ---------------------------------------------------------------------------

def _csv_rows(text: str) -> list[tuple[str, float | None]]:
    """CSV body -> (label, value) rows; skips the header line and blanks."""
    rows = []
    for line in (text or "").splitlines()[1:]:
        if ";" not in line:
            continue
        label, _, val = line.partition(";")
        label = label.strip().lstrip("﻿")
        if not label:
            continue
        rows.append((label, _num(val)))
    return rows


def map_sma_month_csv(text: str, month_key: str) -> list[EnergyPoint]:
    """Month-view CSV -> daily EnergyPoints for 'YYYY-MM'.

    Rows arrive in day order (locale-formatted dates like '6/1/26' are
    ambiguous, so the row index is the day). Empty values (future days or
    comms gaps) are skipped.
    """
    pts = []
    for i, (_, v) in enumerate(_csv_rows(text)):
        if v is not None:
            pts.append(EnergyPoint(f"{month_key}-{i + 1:02d}", v, "day"))
    return pts


def map_sma_year_csv(text: str, year: str) -> list[EnergyPoint]:
    """Year-view CSV ('Jan YY;kWh' × 12, in month order) -> monthly points."""
    pts = []
    for i, (_, v) in enumerate(_csv_rows(text)):
        if i >= 12:
            break
        if v is not None:
            pts.append(EnergyPoint(f"{year}-{i + 1:02d}", v, "month"))
    return pts


def map_sma_total_csv(text: str) -> list[EnergyPoint]:
    """Total-view CSV ('YYYY;kWh') -> yearly points (label is the year)."""
    pts = []
    for label, v in _csv_rows(text):
        if v is not None and re.fullmatch(r"\d{4}", label):
            pts.append(EnergyPoint(label, v, "year"))
    return pts


_LOG_SEVERITY = {
    "info": AlertSeverity.INFO,
    "warning": AlertSeverity.WARNING,
    "error": AlertSeverity.ERROR,
    "failure": AlertSeverity.ERROR,
    "fault": AlertSeverity.ERROR,
    "disturbance": AlertSeverity.ERROR,
}


def map_sma_log_rows(rows: list[dict]) -> list[Alert]:
    """Parsed logbook rows ({id,type,time,device,description}) -> Alerts."""
    out = []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        desc = (r.get("description") or "").strip()
        device = (r.get("device") or "").strip()
        if not desc and not device:
            continue
        sev = _LOG_SEVERITY.get(str(r.get("type") or "").strip().lower(),
                                AlertSeverity.INFO)
        out.append(Alert(
            alert_id=str(r.get("id") or f"sma-log-{i + 1}"),
            severity=sev,
            code=None,
            message=f"{device}: {desc}" if device else desc,
            timestamp_local=(r.get("time") or "").strip() or None,
        ))
    return out


def map_sma_device_rows(rows: list[dict]) -> list[Device]:
    """Parsed DeviceDatagrid rows ({name,serial,product}) -> Devices."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("serial"):
            continue
        product = (r.get("product") or "").strip()
        is_logger = any(k in product.lower()
                        for k in ("webbox", "cluster controller", "com gateway"))
        out.append(Device(
            device_id=str(r.get("serial")),
            device_type="logger" if is_logger else "inverter",
            model=product or None,
            manufacturer="SMA",
            status=DeviceStatus.UNKNOWN,  # grid exposes no live status
        ))
    return out


class SMAAdapter(SolarPortalAdapter):
    """Headless SMA ID login, then read the Sunny Portal PV System List and
    (best-effort) per-plant history CSVs, logbook and device inventory."""

    platform = "sma"

    def login(self) -> None:
        if self.auth.mode != "password":
            raise AdapterError(f"sma: only mode=password is supported; got mode={self.auth.mode!r}")
        if not self.auth.username or not self.auth.password:
            raise AdapterError("sma: username/password not configured")

    def _parse_rows(self, page) -> list[dict]:
        rows = page.locator("table tbody tr")
        out = []
        for i in range(rows.count()):
            tds = rows.nth(i).locator("td")
            n = tds.count()
            if n < 7:
                continue  # not a data row
            link = tds.nth(0).locator("a")
            href = link.get_attribute("href") if link.count() else None
            guid = href.rstrip("/").split("/")[-1] if href else None
            vals = [tds.nth(j).inner_text().strip() for j in range(n)]
            out.append({
                "name": vals[0],
                "guid": guid,
                "power_kw": vals[1],
                "yield_yesterday": vals[2],
                "yield_today": vals[3],
                "yield_lastmonth": vals[4],
                "yield_thismonth": vals[5],
                "lifetime": vals[6],
                "sy_thismonth": vals[7] if n > 7 else None,
                "sy_year": vals[8] if n > 8 else None,
            })
        return out

    def _authenticate(self, bs, had_state: bool) -> None:
        bs.page.goto(_PLANTS_URL, wait_until="domcontentloaded")
        if bs.page.locator(_LOGIN_BUTTON).count():
            bs.page.locator(_LOGIN_BUTTON).click()
            bs.page.wait_for_url("**login.sma.energy**", timeout=30000)
            bs.page.get_by_role("textbox", name="E-mail or user name").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Log in").click()
            bs.page.wait_for_url("**sunnyportal.com/Plants**", timeout=45000)

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
            raise AdapterError(f"sma: login failed ({e})")

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        state = self._load_session()
        with _browser.BrowserSession(storage_state=state) as bs:
            self._authenticate(bs, had_state=bool(state))

            results = self._fetch_list(bs)
            self._save_session(bs)

            if _drilldown_enabled():
                for pd in results:
                    # Whole-plant drilldown is best-effort; the list-page data
                    # above is the guaranteed floor.
                    safe_step(pd, "sma: plant drilldown",
                              lambda pd=pd: self._drilldown(bs, pd, time_range))
            return results

    def _fetch_list(self, bs) -> list[PlantData]:
        """Plant list: JSON endpoint preferred, HTML table as fallback."""
        data = bs.get_json(
            f"{_HOST}/Plants/GetPlantList?sEcho=1&iColumns=9"
            f"&iDisplayStart=0&iDisplayLength=200")
        items = [x for x in ((data or {}).get("aaData") or [])
                 if isinstance(x, dict) and (x.get("PlantOid") or x.get("DT_RowId"))]
        if items:
            return [map_sma_list_row(x) for x in items]

        # Poll for the HTML data grid instead of a fixed sleep: the table can
        # render a few seconds after the URL settles.
        rows = []
        for _ in range(30):  # up to ~15s
            rows = self._parse_rows(bs.page)
            if rows:
                break
            bs.page.wait_for_timeout(500)
        if not rows:
            raise AdapterError("sma: PV System List did not load (JSON and table)")
        return [map_sma_row(r) for r in rows]

    # ---- per-plant drilldown -------------------------------------------

    def _drilldown(self, bs, pd: PlantData, time_range: TimeRange) -> None:
        guid = pd.source_plant_id
        if not guid:
            raise AdapterError("no plant guid")
        p = bs.page
        p.goto(f"{_HOST}/RedirectToPlant/{guid}", wait_until="domcontentloaded")
        p.wait_for_timeout(1500)

        if time_range != TimeRange.SNAPSHOT:
            safe_step(pd, "sma: energy history",
                      lambda: self._fetch_series(bs, pd, time_range))
        safe_step(pd, "sma: logbook", lambda: self._fetch_logbook(bs, pd, guid))
        safe_step(pd, "sma: device overview", lambda: self._fetch_devices(bs, pd))

    def _download_csv(self, p) -> str:
        p.wait_for_selector(_DL_BUTTON, state="attached", timeout=15000)
        # The button hides behind a collapsed toolbox; a DOM click still
        # submits the WebForms form, so bypass visibility checks.
        with p.expect_download(timeout=20000) as dl:
            p.eval_on_selector(_DL_BUTTON, "el => el.click()")
        path = dl.value.path()
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def _click_tab(self, p, label: str) -> None:
        link = p.locator(".tabs a", has_text=label)
        if link.count():  # no anchor = already the active tab
            link.first.click()
            p.wait_for_load_state("domcontentloaded")
            p.wait_for_timeout(2000)

    def _step_prev(self, p) -> None:
        p.eval_on_selector(_PREV_BUTTON, "el => el.click()")
        p.wait_for_load_state("domcontentloaded")
        p.wait_for_timeout(2000)

    def _fetch_series(self, bs, pd: PlantData, time_range: TimeRange) -> None:
        p = bs.page
        p.goto(f"{_HOST}/FixedPages/EnergyAndPower.aspx",
               wait_until="domcontentloaded")
        p.wait_for_timeout(1500)
        today = date.today()
        today_iso = today.isoformat()

        if time_range == TimeRange.LAST_30D:
            self._click_tab(p, "Month")
            keys = month_keys_for(time_range, today)  # [prev, current]
            pts = map_sma_month_csv(self._download_csv(p), keys[-1])
            if len(keys) > 1:
                self._step_prev(p)
                pts = map_sma_month_csv(self._download_csv(p), keys[-2]) + pts
            start = (today - timedelta(days=29)).isoformat()
            pd.energy_timeseries = clip_series(pts, start, today_iso)
        elif time_range == TimeRange.LAST_12MO:
            self._click_tab(p, "Year")
            cur = str(today.year)
            pts = map_sma_year_csv(self._download_csv(p), cur)
            self._step_prev(p)
            pts = map_sma_year_csv(self._download_csv(p), str(today.year - 1)) + pts
            start = f"{today.year - 1:04d}-{today.month:02d}"
            pd.energy_timeseries = clip_series(pts, start, today_iso[:7])
        else:  # ALL — the Total view has the whole life of the plant, yearly
            self._click_tab(p, "Total")
            pts = map_sma_total_csv(self._download_csv(p))
            pd.energy_timeseries = clip_series(pts, "0000", str(today.year))

    def _fetch_logbook(self, bs, pd: PlantData, guid: str) -> None:
        p = bs.page
        p.goto(f"{_HOST}/Plants/{guid}/Log", wait_until="domcontentloaded")
        p.wait_for_timeout(2000)
        if "/Log" not in (p.url or ""):
            raise AdapterError("logbook page did not load")
        rows = p.locator("table.plantLog tbody tr")
        parsed = []
        for i in range(min(rows.count(), 20)):
            tds = rows.nth(i).locator("td")
            if tds.count() < 4:
                continue
            icon = tds.nth(0).locator("img")
            parsed.append({
                "id": rows.nth(i).get_attribute("id"),
                "type": (icon.first.get_attribute("title") or
                         icon.first.get_attribute("alt")) if icon.count() else None,
                "time": tds.nth(1).inner_text(),
                "device": tds.nth(2).inner_text(),
                "description": tds.nth(3).inner_text(),
            })
        alerts = map_sma_log_rows(parsed)
        if alerts:
            pd.alerts = alerts
            pd.data_quality_flags.append(
                "sma: alerts read from the plant logbook (most recent page)")

    def _fetch_devices(self, bs, pd: PlantData) -> None:
        p = bs.page
        href = p.eval_on_selector("#lmiDeviceOverview a", "el => el.href")
        m = re.search(r"__doPostBack\('([^']+)'", href or "")
        if not m:
            raise AdapterError("device overview menu entry not found")
        p.evaluate(f"__doPostBack('{m.group(1)}','')")
        p.wait_for_load_state("domcontentloaded")
        p.wait_for_timeout(2000)
        rows = p.locator("#DeviceDatagrid tr")
        parsed = []
        for i in range(rows.count()):
            cells = [c.strip() for c in
                     rows.nth(i).locator("td").all_inner_texts()]
            cells = [c for c in cells if c and c != "\xa0"]
            if len(cells) < 3 or "Serial Number" in cells:
                continue  # header / spacer rows
            parsed.append({"name": cells[0], "serial": cells[1],
                           "product": cells[2]})
        devices = map_sma_device_rows(parsed)
        if devices:
            pd.devices = devices
            pd.data_quality_flags.append(
                "sma: device inventory from Device Overview (no live status)")
