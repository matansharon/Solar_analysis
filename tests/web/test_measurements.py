"""Measurement persistence: snapshot rows, upsert-deduped energy points."""
import json
import sqlite3

from solaranalysis.core import measurements
from solaranalysis.core.schema import EnergyPoint, PlantData, Metric, TimeRange
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
