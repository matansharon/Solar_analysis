import datetime
import json
import types
from pathlib import Path
import pytest
import solaranalysis.adapters._browser as browser_mod
import solaranalysis.adapters.growatt as growatt_mod
from solaranalysis.adapters.growatt import (
    map_growatt_web, _goto_retry, GrowattAdapter,
    map_growatt_month_chart, map_growatt_year_chart, map_growatt_total_chart,
    map_growatt_faults, map_growatt_device_rows, map_growatt_v1_history,
)
from solaranalysis.config import AuthConfig
from solaranalysis.core.schema import DeviceStatus, AlertSeverity, TimeRange
from solaranalysis.core.session_store import SessionStore

FXDIR = Path(__file__).parent / "fixtures"
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))


def _mapped():
    return map_growatt_web(_fx("growatt_web_plant.json"), _fx("growatt_web_details.json"),
                           _fx("growatt_web_totals.json"), _fx("growatt_web_devices.json"))


def test_metadata_and_energy():
    pd = _mapped()
    assert pd.source_platform == "growatt"
    assert pd.source_plant_id == "10950561"
    assert pd.plant_name == "Elcam Baram"
    assert pd.peak_power_kwp.value == 50.0
    assert pd.energy_today_kwh.value == 314.2         # already kWh
    assert pd.energy_lifetime_kwh.value == 8414.0
    assert pd.currency == "NIS"


def test_month_year_and_current_power_not_exposed():
    pd = _mapped()
    assert pd.energy_month_kwh.data_source_status == "not_exposed"
    assert pd.energy_year_kwh.data_source_status == "not_exposed"
    assert pd.current_power_kw.data_source_status == "not_exposed"


def test_financial_and_environmental():
    pd = _mapped()
    assert pd.revenue.value == 15902.46
    assert pd.co2_avoided_kg.value == 3365.6
    assert pd.trees_equivalent.value == 463.0


def test_device_status_standby_at_night():
    pd = _mapped()
    assert len(pd.devices) == 1
    d = pd.devices[0]
    assert d.device_id == "MZHRF6K012"
    assert d.manufacturer == "Growatt"
    assert d.status == DeviceStatus.STANDBY          # status code "0"


def test_device_status_online_code_1():
    devs = _fx("growatt_web_devices.json")
    devs["max"][0][2] = "1"
    pd = map_growatt_web(_fx("growatt_web_plant.json"), _fx("growatt_web_details.json"),
                         _fx("growatt_web_totals.json"), devs)
    assert pd.devices[0].status == DeviceStatus.ONLINE


def test_defensive_against_empty():
    pd = map_growatt_web({}, {}, {}, {})
    assert pd.source_platform == "growatt"
    assert pd.energy_today_kwh.value is None
    assert pd.devices == []
    assert pd.alerts == []


class _FakePage:
    """Minimal page double: goto raises for the first ``fail_times`` calls."""
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.goto_calls = 0
        self.backoffs = 0

    def goto(self, url, **kwargs):
        self.goto_calls += 1
        if self.goto_calls <= self.fail_times:
            raise Exception("net::ERR_TIMED_OUT")

    def wait_for_timeout(self, ms):
        self.backoffs += 1


def test_goto_retry_recovers_after_transient_timeout():
    # Two transient failures, then success -> should not raise (this is the
    # real-world Growatt partial-outage pattern the fix targets).
    page = _FakePage(fail_times=2)
    _goto_retry(page, "https://server.growatt.com/login")
    assert page.goto_calls == 3
    assert page.backoffs == 2


def test_goto_retry_gives_up_after_all_attempts():
    page = _FakePage(fail_times=99)
    with pytest.raises(Exception, match="ERR_TIMED_OUT"):
        _goto_retry(page, "https://server.growatt.com/login", attempts=3)
    assert page.goto_calls == 3


class _FakeLocator:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def click(self, **kw):
        if self.name == "Login":
            self.page.login_clicks += 1
        elif self.name == "Agree":
            self.page.agree_clicks += 1

    def fill(self, *a, **kw): pass


class _FakeAuthPage:
    """Page double for _authenticate: records wait_for_url kwargs and models
    the live consent-banner race — the post-login navigation only happens
    once Login has been clicked ``login_clicks_needed`` times."""
    def __init__(self, login_clicks_needed=1):
        self.wait_for_url_calls = []
        self.login_clicks = 0
        self.agree_clicks = 0
        self.login_clicks_needed = login_clicks_needed

    def goto(self, url, **kwargs): pass

    def get_by_role(self, role, name=None, **kw):
        return _FakeLocator(self, name)

    def wait_for_url(self, pattern, **kwargs):
        self.wait_for_url_calls.append((pattern, kwargs))
        if self.login_clicks < self.login_clicks_needed:
            raise Exception("Timeout 15000ms exceeded waiting for **/index**")


def _auth_adapter():
    auth = types.SimpleNamespace(mode="password", username="u",
                                 password="p", token=None)
    return GrowattAdapter(auth, session_store=None)


