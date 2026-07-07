from solaranalysis.web import db


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_init_creates_tables(tmp_path):
    c = _conn(tmp_path)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plants", "settings", "schedules", "runs",
            "plant_snapshots", "energy_points"} <= names


_V1_DDL = """
CREATE TABLE IF NOT EXISTS plants(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY);
"""


def test_v1_db_migrates_to_v2_on_init(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    c.executescript(_V1_DDL)
    c.execute("INSERT INTO settings(key,value) VALUES('schema_version','1')")
    c.commit()
    db.init_db(c)  # additive DDL picks up the new tables
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plant_snapshots", "energy_points"} <= names
    ver = c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    assert ver["value"] == str(db.SCHEMA_VERSION)


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
