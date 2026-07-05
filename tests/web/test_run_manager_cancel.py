import threading
from solaranalysis.web import db, repo, crypto, run_manager
from solaranalysis.web.events import EVENT_PREFIX
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn); conn.close()
    return p


class KillableProc:
    def __init__(self):
        self.pid = 4242
        self._killed = threading.Event()
        self.stdout = self._gen()
    def _gen(self):
        # Block until killed, yielding nothing (simulates a hung run).
        self._killed.wait(timeout=5)
        return
        yield
    def wait(self):
        self._killed.wait(timeout=5)
        return -9
    def kill(self):
        self._killed.set()


def test_cancel_marks_cancelled(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    proc = KillableProc()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    # Avoid real psutil tree-kill; route to proc.kill().
    monkeypatch.setattr(rm, "_kill_tree", lambda pid: proc.kill())
    rid = rm.start_run("manual", "30d")
    assert rm.cancel(rid) is True
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "cancelled"


def test_run_test_records_result(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    pid = repo.create_plant(conn, key, {"name": "G", "platform": "sma",
                                        "auth_mode": "password",
                                        "username": "u", "password": "p"})
    conn.close()

    class TestProc:
        def __init__(self, ok):
            self.pid = 1
            self.stdout = iter([EVENT_PREFIX + '{"event":"test_result","ok":%s,"error":null}\n'
                                % ("true" if ok else "false")])
        def wait(self): return 0
        def kill(self): pass
    rm = run_manager.RunManager(paths, spawn=lambda cmd: TestProc(True))
    res = rm.run_test(pid)
    assert res["ok"] is True
    conn = db.connect(paths.db_path)
    assert repo.get_plant(conn, pid)["last_test_ok"] is True


def test_reconcile_marks_dead_running_as_interrupted(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    repo.set_run_pid(conn, rid, 999999)  # not a live pid
    conn.close()
    rm = run_manager.RunManager(paths)
    monkeypatch.setattr(run_manager, "_pid_alive", lambda pid: False)
    n = rm.reconcile_on_startup()
    assert n == 1
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "interrupted"


def test_run_test_releases_lock_when_persist_fails(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    pid = repo.create_plant(conn, key, {"name": "G", "platform": "sma",
                                        "auth_mode": "password",
                                        "username": "u", "password": "p"})
    conn.close()

    class TestProc:
        def __init__(self):
            self.pid = 1
            self.stdout = iter([EVENT_PREFIX + '{"event":"test_result","ok":true,"error":null}\n'])
        def wait(self): return 0
        def kill(self): pass

    rm = run_manager.RunManager(paths, spawn=lambda cmd: TestProc())
    # Force the DB persistence to blow up; the lock must still be released.
    monkeypatch.setattr(run_manager.repo, "set_plant_test_result",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db boom")))
    rm.run_test(pid)  # persistence error is swallowed, lock must free
    assert rm.active() is None


def test_reconcile_continues_past_a_bad_row(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    r1 = repo.create_run(conn, trigger="manual", time_range="30d",
                         log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    r2 = repo.create_run(conn, trigger="manual", time_range="30d",
                         log_path="logs/run-2.log", started_at="2026-07-04T00:00:00")
    conn.close()
    rm = run_manager.RunManager(paths)
    monkeypatch.setattr(run_manager, "_pid_alive", lambda pid: False)
    # Both rows should be reconciled without the loop aborting.
    n = rm.reconcile_on_startup()
    assert n == 2
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, r1)["status"] == "interrupted"
    assert repo.get_run(conn, r2)["status"] == "interrupted"
    conn.close()
