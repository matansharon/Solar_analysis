"""Measurement persistence: snapshot rows, upsert-deduped energy points."""
import json
import sqlite3

from solaranalysis.core import measurements
from solaranalysis.core.schema import (
    EnergyPoint, PlantData, Metric, TimeRange, Device, DeviceStatus, Alert, AlertSeverity,
    PowerPoint,
)
from solaranalysis.web import db


def _conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def _plant(points=()):
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="P",
                   energy_today_kwh=Metric(12.5, "kWh"))
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.energy_timeseries = list(points)
    return pd


def test_snapshot_row_written_without_timeseries_blobs():
    conn = _conn()
    pd = _plant([EnergyPoint("2026-07-06", 100.0, "day")])
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=7)
    conn.commit()
    row = conn.execute("SELECT * FROM plant_snapshots").fetchone()
    assert row["run_id"] == 7
    assert row["plant_uid"] == "growatt-1"
    assert row["source_platform"] == "growatt"
    assert row["time_range"] == "30d"
    kpis = json.loads(row["kpis_json"])
    assert kpis["energy_today_kwh"]["value"] == 12.5
    assert "energy_timeseries" not in kpis          # series live in energy_points
    assert "power_timeseries" not in kpis


def test_cli_runs_use_null_run_id():
    conn = _conn()
    measurements.save_measurements(conn, [_plant()], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    assert conn.execute("SELECT run_id FROM plant_snapshots").fetchone()[0] is None


def test_energy_points_upsert_latest_wins():
    conn = _conn()
    measurements.save_measurements(
        conn, [_plant([EnergyPoint("2026-07-07", 100.0, "day")])],
        TimeRange.LAST_30D, run_id=None)
    # Second run later the same day: the partial value self-corrects.
    measurements.save_measurements(
        conn, [_plant([EnergyPoint("2026-07-07", 250.2, "day")])],
        TimeRange.LAST_30D, run_id=None)
    conn.commit()
    rows = conn.execute("SELECT period, energy_kwh FROM energy_points").fetchall()
    assert len(rows) == 1
    assert rows[0]["energy_kwh"] == 250.2


def test_energy_points_config_plant_id_not_clobbered_by_null():
    conn = _conn()
    pd = _plant([EnergyPoint("2026-07-07", 100.0, "day")])
    pd.config_plant_id = 5
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=None)
    # Later CLI re-fetch of the same period never sets config_plant_id.
    pd2 = _plant([EnergyPoint("2026-07-07", 100.0, "day")])
    pd2.config_plant_id = None
    measurements.save_measurements(conn, [pd2], TimeRange.LAST_30D, run_id=None)
    conn.commit()
    row = conn.execute("SELECT config_plant_id FROM energy_points").fetchone()
    assert row["config_plant_id"] == 5


def test_null_energy_points_skipped_and_granularities_kept_apart():
    conn = _conn()
    pts = [EnergyPoint("2026-07-06", None, "day"),
           EnergyPoint("2026-07-06", 100.0, "day"),
           EnergyPoint("2026-07", 2021.0, "month")]
    measurements.save_measurements(conn, [_plant(pts)], TimeRange.LAST_30D, None)
    conn.commit()
    rows = conn.execute(
        "SELECT granularity, period, energy_kwh FROM energy_points ORDER BY granularity").fetchall()
    assert [(r["granularity"], r["period"], r["energy_kwh"]) for r in rows] == [
        ("day", "2026-07-06", 100.0), ("month", "2026-07", 2021.0)]


def test_load_series_orders_and_filters():
    conn = _conn()
    pts = [EnergyPoint("2026-07-02", 2.0, "day"), EnergyPoint("2026-07-01", 1.0, "day"),
           EnergyPoint("2026-06-30", 0.5, "day")]
    measurements.save_measurements(conn, [_plant(pts)], TimeRange.LAST_30D, None)
    conn.commit()
    out = measurements.load_series(conn, "growatt-1", "day", since="2026-07-01")
    assert [(p.timestamp_local, p.energy_kwh) for p in out] == [
        ("2026-07-01", 1.0), ("2026-07-02", 2.0)]


def _plant_with_device(status=DeviceStatus.ONLINE):
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="P")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = 5
    pd.devices = [Device(device_id="inv-1", status=status, model="MIN 3000",
                         current_power_kw=3.2)]
    return pd


