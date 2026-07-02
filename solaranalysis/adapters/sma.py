from __future__ import annotations
from ..core.schema import PlantData, Metric, TimeRange
from .base import SolarPortalAdapter, AdapterError

# ---------------------------------------------------------------------------
# SMA Sunny Portal (Classic) adapter.
#
# Sunny Portal Classic exposes no public JSON API. We log in via SMA ID
# (Keycloak SSO) with the owner's e-mail/password and read the server-rendered
# "PV System List" table (Extended list) within the authenticated session —
# a stable data grid (it even offers a CSV download), not a live dashboard UI.
#
# Extended-list columns (verified 2026-07-02), by position:
#   0 name (+ /RedirectToPlant/<guid> link)   5 total yield this month [kWh]
#   1 PV system power [kW]                      6 total yield lifetime (meter) [kWh]
#   2 total yield yesterday [kWh]              7 specific yield this month [kWh/kWp]
#   3 total yield today [kWh]                  8 specific yield this year [kWh/kWp]
#   4 total yield last month [kWh]
# Cells read "No data" when a value is unavailable.
# ---------------------------------------------------------------------------

_PLANTS_URL = "https://www.sunnyportal.com/Plants"
_LOGIN_BUTTON = "#ctl00_ContentPlaceHolder1_Logincontrol1_SmaIdLoginButton"


def _num(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if s == "" or s.lower() == "no data":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def map_sma_row(row: dict) -> PlantData:
    """Pure mapper: one parsed PV-System-List row -> PlantData."""
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
    # Sunny Portal's list gives only specific-yield (not energy) for the year.
    pd.energy_year_kwh = Metric(None, "kWh", data_source_status="not_exposed")
    pd.current_power_kw = Metric(None, "kW", data_source_status="not_exposed")
    # No device inventory, alerts, financials or CO2 in the list view.
    pd.co2_avoided_kg = Metric(None, "kg", data_source_status="not_exposed")
    pd.revenue = Metric(None, "currency", data_source_status="not_exposed")
    pd.data_quality_flags.append(
        "sma: values read from PV System List (Extended); today/month columns are position-based")
    return pd


class SMAAdapter(SolarPortalAdapter):
    """Headless SMA ID login, then read the Sunny Portal PV System List table."""

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

    def fetch(self, time_range: TimeRange) -> list[PlantData]:
        self.login()
        from ._browser import BrowserSession
        with BrowserSession() as bs:
            bs.page.goto(_PLANTS_URL, wait_until="domcontentloaded")
            # If not authenticated, /Plants redirects to a login page carrying
            # the SMA ID button; click it to reach the Keycloak form.
            if bs.page.locator(_LOGIN_BUTTON).count():
                bs.page.locator(_LOGIN_BUTTON).click()
                bs.page.wait_for_url("**login.sma.energy**", timeout=30000)
                bs.page.get_by_role("textbox", name="E-mail or user name").fill(self.auth.username)
                bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
                bs.page.get_by_role("button", name="Log in").click()
                bs.page.wait_for_url("**sunnyportal.com/Plants**", timeout=45000)
                bs.page.wait_for_timeout(3000)

            rows = self._parse_rows(bs.page)
            if not rows:
                raise AdapterError("sma: PV System List table did not load")
            return [map_sma_row(r) for r in rows]
