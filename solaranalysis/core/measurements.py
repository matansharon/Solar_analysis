"""Persist fetched plant measurements so history accumulates across runs.

Stdlib-sqlite3 only (no web dependencies) so the CLI can import it too. The
schema (plant_snapshots + energy_points) lives in web/db.py's DDL; callers
run db.init_db(conn) before saving.

energy_points is upsert-keyed on (plant, granularity, period), latest value
wins — today's partial figure self-corrects on the next run.
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
            "(run_id, plant_uid, source_platform, fetched_at_utc, time_range, kpis_json) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, pd.plant_id, pd.source_platform,
             pd.fetched_at_utc or now, time_range.value,
             json.dumps(kpis, ensure_ascii=False)))
        for p in pd.energy_timeseries:
            if p.energy_kwh is None:
                continue
            conn.execute(
                "INSERT INTO energy_points"
                "(plant_uid, granularity, period, energy_kwh, updated_at_utc) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(plant_uid, granularity, period) DO UPDATE SET "
                "energy_kwh=excluded.energy_kwh, "
                "updated_at_utc=excluded.updated_at_utc",
                (pd.plant_id, p.granularity, p.timestamp_local,
                 p.energy_kwh, now))


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
