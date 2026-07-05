import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import AdapterError
from solaranalysis.adapters.solaredge import SolarEdgeAdapter
from solaranalysis.adapters.growatt import GrowattAdapter
from solaranalysis.adapters._growatt_v1 import GrowattV1Error


class FakeBS:
    """Stands in for BrowserSession; records that _authenticate ran."""
    def __init__(self, fail=False):
        self.fail = fail
        self.authed = False
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def storage_state(self): return {"cookie": "x"}


def test_verify_login_rejects_bad_config(tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username=None, password=None), ss)
    with pytest.raises(AdapterError):
        ad.verify_login()


def test_verify_login_success(monkeypatch, tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
    fake = FakeBS()
    monkeypatch.setattr("solaranalysis.adapters._browser.BrowserSession",
                        lambda **k: fake)
    monkeypatch.setattr(ad, "_authenticate", lambda bs, had_state: None)
    ad.verify_login()  # must not raise


def test_verify_login_propagates_auth_failure(monkeypatch, tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
    monkeypatch.setattr("solaranalysis.adapters._browser.BrowserSession",
                        lambda **k: FakeBS())
    def boom(bs, had_state): raise RuntimeError("login timeout")
    monkeypatch.setattr(ad, "_authenticate", boom)
    with pytest.raises(AdapterError):
        ad.verify_login()


class FakeGrowattClient:
    def __init__(self, plant_list_result=None, plant_list_error=None):
        self._result = plant_list_result
        self._error = plant_list_error

    def plant_list(self):
        if self._error is not None:
            raise self._error
        return self._result


def test_growatt_token_verify_login_success(tmp_path):
    ss = SessionStore(str(tmp_path))
    auth = AuthConfig("growatt", mode="token", token="t")
    client = FakeGrowattClient(plant_list_result=[])
    ad = GrowattAdapter(auth, ss, client=client)
    ad.verify_login()  # must not raise


def test_growatt_token_verify_login_preserves_adaptererror_subclass(tmp_path):
    ss = SessionStore(str(tmp_path))
    auth = AuthConfig("growatt", mode="token", token="t")
    err = GrowattV1Error(10001, "token invalid")
    client = FakeGrowattClient(plant_list_error=err)
    ad = GrowattAdapter(auth, ss, client=client)
    with pytest.raises(GrowattV1Error):
        ad.verify_login()


def test_growatt_token_verify_login_wraps_generic_error(tmp_path):
    ss = SessionStore(str(tmp_path))
    auth = AuthConfig("growatt", mode="token", token="t")
    client = FakeGrowattClient(plant_list_error=RuntimeError("boom"))
    ad = GrowattAdapter(auth, ss, client=client)
    with pytest.raises(AdapterError):
        ad.verify_login()
