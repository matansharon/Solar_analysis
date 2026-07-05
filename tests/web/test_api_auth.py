import pytest
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo, crypto
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn); conn.close()
    app = create_app(paths)
    return TestClient(app), paths


def _setup_token(paths):
    # The token is generated in create_app; re-derive by reading its hash is not
    # possible, so tests set a known token directly.
    conn = db.connect(paths.db_path)
    import hashlib
    repo.set_setup_token_hash(conn, hashlib.sha256(b"tok123").hexdigest())
    conn.close()


def test_status_before_setup(tmp_path):
    client, paths = _client(tmp_path)
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_required": True, "authenticated": False}


def test_setup_requires_token(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    bad = client.post("/api/auth/setup", json={"token": "wrong", "password": "pw"}, headers=CSRF)
    assert bad.status_code == 403
    ok = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert ok.status_code == 200
    # second setup rejected
    again = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert again.status_code == 409


def test_login_logout_flow(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert client.post("/api/auth/login", json={"password": "nope"}, headers=CSRF).status_code == 401
    r = client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    assert r.status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True
    client.post("/api/auth/logout", headers=CSRF)
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_protected_route_requires_cookie(tmp_path):
    client, paths = _client(tmp_path)
    # /api/plants is registered later; use auth/password which requires auth.
    r = client.put("/api/auth/password", json={"old": "a", "new": "b"}, headers=CSRF)
    assert r.status_code == 401


def test_csrf_header_required_on_mutation(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    # missing CSRF header -> 403 even though route is public
    r = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"})
    assert r.status_code == 403


def test_password_change_invalidates_session(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    r = client.put("/api/auth/password", json={"old": "pw", "new": "pw2"}, headers=CSRF)
    assert r.status_code == 200
    # old cookie now fails (epoch bumped)
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_login_rate_limited(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "x"}, headers=CSRF)
    r = client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    assert r.status_code == 429


def test_create_app_initializes_fresh_db(tmp_path):
    # A fresh data dir with NO manual db.init_db() must still boot: create_app
    # is responsible for creating the schema.
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    client = TestClient(create_app(paths))
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_required": True, "authenticated": False}
