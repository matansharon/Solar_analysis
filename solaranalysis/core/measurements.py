"""Persist fetched plant measurements so history accumulates across runs.

Stdlib-sqlite3 only (no web dependencies) so the CLI can import it too. The
schema (plant_snapshots, energy_points, device_snapshots, alert_snapshots)
lives in web/db.py's DDL; callers run db.init_db(conn) before saving.

energy_points is upsert-keyed on (plant, granularity, period), latest value
wins — today's partial figure self-corrects on the next run. device_snapshots
and alert_snapshots are append-only: one row per device/alert per fetch, with
dedup to the latest fetch happening at read time in load_devices_latest.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

from .schema import EnergyPoint, PlantData, TimeRange


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_measurements(conn: sqlite3.Connection, plants: list[PlantData],
                      time_range: TimeRange, run_id: int | None) -> None:
    """One snapshot row per plant + upserted energy points. Caller commits."""
    now = _now_utc()
    for pd in plants:
        kpis = pd.to_dict()
        kpis.pop("energy_timeseries", None)
        kpis.pop("power_timeseries", None)
        conn.execute(
            "INSERT INTO plant_snapshots"
            "(run_id, config_plant_id, plant_uid, source_platform, fetched_at_utc, "
            "time_range, kpis_json) VALUES (?,?,?,?,?,?,?)",
            (run_id, pd.config_plant_id, pd.plant_id, pd.source_platform,
             pd.fetched_at_utc or now, time_range.value,
             json.dumps(kpis, ensure_ascii=False)))
        for p in pd.energy_timeseries:
            if p.energy_kwh is None:
                continue
            conn.execute(
                "INSERT INTO energy_points"
                "(plant_uid, config_plant_id, granularity, period, energy_kwh, updated_at_utc) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(plant_uid, granularity, period) DO UPDATE SET "
                "energy_kwh=excluded.energy_kwh, "
                "config_plant_id=excluded.config_plant_id, "
                "updated_at_utc=excluded.updated_at_utc",
                (pd.plant_id, pd.config_plant_id, p.granularity, p.timestamp_local,
                 p.energy_kwh, now))
        for d in pd.devices:
            conn.execute(
                "INSERT INTO device_snapshots"
                "(run_id, config_plant_id, plant_uid, device_id, device_type, model,"
                " manufacturer, status, current_power_kw, energy_lifetime_kwh,"
                " temperature_c, last_seen_local, fetched_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, pd.config_plant_id, pd.plant_id, d.device_id, d.device_type,
                 d.model, d.manufacturer, d.status.value, d.current_power_kw,
                 d.energy_lifetime_kwh, d.temperature_c, d.last_seen_local,
                 pd.fetched_at_utc or now))
        for a in pd.alerts:
            conn.execute(
                "INSERT INTO alert_snapshots"
                "(run_id, config_plant_id, plant_uid, alert_id, severity, code,"
                " message, timestamp_local, resolved, fetched_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, pd.config_plant_id, pd.plant_id, a.alert_id, a.severity.value,
                 a.code, a.message, a.timestamp_local,
                 None if a.resolved is None else int(a.resolved),
                 pd.fetched_at_utc or now))


def load_series(conn: sqlite3.Connection, plant_uid: str, granularity: str,
                since: str | None = None) -> list[EnergyPoint]:
    """Accumulated series for a plant, oldest first."""
    sql = ("SELECT period, energy_kwh FROM energy_points "
           "WHERE plant_uid=? AND granularity=?")
    args: list = [plant_uid, granularity]
    if since is not None:
        sql += " AND period>=?"
        args.append(since)
    sql += " ORDER BY period"
    return [EnergyPoint(row[0], row[1], granularity)
            for row in conn.execute(sql, args)]


def load_devices_latest(conn: sqlite3.Connection, config_plant_id: int) -> list[dict]:
    """Most recent snapshot row per device_id, newest fetch wins."""
    rows = conn.execute(
        "SELECT * FROM device_snapshots WHERE config_plant_id=? "
        "ORDER BY fetched_at_utc DESC", (config_plant_id,)).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(row["device_id"], row)
    return [dict(r) for r in latest.values()]


def load_alerts(conn: sqlite3.Connection, config_plant_id: int,
               limit: int = 100) -> list[dict]:
    """Most recent alert rows, newest first."""
    rows = conn.execute(
        "SELECT * FROM alert_snapshots WHERE config_plant_id=? "
        "ORDER BY fetched_at_utc DESC, id DESC LIMIT ?",
        (config_plant_id, limit)).fetchall()
    return [dict(r) for r in rows]
