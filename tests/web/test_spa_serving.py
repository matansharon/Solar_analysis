import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn); conn.close()
    return TestClient(create_app(paths))


def test_root_serves_spa_placeholder(tmp_path):
    client = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_client_route_falls_through_to_spa(tmp_path):
    client = _client(tmp_path)
    # An unknown non-API path must return the SPA shell, not 404.
    r = client.get("/plants")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_unknown_api_route_is_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/does-not-exist").status_code == 404
