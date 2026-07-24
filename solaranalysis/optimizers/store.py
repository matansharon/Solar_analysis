"""Persist optimizer inventory + daily energy into app.db (schema v6 tables)."""
from __future__ import annotations
import sqlite3

from .mappers import OptimizerInfo, OptimizerEnergyRow


def save_inventory(conn: sqlite3.Connection, site_id: int,
                   infos: list[OptimizerInfo], now: str) -> None:
    for i in infos:
        conn.execute(
            "INSERT INTO optimizers"
            "(site_id, optimizer_serial, label, name, inverter_serial, inverter_name,"
            " string_label, string_name, model, status, first_seen_utc, last_seen_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id, optimizer_serial) DO UPDATE SET "
            "label=excluded.label, name=excluded.name, "
            "inverter_serial=excluded.inverter_serial, inverter_name=excluded.inverter_name, "
            "string_label=excluded.string_label, string_name=excluded.string_name, "
            "model=excluded.model, status=excluded.status, "
            "last_seen_utc=excluded.last_seen_utc",
            (site_id, i.serial, i.label, i.name, i.inverter_serial, i.inverter_name,
             i.string_label, i.string_name, i.model, i.status, now, now))


def save_energy(conn: sqlite3.Connection, site_id: int, day: str,
                rows: list[OptimizerEnergyRow], now: str) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO optimizer_energy"
            "(site_id, optimizer_serial, day, energy_wh, color, temperature_c, updated_at_utc) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id, optimizer_serial, day) DO UPDATE SET "
            "energy_wh=excluded.energy_wh, color=excluded.color, "
            "temperature_c=excluded.temperature_c, updated_at_utc=excluded.updated_at_utc",
            (site_id, r.optimizer_serial, day, r.energy_wh, r.color, r.temperature_c, now))


def load_inventory(conn: sqlite3.Connection, site_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizers WHERE site_id=? ORDER BY optimizer_serial", (site_id,))]


def load_energy(conn: sqlite3.Connection, site_id: int, day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizer_energy WHERE site_id=? AND day=? ORDER BY optimizer_serial",
        (site_id, day))]


def load_energy_window(conn: sqlite3.Connection, site_id: int,
                       since_day: str) -> list[dict]:
    """All energy rows for a site on/after `since_day`, oldest optimizer/day first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizer_energy WHERE site_id=? AND day>=? "
        "ORDER BY optimizer_serial, day", (site_id, since_day))]
