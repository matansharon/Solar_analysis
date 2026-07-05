import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import SolarPortalAdapter, get_adapter, AdapterError

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
