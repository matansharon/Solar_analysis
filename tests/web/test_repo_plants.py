from solaranalysis.web import db, repo, crypto


def _ctx(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    return c, key


def test_create_and_public_hides_secrets(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "Roof", "platform": "solaredge", "auth_mode": "password",
        "username": "a@b.com", "password": "pw", "tariff_per_kwh": 0.5,
        "currency": "ILS"})
    p = repo.get_plant(c, pid)
    assert p["name"] == "Roof" and p["has_password"] is True
    assert p["has_token"] is False
    assert "password" not in p and "password_enc" not in p


def test_load_plant_auth_decrypts(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "pw"})
    auth = repo.load_plant_auth(c, key, pid)
    assert auth.platform == "growatt"
    assert auth.username == "u" and auth.password == "pw"


def test_update_blank_password_keeps_existing(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "orig"})
    repo.update_plant(c, key, pid, {"username": "u2", "password": ""})
    auth = repo.load_plant_auth(c, key, pid)
    assert auth.username == "u2" and auth.password == "orig"


def test_switch_platform_off_growatt_clears_token(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "token",
        "token": "tok"})
    repo.update_plant(c, key, pid, {"platform": "sma"})
    p = repo.get_plant(c, pid)
    assert p["platform"] == "sma" and p["auth_mode"] == "password"
    assert p["has_token"] is False


def test_test_result_recorded(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"})
    repo.set_plant_test_result(c, pid, ok=False, error="bad creds",
                               at="2026-07-04T00:00:00")
    p = repo.get_plant(c, pid)
    assert p["last_test_ok"] is False and p["last_test_error"] == "bad creds"
