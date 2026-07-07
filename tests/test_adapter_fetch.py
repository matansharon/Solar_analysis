"""Orchestration tests for the adapter fetch() paths, driven through a fake
BrowserSession — covers per-site isolation, session reuse and the SMA table
poll without touching a real browser."""
import json
from pathlib import Path

import solaranalysis.adapters._browser as browser_mod
from solaranalysis.adapters.growatt import GrowattAdapter
from solaranalysis.adapters.sma import SMAAdapter
from solaranalysis.adapters.solaredge import SolarEdgeAdapter, _SEARCH, _MEAS
from solaranalysis.config import AuthConfig
from solaranalysis.core.schema import TimeRange
from solaranalysis.core.session_store import SessionStore

FXDIR = Path(__file__).parent / "fixtures"


def _fx(name):
    return json.loads((FXDIR / name).read_text(encoding="utf-8"))


FAKE_STATE_OUT = {"cookies": [{"name": "sess", "value": "fresh"}]}


class FakeElement:
    def __init__(self, count=0, href=None):
        self._count = count
        self._href = href
        self.first = self

    def count(self):
        return self._count

    def click(self, timeout=None):
        pass

    def fill(self, value):
        pass

    def get_attribute(self, name):
        return self._href


def make_fake_bs(*, captured=None, json_map=None, locator_factory=None,
                 wait_url_raises=False):
    """Build a BrowserSession stand-in class configured for one test."""

    class FakePage:
        def __init__(self, bs):
            self._bs = bs

        def goto(self, url, **kw):
            self._bs.actions.append(("goto", url))

        def wait_for_url(self, pattern, timeout=None, **kw):
            self._bs.actions.append(("wait_url", pattern))
            if wait_url_raises:
                raise TimeoutError("wait_for_url timed out")

        def wait_for_timeout(self, ms):
            self._bs.poll_count += 1

        def get_by_role(self, role, name=None):
            return FakeElement()

        def locator(self, sel):
            if locator_factory is not None:
                return locator_factory(self._bs, sel)
            return FakeElement()

    class FakeBS:
        instances = []

        def __init__(self, storage_state=None, **kw):
            self.storage_state_in = storage_state
            self.actions = []
            self.poll_count = 0
            self.page = FakePage(self)
            type(self).instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def _lookup(self, url):
            for frag, val in (json_map or {}).items():
                if frag in url:
                    return val() if callable(val) else val
            return None

        def get_json(self, url):
            return self._lookup(url)

        def post_json(self, url, **kw):
            return self._lookup(url)

        def capture(self, fragments):
            return dict(captured or {})

        def storage_state(self):
            return FAKE_STATE_OUT

    return FakeBS


# ---------------------------------------------------------------------------
# SolarEdge
# ---------------------------------------------------------------------------

def _solaredge_captured(extra_sites=()):
    return {_SEARCH: {"page": [_fx("solaredge_site.json"), *extra_sites]},
            _MEAS: [_fx("solaredge_meas.json")]}


def _solaredge_adapter(tmp_path):
    ss = SessionStore(str(tmp_path))
    return SolarEdgeAdapter(AuthConfig("solaredge", username="u", password="p"), ss), ss


def test_solaredge_survives_non_dict_enrichment_payload(tmp_path, monkeypatch):
    # A 200 response whose JSON body is not a dict must degrade that metric,
    # not abort the whole account.
    fake = make_fake_bs(captured=_solaredge_captured(),
                        json_map={"environmental-benefits": ["weird"],
                                  "live-power": ["weird"]})
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, _ = _solaredge_adapter(tmp_path)
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1
    assert results[0].energy_today_kwh.value == 1393.099  # meas still mapped
    assert results[0].co2_avoided_kg.data_source_status == "not_exposed"


