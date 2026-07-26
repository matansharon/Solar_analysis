from solaranalysis.web import db


def test_init_db_creates_optimizer_tables_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"optimizers", "optimizer_energy"} <= tables
    inv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(optimizers)")}
    assert {"site_id", "optimizer_serial", "label", "inverter_serial",
            "model", "first_seen_utc", "last_seen_utc"} <= inv_cols
    e_cols = {r["name"] for r in conn.execute("PRAGMA table_info(optimizer_energy)")}
    assert {"site_id", "optimizer_serial", "day", "energy_wh", "color",
            "temperature_c", "updated_at_utc"} <= e_cols
    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == str(db.SCHEMA_VERSION)
