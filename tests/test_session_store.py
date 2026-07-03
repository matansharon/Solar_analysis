from solaranalysis.core.session_store import SessionStore

class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t

def test_save_load_roundtrip(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    s.save("growatt", {"cookie": "abc"}, ttl_seconds=100)
    assert s.load("growatt") == {"cookie": "abc"}

def test_load_expired_returns_none(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    s.save("solaredge", {"c": 1}, ttl_seconds=10)
    clk.t += 11
    assert s.load("solaredge") is None

def test_missing_returns_none(tmp_path):
    s = SessionStore(str(tmp_path), now_fn=Clock())
    assert s.load("nope") is None

def test_failed_save_preserves_previous_session(tmp_path, monkeypatch):
    # A crash mid-write must not destroy the previously saved session.
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    s.save("growatt", {"cookie": "old"}, ttl_seconds=100)
    import pytest
    import solaranalysis.core.session_store as mod
    def boom(*a, **k): raise RuntimeError("disk full")
    monkeypatch.setattr(mod.json, "dump", boom)
    with pytest.raises(RuntimeError):
        s.save("growatt", {"cookie": "new"}, ttl_seconds=100)
    monkeypatch.undo()
    assert s.load("growatt") == {"cookie": "old"}
