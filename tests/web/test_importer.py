from solaranalysis.web import db, crypto, repo, importer


def _ctx(tmp_path):
    c = db.connect(str(tmp_path / "app.db")); db.init_db(c)
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    return c, key


def _write_cfg(tmp_path):
    (tmp_path / ".env").write_text("SE_USER=a@b.com\nSE_PASS=pw\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "model: null\nmax_input_tokens: 1234\noutput_language: he\n"
        "plants:\n  - name: Roof\n    auth:\n      platform: solaredge\n"
        "      mode: password\n      username: ${SE_USER}\n      password: ${SE_PASS}\n"
        "    tariff_per_kwh: 0.5\n    currency: ILS\n", encoding="utf-8")


def test_import_creates_plants_and_settings(tmp_path):
    c, key = _ctx(tmp_path)
    _write_cfg(tmp_path)
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"),
                                     str(tmp_path / ".env"))
    assert summary["created"] == ["Roof"]
    assert summary["secrets"]["Roof"]["password"] is True
    assert repo.get_app_settings(c)["max_input_tokens"] == 1234
    auth = repo.load_plant_auth(c, key, repo.list_plants(c)[0]["id"])
    assert auth.password == "pw"


def test_import_is_idempotent_updates(tmp_path):
    c, key = _ctx(tmp_path)
    _write_cfg(tmp_path)
    importer.import_config(c, key, str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    assert summary["updated"] == ["Roof"]
    assert len(repo.list_plants(c)) == 1


def test_import_reports_missing_env(tmp_path):
    c, key = _ctx(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "plants:\n  - name: X\n    auth:\n      platform: growatt\n"
        "      mode: password\n      username: ${NOPE}\n      password: p\n",
        encoding="utf-8")
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"),
                                     str(tmp_path / ".env"))
    assert summary["error"] and "NOPE" in summary["error"]


def test_import_config_reports_duplicate_names(tmp_path):
    c, key = _ctx(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "plants:\n"
        "  - name: Dup\n    auth:\n      platform: sma\n      mode: password\n"
        "      username: u\n      password: p\n"
        "  - name: Dup\n    auth:\n      platform: growatt\n      mode: password\n"
        "      username: u2\n      password: p2\n", encoding="utf-8")
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"),
                                     str(tmp_path / ".env"))
    assert summary["error"] and "Dup" in summary["error"]
    assert repo.list_plants(c) == []  # no partial writes


import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

_CSRF = {"X-Solar-CSRF": "1"}


def _client_with_app(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    client = TestClient(create_app(paths))
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=_CSRF)
    return client, paths


def _write_cfg_into(dir_path):
    (dir_path / ".env").write_text("SE_USER=a@b.com\nSE_PASS=pw\n", encoding="utf-8")
    (dir_path / "config.yaml").write_text(
        "model: null\nmax_input_tokens: 1234\noutput_language: he\n"
        "plants:\n  - name: Roof\n    auth:\n      platform: solaredge\n"
        "      mode: password\n      username: ${SE_USER}\n      password: ${SE_PASS}\n"
        "    tariff_per_kwh: 0.5\n    currency: ILS\n", encoding="utf-8")


def test_import_route_404_without_config(tmp_path):
    client, paths = _client_with_app(tmp_path)
    assert client.post("/api/import", headers=_CSRF).status_code == 404


def test_import_route_200_with_config(tmp_path):
    client, paths = _client_with_app(tmp_path)
    import os
    _write_cfg_into(__import__("pathlib").Path(paths.app_dir))
    r = client.post("/api/import", headers=_CSRF)
    assert r.status_code == 200
    assert r.json()["created"] == ["Roof"]
    # Secrets must never come back in the summary.
    assert "pw" not in r.text


def test_import_route_400_on_bad_config(tmp_path):
    client, paths = _client_with_app(tmp_path)
    from pathlib import Path
    d = Path(paths.app_dir)
    (d / ".env").write_text("", encoding="utf-8")
    (d / "config.yaml").write_text(
        "plants:\n  - name: X\n    auth:\n      platform: growatt\n"
        "      mode: password\n      username: ${NOPE_MISSING}\n      password: p\n",
        encoding="utf-8")
    r = client.post("/api/import", headers=_CSRF)
    assert r.status_code == 400
    assert "NOPE_MISSING" in r.text
