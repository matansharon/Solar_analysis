import json
import types
from pathlib import Path
import pytest
from solaranalysis.adapters.growatt import map_growatt_web, _goto_retry, GrowattAdapter
from solaranalysis.core.schema import DeviceStatus

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