def test_authenticate_login_wait_does_not_require_load_event():
    # server.growatt.com's dashboard routinely never fires 'load' within the
    # timeout (see _goto_retry docstring); the post-login wait must key off
    # the navigation commit, not the full page load.
    page = _FakeAuthPage()
    bs = types.SimpleNamespace(page=page)
    _auth_adapter()._authenticate(bs, had_state=False)
    (pattern, kwargs), = page.wait_for_url_calls
    assert pattern == "**/index**"
    assert kwargs.get("wait_until") == "commit"


def test_authenticate_had_state_check_does_not_require_load_event():
    page = _FakeAuthPage()
    bs = types.SimpleNamespace(page=page)
    _auth_adapter()._authenticate(bs, had_state=True)
    pattern, kwargs = page.wait_for_url_calls[0]
    assert pattern == "**/index**"
    assert kwargs.get("wait_until") == "commit"


def test_login_retries_when_first_click_is_swallowed():
    # Live-observed race: clicking Agree before the consent banner's handler
    # binds leaves the banner up, and the portal's Login click then submits
    # nothing. A second Agree+Login pass in the same session succeeds.
    page = _FakeAuthPage(login_clicks_needed=2)
    bs = types.SimpleNamespace(page=page)
    _auth_adapter()._authenticate(bs, had_state=False)
    assert page.login_clicks == 2
    assert page.agree_clicks >= 2


def test_login_gives_up_after_bounded_attempts():
    page = _FakeAuthPage(login_clicks_needed=99)
    bs = types.SimpleNamespace(page=page)
    with pytest.raises(Exception, match="Timeout"):
        _auth_adapter()._authenticate(bs, had_state=False)
    assert page.login_clicks == 3


# ---------------------------------------------------------------------------
# Deep-fetch pure mappers (history charts, fault log, device list, v1 history)
# ---------------------------------------------------------------------------

class _FakeDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 7)


def test_month_chart_maps_calendar_days():
    pts = map_growatt_month_chart(_fx("growatt_web_month_chart.json")["obj"], "2026-07")
    assert len(pts) == 31  # whole calendar month; clipping is the caller's job
    assert pts[0].timestamp_local == "2026-07-01" and pts[0].energy_kwh == 313.2
    assert pts[6].energy_kwh == 247.1
    assert all(p.granularity == "day" for p in pts)


def test_month_chart_respects_short_months():
    obj = {"energy": [1.0] * 31}
    assert len(map_growatt_month_chart(obj, "2026-06")) == 30
    assert len(map_growatt_month_chart(obj, "2026-02")) == 28


def test_year_chart_maps_months():
    pts = map_growatt_year_chart(_fx("growatt_web_year_chart.json")["obj"], "2026")
    assert len(pts) == 12
    assert pts[5].timestamp_local == "2026-06" and pts[5].energy_kwh == 7786.6
    assert all(p.granularity == "month" for p in pts)


def test_total_chart_window_ends_at_requested_year():
    pts = map_growatt_total_chart(_fx("growatt_web_total_chart.json")["obj"], 2026)
    assert [p.timestamp_local for p in pts] == ["2022", "2023", "2024", "2025", "2026"]
    assert pts[-1].energy_kwh == 9807.599853515625
    assert all(p.granularity == "year" for p in pts)


def test_faults_map_severity_and_resolution():
    alerts = map_growatt_faults(_fx("growatt_web_faultlog.json")["obj"])
    assert len(alerts) == 2
    warn, fault = alerts
    assert warn.severity == AlertSeverity.WARNING
    assert warn.code == "Warning 106" and warn.message == "SPD abnormal"
    assert warn.timestamp_local == "2026-07-07 15:02:00"
    assert warn.resolved is False
    assert fault.severity == AlertSeverity.ERROR
    assert fault.resolved is True


def test_device_rows_map_real_inventory():
    devs = map_growatt_device_rows(
        _fx("growatt_web_devices_list.json")["obj"]["datas"])
    assert len(devs) == 1
    d = devs[0]
    assert d.device_id == "SN-TEST-1"
    assert d.model == "MAX 70KTL3 LV"
    assert d.status == DeviceStatus.ONLINE
    assert d.current_power_kw == 5.459  # pac is W
    assert d.energy_lifetime_kwh == 9807.6
    assert d.last_seen_local == "2026-07-07 15:02:01"


def test_v1_history_maps_and_sorts():
    data = {"energys": [{"date": "2026-07-02", "energy": "8.1"},
                        {"date": "2026-07-01", "energy": "12.5"}, "junk"]}
    pts = map_growatt_v1_history(data, "day")
    assert [p.timestamp_local for p in pts] == ["2026-07-01", "2026-07-02"]
    assert pts[0].energy_kwh == 12.5


def test_deep_mappers_defensive_against_empty_payloads():
    assert map_growatt_month_chart({}, "2026-07") == []
    assert map_growatt_month_chart(None, "bogus") == []
    assert map_growatt_year_chart(None, "2026") == []
    assert map_growatt_total_chart({}, 2026) == []
    assert map_growatt_faults(None) == []
    assert map_growatt_device_rows(None) == []
    assert map_growatt_v1_history(None, "day") == []


