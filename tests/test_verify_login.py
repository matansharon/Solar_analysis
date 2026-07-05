import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import AdapterError
from solaranalysis.adapters.solaredge import SolarEdgeAdapter


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
