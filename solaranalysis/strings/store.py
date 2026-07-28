"""Persist Growatt channel inventory, daily energy and 5-minute samples into
app.db (schema v7 tables). Every write is an upsert, so re-collecting a day is
safe and idempotent."""
from __future__ import annotations
import sqlite3

from .mappers import (ChannelInfo, ChannelDayEnergy, ChannelSample,
                      InverterSample)


def save_channels(conn: sqlite3.Connection, device_sn: str, plant_uid: str | None,
                  channels: list[ChannelInfo], now: str) -> None:
    conn.executemany(
        "INSERT INTO inverter_channels"
        "(device_sn, channel_kind, channel_no, parent_channel_no, group_voltage,"
        " plant_uid, lifetime_kwh, first_seen_utc, last_seen_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, channel_kind, channel_no) DO UPDATE SET "
        "parent_channel_no=excluded.parent_channel_no, "
        "group_voltage=excluded.group_voltage, plant_uid=excluded.plant_uid, "
        "lifetime_kwh=excluded.lifetime_kwh, last_seen_utc=excluded.last_seen_utc",
        [(device_sn, c.kind, c.no, c.parent_no, c.group_voltage, plant_uid,
          c.lifetime_kwh, now, now) for c in channels])


def save_day_energy(conn: sqlite3.Connection, device_sn: str, day: str,
                    rows: list[ChannelDayEnergy], now: str) -> None:
    conn.executemany(
        "INSERT INTO channel_day_energy"
        "(device_sn, channel_no, day, energy_kwh, share_of_total, peak_w,"
        " peak_at, producing_minutes, updated_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, channel_no, day) DO UPDATE SET "
        "energy_kwh=excluded.energy_kwh, share_of_total=excluded.share_of_total, "
        "peak_w=excluded.peak_w, peak_at=excluded.peak_at, "
        "producing_minutes=excluded.producing_minutes, "
        "updated_at_utc=excluded.updated_at_utc",
        [(device_sn, r.channel_no, day, r.energy_kwh, r.share_of_total, r.peak_w,
          r.peak_at, r.producing_minutes, now) for r in rows])


def save_channel_samples(conn: sqlite3.Connection, device_sn: str,
                         samples: list[ChannelSample]) -> None:
    """`channel_samples` is a WITHOUT ROWID table whose primary key leads with
    `sampled_at`. The collector's rows arrive newest-first, so writing them as
    given inserts strictly descending keys -- SQLite can only fast-path
    appends at the right edge of a B-tree, so descending inserts split pages
    mid-way and settle at roughly half the fill of ascending ones. Sorting
    ascending by primary-key order before the executemany keeps every insert
    an append."""
    ordered = sorted(samples, key=lambda s: (s.sampled_at, s.kind, s.no))
    conn.executemany(
        "INSERT INTO channel_samples"
        "(device_sn, sampled_at, day, channel_kind, channel_no, power_w,"
        " voltage_v, current_a) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_sn, sampled_at, channel_kind, channel_no) DO UPDATE SET "
        "power_w=excluded.power_w, voltage_v=excluded.voltage_v, "
        "current_a=excluded.current_a",
        [(device_sn, s.sampled_at, s.day, s.kind, s.no, s.power_w, s.voltage_v,
          s.current_a) for s in ordered])


_INV_COLS = ("pac_w", "e_ac_today_kwh", "temp_c", "temp2_c", "temp3_c",
             "temp4_c", "temp5_c", "pv_iso_kohm", "gfci_ma", "status",
             "derating_mode", "str_break", "str_unbalance", "str_unmatch",
             "warn_code", "fault_code1", "fault_code2", "fault_type")


def save_inverter_samples(conn: sqlite3.Connection, device_sn: str,
                          samples: list[InverterSample]) -> None:
    """Same descending-insert page-split problem as `save_channel_samples`
    (see there): `inverter_samples` is WITHOUT ROWID with PK (device_sn,
    sampled_at), so sort ascending by `sampled_at` before the executemany."""
    ordered = sorted(samples, key=lambda s: s.sampled_at)
    sets = ", ".join(f"{c}=excluded.{c}" for c in _INV_COLS)
    cols = ", ".join(_INV_COLS)
    marks = ",".join("?" * (3 + len(_INV_COLS)))
    conn.executemany(
        f"INSERT INTO inverter_samples(device_sn, sampled_at, day, {cols}) "
        f"VALUES ({marks}) "
        f"ON CONFLICT(device_sn, sampled_at) DO UPDATE SET {sets}",
        [(device_sn, s.sampled_at, s.day, *(getattr(s, c) for c in _INV_COLS))
         for s in ordered])


def load_channels(conn: sqlite3.Connection, device_sn: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM inverter_channels WHERE device_sn=? "
        "ORDER BY channel_kind, channel_no", (device_sn,))]


def load_day_energy(conn: sqlite3.Connection, device_sn: str, day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_day_energy WHERE device_sn=? AND day=? "
        "ORDER BY channel_no", (device_sn, day))]


def load_energy_window(conn: sqlite3.Connection, device_sn: str,
                       since_day: str) -> list[dict]:
    """All daily energy rows on/after `since_day`, oldest channel/day first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_day_energy WHERE device_sn=? AND day>=? "
        "ORDER BY channel_no, day", (device_sn, since_day))]


def load_channel_samples(conn: sqlite3.Connection, device_sn: str,
                         day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM channel_samples WHERE device_sn=? AND day=? "
        "ORDER BY sampled_at, channel_kind, channel_no", (device_sn, day))]


def load_inverter_samples(conn: sqlite3.Connection, device_sn: str,
                          day: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM inverter_samples WHERE device_sn=? AND day=? "
        "ORDER BY sampled_at", (device_sn, day))]