def _plant_with_alert():
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="P")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = 5
    pd.alerts = [Alert(alert_id="A1", severity=AlertSeverity.ERROR,
                       message="Grid fault", timestamp_local="2026-07-07 09:00")]
    return pd


def test_device_snapshot_written():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_device()], TimeRange.SNAPSHOT, run_id=3)
    conn.commit()
    row = conn.execute("SELECT * FROM device_snapshots").fetchone()
    assert row["run_id"] == 3
    assert row["config_plant_id"] == 5
    assert row["device_id"] == "inv-1"
    assert row["status"] == "online"
    assert row["current_power_kw"] == 3.2


def test_device_history_appends_each_run_instead_of_overwriting():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_device(DeviceStatus.ONLINE)],
                                   TimeRange.SNAPSHOT, run_id=1)
    measurements.save_measurements(conn, [_plant_with_device(DeviceStatus.OFFLINE)],
                                   TimeRange.SNAPSHOT, run_id=2)
    conn.commit()
    rows = conn.execute("SELECT status FROM device_snapshots ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["online", "offline"]


def test_load_devices_latest_dedupes_to_most_recent_fetch():
    conn = _conn()
    older = _plant_with_device(DeviceStatus.ONLINE)
    older.fetched_at_utc = "2026-07-06T10:00:00+00:00"
    measurements.save_measurements(conn, [older], TimeRange.SNAPSHOT, run_id=1)
    newer = _plant_with_device(DeviceStatus.OFFLINE)
    newer.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    measurements.save_measurements(conn, [newer], TimeRange.SNAPSHOT, run_id=2)
    conn.commit()
    latest = measurements.load_devices_latest(conn, 5)
    assert len(latest) == 1
    assert latest[0]["status"] == "offline"


def test_alert_snapshot_written_and_loaded_newest_first():
    conn = _conn()
    measurements.save_measurements(conn, [_plant_with_alert()], TimeRange.SNAPSHOT, run_id=1)
    conn.commit()
    alerts = measurements.load_alerts(conn, 5)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "error"
    assert alerts[0]["message"] == "Grid fault"


def test_load_alerts_respects_limit():
    conn = _conn()
    for i in range(3):
        pd = _plant_with_alert()
        pd.alerts[0].alert_id = f"A{i}"
        measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=i)
    conn.commit()
    assert len(measurements.load_alerts(conn, 5, limit=2)) == 2


def test_power_points_upsert_latest_wins():
    conn = _conn()
    p1 = _plant()
    p1.config_plant_id = 5
    p1.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.0)]
    measurements.save_measurements(conn, [p1], TimeRange.SNAPSHOT, run_id=None)
    p2 = _plant()
    p2.config_plant_id = 5
    p2.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.5)]
    measurements.save_measurements(conn, [p2], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    rows = conn.execute("SELECT power_kw FROM power_points").fetchall()
    assert len(rows) == 1
    assert rows[0]["power_kw"] == 3.5


def test_power_points_config_plant_id_not_clobbered_by_null():
    conn = _conn()
    p1 = _plant()
    p1.config_plant_id = 5
    p1.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.0)]
    measurements.save_measurements(conn, [p1], TimeRange.SNAPSHOT, run_id=None)
    # Later CLI re-fetch of the same point never sets config_plant_id.
    p2 = _plant()
    p2.config_plant_id = None
    p2.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.0)]
    measurements.save_measurements(conn, [p2], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    row = conn.execute("SELECT config_plant_id FROM power_points").fetchone()
    assert row["config_plant_id"] == 5


def test_load_power_series_orders_and_filters():
    conn = _conn()
    pd = _plant()
    pd.config_plant_id = 5
    pd.power_timeseries = [PowerPoint("2026-07-07T11:00", 2.0),
                           PowerPoint("2026-07-07T09:00", 1.0)]
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=None)
    conn.commit()
    out = measurements.load_power_series(conn, 5, since="2026-07-07T10:00")
    assert [(p.timestamp_local, p.power_kw) for p in out] == [("2026-07-07T11:00", 2.0)]


def test_load_energy_series_by_config_plant_id():
    conn = _conn()
    pd = _plant([EnergyPoint("2026-07-06", 100.0, "day")])
    pd.config_plant_id = 5
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=None)
    conn.commit()
    out = measurements.load_energy_series(conn, 5, granularity="day")
    assert [(p.timestamp_local, p.energy_kwh) for p in out] == [("2026-07-06", 100.0)]