def test_solaredge_flags_site_when_enrichment_raises(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("portal hiccup")
    fake = make_fake_bs(captured=_solaredge_captured(),
                        json_map={"environmental-benefits": boom,
                                  "live-power": boom})
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, _ = _solaredge_adapter(tmp_path)
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1
    assert any("enrichment failed" in f for f in results[0].data_quality_flags)


def test_solaredge_skips_non_dict_site_entries(tmp_path, monkeypatch):
    fake = make_fake_bs(captured=_solaredge_captured(extra_sites=("junk", 42)))
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, _ = _solaredge_adapter(tmp_path)
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1  # junk entries skipped, good site survives


def test_solaredge_saves_and_reuses_session(tmp_path, monkeypatch):
    fake = make_fake_bs(captured=_solaredge_captured())
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, ss = _solaredge_adapter(tmp_path)
    adapter.fetch(TimeRange.SNAPSHOT)
    assert ss.load(adapter._session_key()) == FAKE_STATE_OUT     # persisted for next run
    adapter.fetch(TimeRange.SNAPSHOT)
    assert fake.instances[-1].storage_state_in == FAKE_STATE_OUT  # restored


# ---------------------------------------------------------------------------
# Growatt (web/password mode)
# ---------------------------------------------------------------------------

def _growatt_json_map():
    return {"getPlantData": {"obj": _fx("growatt_web_details.json")},
            "getMAXTotalData": {"obj": _fx("growatt_web_totals.json")},
            "getDevicesByPlant": {"obj": _fx("growatt_web_devices.json")}}


def _growatt_adapter(tmp_path):
    ss = SessionStore(str(tmp_path))
    return GrowattAdapter(AuthConfig("growatt", username="u", password="p"), ss), ss


def test_growatt_web_skips_non_dict_plant_entries(tmp_path, monkeypatch):
    fake = make_fake_bs(
        captured={"getPlantListTitle": [_fx("growatt_web_plant.json"), 123]},
        json_map=_growatt_json_map())
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, _ = _growatt_adapter(tmp_path)
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1
    assert results[0].plant_name == "Elcam Baram"


def test_growatt_web_saves_and_reuses_session(tmp_path, monkeypatch):
    fake = make_fake_bs(
        captured={"getPlantListTitle": [_fx("growatt_web_plant.json")]},
        json_map=_growatt_json_map())
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    adapter, ss = _growatt_adapter(tmp_path)
    adapter.fetch(TimeRange.SNAPSHOT)
    assert ss.load(adapter._session_key()) == FAKE_STATE_OUT
    adapter.fetch(TimeRange.SNAPSHOT)
    assert fake.instances[-1].storage_state_in == FAKE_STATE_OUT


# ---------------------------------------------------------------------------
# Growatt (token mode)
# ---------------------------------------------------------------------------

class FakeV1Client:
    def plant_list(self):
        return {"plants": [{"plant_id": 9001, "name": "Roof"}, "junk"]}

    def plant_details(self, pid):
        return _fx("growatt_v1_details.json")

    def plant_energy_overview(self, pid):
        return _fx("growatt_v1_overview.json")

    def device_list(self, pid):
        return _fx("growatt_v1_devices.json")


def test_growatt_token_skips_non_dict_plant_entries(tmp_path):
    ss = SessionStore(str(tmp_path))
    adapter = GrowattAdapter(AuthConfig("growatt", mode="token", token="T"), ss,
                             client=FakeV1Client())
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1
    assert results[0].plant_name == "Growatt Roof"


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------

def _sma_row():
    name_td = FakeElement(count=1, href="/RedirectToPlant/GUID-1")

    class Td:
        def __init__(self, text, link=None):
            self._text = text
            self._link = link

        def inner_text(self):
            return self._text

        def locator(self, sel):
            return self._link or FakeElement(count=0)

    tds = [Td("Barn Roof", name_td), Td("50.5"), Td("10"), Td("3.5"),
           Td("100"), Td("1,234.5"), Td("8,414"), Td("24"), Td("800")]

    class Tds:
        def count(self):
            return len(tds)

        def nth(self, i):
            return tds[i]

    class Row:
        def locator(self, sel):
            return Tds()

    return Row()


def _sma_locator_factory(rows_after_polls):
    row = _sma_row()

    class Rows:
        def __init__(self, bs):
            self._bs = bs

        def count(self):
            return 1 if self._bs.poll_count >= rows_after_polls else 0

        def nth(self, i):
            return row

    def factory(bs, sel):
        if "tbody" in sel:
            return Rows(bs)
        return FakeElement(count=0)  # login button absent: already signed in

    return factory


def test_sma_polls_until_table_renders(tmp_path, monkeypatch):
    # The data grid can render seconds after the URL settles; fetch must poll
    # rather than give up after a fixed sleep.
    fake = make_fake_bs(locator_factory=_sma_locator_factory(rows_after_polls=2))
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    ss = SessionStore(str(tmp_path))
    adapter = SMAAdapter(AuthConfig("sma", username="u", password="p"), ss)
    results = adapter.fetch(TimeRange.SNAPSHOT)
    assert len(results) == 1
    pd = results[0]
    assert pd.plant_name == "Barn Roof"
    assert pd.source_plant_id == "GUID-1"
    assert pd.energy_today_kwh.value == 3.5
    assert pd.energy_month_kwh.value == 1234.5
    assert pd.energy_lifetime_kwh.value == 8414.0


def test_sma_saves_session_after_fetch(tmp_path, monkeypatch):
    fake = make_fake_bs(locator_factory=_sma_locator_factory(rows_after_polls=0))
    monkeypatch.setattr(browser_mod, "BrowserSession", fake)
    ss = SessionStore(str(tmp_path))
    adapter = SMAAdapter(AuthConfig("sma", username="u", password="p"), ss)
    adapter.fetch(TimeRange.SNAPSHOT)
    assert ss.load(adapter._session_key()) == FAKE_STATE_OUT
