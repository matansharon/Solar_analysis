import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import SolarPortalAdapter, get_adapter, AdapterError
from solaranalysis.core.schema import PlantData

def test_cannot_instantiate_abc(tmp_path):
    with pytest.raises(TypeError):
        SolarPortalAdapter(AuthConfig("x"), SessionStore(str(tmp_path)))

def test_factory_dispatch(tmp_path):
    ss = SessionStore(str(tmp_path))
    se = get_adapter(AuthConfig("solaredge", username="u", password="p"), ss)
    gw = get_adapter(AuthConfig("growatt", username="u", password="p"), ss)
    assert se.platform == "solaredge"
    assert gw.platform == "growatt"

def test_factory_unknown_raises(tmp_path):
    with pytest.raises(AdapterError, match="unknown"):
        get_adapter(AuthConfig("nope"), SessionStore(str(tmp_path)))

def test_session_key_is_per_account():
    from solaranalysis.config import AuthConfig
    from solaranalysis.core.session_store import SessionStore
    from solaranalysis.adapters.solaredge import SolarEdgeAdapter
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ss = SessionStore(d)
        a = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
        b = SolarEdgeAdapter(AuthConfig("solaredge", username="b@x.com", password="p"), ss)
        assert a._session_key() != b._session_key()
        assert a._session_key().startswith("solaredge:")


class _StubAdapter(SolarPortalAdapter):
    platform = "stub"
    def login(self): ...
    def verify_login(self): ...
    def fetch(self, time_range): return []


class _FakeBS:
    def __init__(self, recs):
        self._recs, self.started = recs, False
    def start_raw_capture(self):
        self.started = True
    def raw_records(self):
        return self._recs


def test_begin_raw_starts_only_when_enabled():
    a = _StubAdapter(None, None)
    a.record_raw = True
    bs = _FakeBS([])
    a._begin_raw(bs)
    assert bs.started is True

    b = _StubAdapter(None, None)  # record_raw defaults False
    bs2 = _FakeBS([])
    b._begin_raw(bs2)
    assert bs2.started is False


def test_finish_raw_attaches_to_first_result_when_enabled():
    a = _StubAdapter(None, None)
    a.record_raw = True
    bs = _FakeBS([{"url": "https://h/s/meas", "method": "GET",
                   "status": 200, "body": {"a": 1}}])
    r0 = PlantData("uid0", "stub", "0", "S0")
    r1 = PlantData("uid1", "stub", "1", "S1")
    a._finish_raw(bs, [r0, r1])
    assert len(r0.raw_payloads) == 1
    assert r0.raw_payloads[0].endpoint_label == "meas"
    assert r0.raw_payloads[0].body == {"a": 1}
    assert r1.raw_payloads == []  # attached to the first only


def test_finish_raw_routes_per_site_for_multi_site_accounts():
    a = _StubAdapter(None, None)
    a.record_raw = True
    bs = _FakeBS([
        {"url": "https://monitoring.solaredge.com/services/sitelist/sitesMeasurements",
         "method": "GET", "status": 200, "body": {"fleet": True}},
        {"url": "https://monitoring.solaredge.com/services/dashboard/live-power/sites/2257529",
         "method": "GET", "status": 200, "body": {"site": "2257529"}},
        {"url": "https://monitoring.solaredge.com/services/dashboard/live-power/sites/2387929",
         "method": "GET", "status": 200, "body": {"site": "2387929"}},
    ])
    r0 = PlantData("solaredge-2387929", "solaredge", "2387929", "S1")
    r1 = PlantData("solaredge-2257529", "solaredge", "2257529", "S2")
    a._finish_raw(bs, [r0, r1])
    assert len(r0.raw_payloads) == 2  # fleet + 2387929
    assert len(r1.raw_payloads) == 1  # 2257529
    assert r1.raw_payloads[0].url.endswith("2257529")


def test_finish_raw_noop_when_disabled_or_empty():
    a = _StubAdapter(None, None)  # disabled
    r0 = PlantData("uid0", "stub", "0", "S0")
    a._finish_raw(_FakeBS([{"url": "https://h/x", "body": {}}]), [r0])
    assert r0.raw_payloads == []

    a.record_raw = True
    a._finish_raw(_FakeBS([{"url": "https://h/x", "body": {}}]), [])  # empty results
    # nothing to assert beyond "does not raise"
