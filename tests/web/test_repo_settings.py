from solaranalysis.web import db, repo


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_app_settings_defaults(tmp_path):
    c = _conn(tmp_path)
    s = repo.get_app_settings(c)
    assert s == {"model": None, "max_input_tokens": 60000, "output_language": "en"}


def test_app_settings_roundtrip(tmp_path):
    c = _conn(tmp_path)
    repo.set_app_settings(c, model="claude-opus-4-8",
                          max_input_tokens=1000, output_language="he")
    s = repo.get_app_settings(c)
    assert s["model"] == "claude-opus-4-8"
    assert s["max_input_tokens"] == 1000
    assert s["output_language"] == "he"


def test_epoch_starts_zero_and_bumps(tmp_path):
    c = _conn(tmp_path)
    assert repo.get_session_epoch(c) == 0
    assert repo.bump_session_epoch(c) == 1
    assert repo.get_session_epoch(c) == 1


def test_setup_required_until_password_set(tmp_path):
    c = _conn(tmp_path)
    assert repo.setup_required(c) is True
    repo.set_password_hash(c, "pbkdf2_sha256$1$aa$bb")
    assert repo.setup_required(c) is False
    assert repo.get_password_hash(c) == "pbkdf2_sha256$1$aa$bb"
