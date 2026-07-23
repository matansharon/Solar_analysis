from solaranalysis.web import db


def test_init_db_creates_raw_payloads_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "raw_payloads" in tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_payloads)")}
    assert {"run_id", "plant_uid", "platform", "endpoint_label",
            "payload_zjson", "fetched_at_utc"} <= cols
    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == "5"


def test_init_db_idempotent():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.init_db(conn)  # must not raise
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 0
