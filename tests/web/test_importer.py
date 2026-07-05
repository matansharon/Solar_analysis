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
