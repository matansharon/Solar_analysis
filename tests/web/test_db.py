from solaranalysis.web import db


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_init_creates_tables(tmp_path):
    c = _conn(tmp_path)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plants", "settings", "schedules", "runs"} <= names


def test_init_is_idempotent(tmp_path):
    c = _conn(tmp_path)
    db.init_db(c)  # second call must not raise
    assert c.execute("SELECT COUNT(*) AS n FROM plants").fetchone()["n"] == 0


def test_wal_and_row_factory(tmp_path):
    c = _conn(tmp_path)
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    c.execute("INSERT INTO settings(key,value) VALUES('k','v')")
    row = c.execute("SELECT value FROM settings WHERE key='k'").fetchone()
    assert row["value"] == "v"  # Row supports name access


def test_platform_check_constraint(tmp_path):
    import sqlite3
    import pytest
    c = _conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO plants(name,platform) VALUES('x','bogus')")
        c.commit()
