from solaranalysis.web import db


def test_init_db_creates_string_tables_and_bumps_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"inverter_channels", "channel_day_energy",
            "channel_samples", "inverter_samples"} <= tables

    chan = {r["name"] for r in conn.execute("PRAGMA table_info(inverter_channels)")}
    assert {"device_sn", "channel_kind", "channel_no", "parent_channel_no",
            "group_voltage", "plant_uid", "lifetime_kwh",
            "first_seen_utc", "last_seen_utc"} <= chan

    energy = {r["name"] for r in conn.execute("PRAGMA table_info(channel_day_energy)")}
    assert {"device_sn", "channel_no", "day", "energy_kwh", "share_of_total",
            "peak_w", "peak_at", "producing_minutes", "updated_at_utc"} <= energy

    samples = {r["name"] for r in conn.execute("PRAGMA table_info(channel_samples)")}
    assert {"device_sn", "sampled_at", "day", "channel_kind", "channel_no",
            "power_w", "voltage_v", "current_a"} <= samples

    inv = {r["name"] for r in conn.execute("PRAGMA table_info(inverter_samples)")}
    assert {"device_sn", "sampled_at", "day", "pac_w", "e_ac_today_kwh",
            "temp_c", "temp2_c", "temp3_c", "temp4_c", "temp5_c",
            "pv_iso_kohm", "gfci_ma", "status", "derating_mode",
            "str_break", "str_unbalance", "str_unmatch",
            "warn_code", "fault_code1", "fault_code2", "fault_type"} <= inv

    ver = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0]
    assert ver == "7"


def test_channel_kind_is_constrained():
    import sqlite3
    import pytest
    conn = db.connect(":memory:")
    db.init_db(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO inverter_channels(device_sn, channel_kind, channel_no,"
            " first_seen_utc, last_seen_utc) VALUES('SN','bogus',1,'t','t')")


def test_init_db_idempotent_with_string_tables():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.init_db(conn)  # must not raise
    assert conn.execute("SELECT COUNT(*) FROM channel_samples").fetchone()[0] == 0
