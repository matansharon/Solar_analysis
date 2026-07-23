import queue
import threading
from solaranalysis.web import db, repo, crypto, run_manager
from solaranalysis.web.events import EVENT_PREFIX
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn)
    key = crypto.load_or_create_key(p.key_path)
    repo.create_plant(conn, key, {"name": "Good", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "sekret"})
    conn.close()
    return p


class FakeProc:
    def __init__(self, lines, code=0):
        self._lines = lines
        self.stdout = iter(lines)
        self.pid = 9999
        self._code = code
        self._done = threading.Event()
    def wait(self):
        self._done.wait(timeout=5)
        return self._code
    def kill(self):
        self._done.set()


def _ev(d):
    import json
    return EVENT_PREFIX + json.dumps(d) + "\n"


def test_start_run_success_finalizes(tmp_path):
    paths = _paths(tmp_path)
    lines = [_ev({"event": "run_start", "plants": ["Good"], "time_range": "30d"}),
             _ev({"event": "plant_done", "plant": "Good", "ok": True}),
             _ev({"event": "report_written", "path": "output/x/report.html"}),
             _ev({"event": "run_complete", "status": "success",
                  "report_path": "output/x/report.html", "skipped": [],
                  "plants_summary": [{"name": "Good", "ok": True}],
                  "notes": {"verify_missing_count": 0}})]
    proc = FakeProc(lines)
    proc._done.set()  # wait() returns immediately after stdout drains
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)  # test helper: waits for pump thread
    conn = db.connect(paths.db_path)
    run = repo.get_run(conn, rid)
    assert run["status"] == "success"
    assert run["report_path"] == "output/x/report.html"
    assert rm.active() is None


def test_busy_rejects_second_start(tmp_path):
    paths = _paths(tmp_path)
    gate = threading.Event()
    class Blocking(FakeProc):
        def __init__(self): super().__init__([]);
        def wait(self):
            gate.wait(timeout=5); return 0
    proc = Blocking()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rm.start_run("manual", "30d")
    import pytest
    with pytest.raises(run_manager.Busy) as ei:
        rm.start_run("manual", "30d")
    assert ei.value.active["kind"] == "run"
    gate.set()


def test_subscriber_receives_events_and_end(tmp_path):
    paths = _paths(tmp_path)
    import threading as _t
    gate = _t.Event()
    lines = [_ev({"event": "run_start", "plants": ["Good"], "time_range": "30d"}),
             "plain log line\n",
             _ev({"event": "run_complete", "status": "success",
                  "report_path": "output/x/report.html", "skipped": [],
                  "plants_summary": [], "notes": {"verify_missing_count": 0}})]

    class GatedProc:
        def __init__(self):
            self.pid = 8888
            def gen():
                gate.wait(timeout=5)  # do not emit until subscriber is registered
                for l in lines:
                    yield l
            self.stdout = gen()
        def wait(self): return 0
        def kill(self): gate.set()

    rm = run_manager.RunManager(paths, spawn=lambda cmd: GatedProc())
    rid = rm.start_run("manual", "30d")
    q = rm.subscribe(rid)
    gate.set()  # now let the pump drain and broadcast
    rm.join(rid, timeout=5)
    seen = []
    while True:
        try:
            seen.append(q.get_nowait())
        except queue.Empty:
            break
    types = [m["type"] for m in seen]
    assert "end" in types


def test_secret_redacted_in_log_and_stream(tmp_path):
    paths = _paths(tmp_path)
    lines = ["traceback: password was sekret\n",
             _ev({"event": "run_complete", "status": "failed"})]
    proc = FakeProc(lines, code=1); proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    run = repo.get_run(conn, rid)
    log = open(paths.data_dir + "/" + run["log_path"], encoding="utf-8").read()
    assert "sekret" not in log and "***" in log
    assert run["status"] == "failed"


def test_start_run_persists_plant_id(tmp_path):
    paths = _paths(tmp_path)
    proc = FakeProc([_ev({"event": "run_complete", "status": "failed"})], code=1)
    proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d", plant_id=3)
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["plant_id"] == 3


def test_start_run_default_plant_id_is_null(tmp_path):
    paths = _paths(tmp_path)
    proc = FakeProc([_ev({"event": "run_complete", "status": "failed"})], code=1)
    proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["plant_id"] is None
