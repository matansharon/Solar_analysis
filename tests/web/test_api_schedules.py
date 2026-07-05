import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


class FakeSched:
    def __init__(self): self.reloads = 0
    def reload(self): self.reloads += 1
    def start(self): pass


def _client(tmp_path, sched=None):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, schedule_service=sched)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client


def test_schedule_crud_and_reload(tmp_path):
    sched = FakeSched()
    client = _client(tmp_path, sched=sched)
    r = client.post("/api/schedules", headers=CSRF, json={
        "time_of_day": "06:00", "days_of_week": "0,1,2,3,4",
        "time_range": "30d", "enabled": True})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert len(client.get("/api/schedules").json()) == 1
    client.put(f"/api/schedules/{sid}", headers=CSRF, json={"enabled": False})
    assert client.get("/api/schedules").json()[0]["enabled"] is False
    client.delete(f"/api/schedules/{sid}", headers=CSRF)
    assert client.get("/api/schedules").json() == []
    assert sched.reloads == 3  # create, update, delete each reload
