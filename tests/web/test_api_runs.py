import hashlib, os
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths
from solaranalysis.web.run_manager import Busy

CSRF = {"X-Solar-CSRF": "1"}


class FakeRM:
    def __init__(self, busy=False):
        self.busy = busy
        self.cancelled = None
        self.last_plant_id = None
        self._progress = {"plants": {"A": "running"}, "status": "running"}
    def start_run(self, trigger, time_range, plant_id=None):
        self.last_plant_id = plant_id
        if self.busy:
            raise Busy({"kind": "run", "id": 1})
        return 5
    def get_progress(self, rid): return self._progress
    def cancel(self, rid):
        self.cancelled = rid; return True


def _client(tmp_path, rm):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, run_manager=rm)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def _seed_plant(paths, *, enabled=True):
    from solaranalysis.web import crypto
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    pid = repo.create_plant(conn, key, {"name": "Sys", "platform": "growatt",
                                        "auth_mode": "password", "username": "u",
                                        "password": "pw", "enabled": enabled})
    conn.close()
    return pid


def test_create_run_ok(tmp_path):
    client, _ = _client(tmp_path, FakeRM())
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 201 and r.json()["id"] == 5


def test_create_run_accepts_plant_id(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    pid = _seed_plant(paths)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": pid})
    assert r.status_code == 201 and r.json()["id"] == 5
    assert rm.last_plant_id == pid


def test_create_run_rejects_unknown_plant(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": 999})
    assert r.status_code == 422
    assert rm.last_plant_id is None


def test_create_run_rejects_disabled_plant(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    pid = _seed_plant(paths, enabled=False)
    r = client.post("/api/runs", headers=CSRF,
                    json={"time_range": "30d", "plant_id": pid})
    assert r.status_code == 422
    assert rm.last_plant_id is None


def test_create_run_without_plant_id_is_fleet(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 201
    assert rm.last_plant_id is None


def test_create_run_bad_range(tmp_path):
    client, _ = _client(tmp_path, FakeRM())
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "bogus"})
    assert r.status_code == 422


def test_create_run_busy(tmp_path):
    client, _ = _client(tmp_path, FakeRM(busy=True))
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 409 and r.json()["active"]["kind"] == "run"


def test_get_run_merges_progress(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()
    r = client.get(f"/api/runs/{rid}").json()
    assert r["status"] == "running"
    assert r["progress"]["plants"]["A"] == "running"


def test_cancel(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs/5/cancel", headers=CSRF)
    assert r.status_code == 200 and r.json()["cancelled"] is True
    assert rm.cancelled == 5


def test_log_reads_file(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-9.log", started_at="2026-07-04T00:00:00")
    conn.close()
    os.makedirs(os.path.join(paths.data_dir, "logs"), exist_ok=True)
    with open(os.path.join(paths.data_dir, "logs", "run-9.log"), "w", encoding="utf-8") as f:
        f.write("line one\nline two\n")
    r = client.get(f"/api/runs/{rid}/log").json()
    assert "line two" in r["log"]