# ---------------------------------------------------------------------------
# Deep-fetch orchestration through the fake BrowserSession
# ---------------------------------------------------------------------------

def _deep_json_map(month_calls=None):
    def month_chart():
        if month_calls is not None:
            month_calls.append(1)
        return _fx("growatt_web_month_chart.json")
    # NB: "getDevicesByPlantList" must precede "getDevicesByPlant" — the fake
    # matches by first substring hit, mirroring the real URL overlap.
    return {
        "getDevicesByPlantList": _fx("growatt_web_devices_list.json"),
        "getPlantData": {"obj": _fx("growatt_web_details.json")},
        "getMAXTotalData": {"obj": _fx("growatt_web_totals.json")},
        "getDevicesByPlant": {"obj": _fx("growatt_web_devices.json")},
        "getMAXMonthChart": month_chart,
        "getMAXYearChart": _fx("growatt_web_year_chart.json"),
        "getMAXTotalChart": _fx("growatt_web_total_chart.json"),
        "getNewPlantFaultLog": _fx("growatt_web_faultlog.json"),
    }


def _deep_fetch(tmp_path, monkeypatch, time_range, json_map):
    from test_adapter_fetch import make_fake_bs
    fake = make_fake_bs(
        captured={"getPlantListTitle": [_fx("growatt_web_plant.json")]},
        json_map=json_map)
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    monkeypatch.setattr(growatt_mod, "date", _FakeDate)
    ss = SessionStore(str(tmp_path))
    adapter = GrowattAdapter(AuthConfig("growatt", username="u", password="p"), ss)
    results = adapter.fetch(time_range)
    assert len(results) == 1
    return results[0]


def test_growatt_30d_populates_daily_series(tmp_path, monkeypatch):
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.LAST_30D, _deep_json_map())
    # window 2026-06-08..2026-07-07 (install 2026-06-05 doesn't clip further)
    assert len(pd.energy_timeseries) == 30
    assert all(p.granularity == "day" for p in pd.energy_timeseries)
    assert pd.energy_timeseries[0].timestamp_local == "2026-06-08"
    assert pd.energy_timeseries[-1].timestamp_local == "2026-07-07"
    assert pd.energy_timeseries[-1].energy_kwh == 247.1


def test_growatt_12mo_populates_monthly_series(tmp_path, monkeypatch):
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.LAST_12MO, _deep_json_map())
    # 2025-07..2026-07 inclusive = 13 monthly points
    assert len(pd.energy_timeseries) == 13
    assert all(p.granularity == "month" for p in pd.energy_timeseries)
    assert pd.energy_timeseries[0].timestamp_local == "2025-07"
    assert pd.energy_timeseries[-1].timestamp_local == "2026-07"


def test_growatt_all_clips_series_to_install_date(tmp_path, monkeypatch):
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.ALL, _deep_json_map())
    # install (creatDate) 2026-06-05 -> monthly points from 2026-06
    assert [p.timestamp_local for p in pd.energy_timeseries] == ["2026-06", "2026-07"]
    assert pd.energy_timeseries[0].energy_kwh == 7786.6


def test_growatt_deep_fetch_fills_kpis_devices_alerts(tmp_path, monkeypatch):
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.SNAPSHOT, _deep_json_map())
    assert pd.energy_month_kwh.value == 2021.0        # devices-list eMonth
    assert pd.energy_year_kwh.value == 9807.6         # year-chart sum
    assert pd.energy_year_kwh.is_derived is True
    assert pd.current_power_kw.value == 5.459         # summed pac (W -> kW)
    assert pd.extras["revenue_today"] == 593.84
    assert pd.devices[0].model == "MAX 70KTL3 LV"     # real inventory replaced tuples
    assert len(pd.alerts) == 2                        # real fault log
    assert any("fault log shows the most recent page" in f
               for f in pd.data_quality_flags)


def test_growatt_snapshot_skips_month_charts(tmp_path, monkeypatch):
    calls = []
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.SNAPSHOT,
                     _deep_json_map(month_calls=calls))
    assert calls == []
    assert pd.energy_timeseries == []


def test_growatt_degrades_when_deep_endpoints_break(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("portal hiccup")
    jm = _deep_json_map()
    for frag in ("getDevicesByPlantList", "getMAXMonthChart", "getMAXYearChart",
                 "getNewPlantFaultLog"):
        jm[frag] = boom
    pd = _deep_fetch(tmp_path, monkeypatch, TimeRange.LAST_30D, jm)
    # Baseline snapshot behavior survives untouched.
    assert pd.plant_name == "Elcam Baram"
    assert pd.energy_today_kwh.value == 314.2
    assert pd.energy_timeseries == []
    assert pd.energy_month_kwh.data_source_status == "not_exposed"
    assert any("MonthChart" in f and "unavailable" in f for f in pd.data_quality_flags)
    assert any("fault log" in f for f in pd.data_quality_flags)
