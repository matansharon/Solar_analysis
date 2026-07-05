import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


class FakeRM:
    def __init__(self): self.tested = None
    def run_test(self, plant_id, timeout_s=95):
        self.tested = plant_id
        return {"ok": True, "error": None}


def _client(tmp_path, rm=None):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, run_manager=rm)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def test_create_list_hides_secrets(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "Roof", "platform": "solaredge", "auth_mode": "password",
        "username": "a@b.com", "password": "pw", "tariff_per_kwh": 0.5,
        "currency": "ILS"})
    assert r.status_code == 201
    lst = client.get("/api/plants").json()
    assert lst[0]["has_password"] is True
    assert "password" not in lst[0]


def test_create_rejects_token_for_non_growatt(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "X", "platform": "sma", "auth_mode": "token", "token": "t"})
    assert r.status_code == 422


def test_create_requires_secret(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "X", "platform": "sma", "auth_mode": "password", "username": "u"})
    assert r.status_code == 422


def test_update_blank_password_keeps(tmp_path):
    client, paths = _client(tmp_path)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "orig"}).json()["id"]
    client.put(f"/api/plants/{pid}", headers=CSRF,
               json={"username": "u2", "password": ""})
    p = client.get(f"/api/plants/{pid}").json()
    assert p["username"] == "u2" and p["has_password"] is True


def test_delete(tmp_path):
    client, _ = _client(tmp_path)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]
    assert client.delete(f"/api/plants/{pid}", headers=CSRF).status_code == 200
    assert client.get("/api/plants").json() == []


def test_test_endpoint_calls_run_manager(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm=rm)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]
    r = client.post(f"/api/plants/{pid}/test", headers=CSRF)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert rm.tested == pid


def test_test_endpoint_409_when_disabled(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm=rm)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p", "enabled": False}).json()["id"]
    r = client.post(f"/api/plants/{pid}/test", headers=CSRF)
    assert r.status_code == 409


def test_settings_get_put(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/settings").json()["output_language"] == "en"
    client.put("/api/settings", headers=CSRF,
               json={"model": None, "max_input_tokens": 1000, "output_language": "he"})
    assert client.get("/api/settings").json()["max_input_tokens"] == 1000
