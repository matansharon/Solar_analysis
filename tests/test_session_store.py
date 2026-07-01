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

def test_poll_guard(tmp_path):
    clk = Clock()
    s = SessionStore(str(tmp_path), now_fn=clk)
    assert s.can_poll("growatt", 300) is True   # never polled
    s.mark_poll("growatt")
    assert s.can_poll("growatt", 300) is False
    clk.t += 301
    assert s.can_poll("growatt", 300) is True
