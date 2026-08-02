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


# --- derived series for the analyzer -------------------------------------
# The analyzer is pure and DB-free, so the three loaders below do the heavy
# aggregation in SQL and hand back small row lists. They are deliberately not
# stored as columns: `channel_samples` already holds everything they derive,
# so materializing them would be a schema migration buying nothing until the
# sample-retention prune lands (see NextTODO).

def load_peak_string_samples(conn: sqlite3.Connection, device_sn: str,
                             since_day: str, power_frac: float) -> list[dict]:
    """Individual-string samples taken while the inverter was at >= `power_frac`
    of that day's own peak AC power, for every day on/after `since_day`.

    Restricting to the high-power part of the day is what makes the pair
    imbalance metric stable -- measured at a single peak instant the metric's
    per-pair spread is 5.54pp, across this window it is 1.93pp (see the
    2026-08-02 C2 calibration). Voltage comes along because pairs are grouped
    on it; `inverter_channels.group_voltage` is a last-write-wins snapshot and
    must not be used for history."""
    return [dict(r) for r in conn.execute(
        "WITH peak AS ("
        "  SELECT day, MAX(pac_w) AS pk FROM inverter_samples"
        "   WHERE device_sn=:sn AND day>=:since GROUP BY day),"
        " hot AS ("
        "  SELECT i.sampled_at FROM inverter_samples i JOIN peak p ON i.day=p.day"
        "   WHERE i.device_sn=:sn AND i.day>=:since AND p.pk>0"
        "     AND i.pac_w >= :frac * p.pk) "
        "SELECT c.day, c.sampled_at, c.channel_no, c.voltage_v, c.current_a "
        "  FROM channel_samples c JOIN hot h ON c.sampled_at=h.sampled_at "
        " WHERE c.device_sn=:sn AND c.day>=:since AND c.channel_kind='string' "
        " ORDER BY c.day, c.sampled_at, c.channel_no",
        {"sn": device_sn, "since": since_day, "frac": power_frac})]


def load_hourly_channel_power(conn: sqlite3.Connection, device_sn: str,
                              since_day: str) -> list[dict]:
    """Per day, per clock hour, per MPPT input: summed DC power.

    Aggregated in SQL -- a 30-day window is ~155k sample rows but only ~4k
    aggregated ones. The analyzer turns these into per-hour shares."""
    return [dict(r) for r in conn.execute(
        "SELECT day, CAST(substr(sampled_at, 12, 2) AS INTEGER) AS hour, "
        "       channel_no, SUM(power_w) AS power_w "
        "  FROM channel_samples "
        " WHERE device_sn=? AND day>=? AND channel_kind='mppt' "
        " GROUP BY day, hour, channel_no ORDER BY day, hour, channel_no",
        (device_sn, since_day))]


def load_inverter_day_health(conn: sqlite3.Connection, device_sn: str,
                             since_day: str) -> list[dict]:
    """Per day: how many samples carried each non-zero fault/status flag, and
    the daily max of each real temperature sensor.

    Fault counts span the whole day -- a fault at any hour matters. Temperature
    maxima are taken over `status='1'` samples only: that is the portal's own
    producing flag, and it is also the filter that perfectly excludes the
    65,530 kOhm sentinel (0 of 3,032 producing samples carry it). `temp4_c` is
    omitted on purpose -- it is constant 0.0 on this hardware, the sensor is
    absent, and a naive outlier rule would flag it as cold forever."""
    return [dict(r) for r in conn.execute(
        # COALESCE, because a flag the payload omitted is stored NULL and
        # `SUM(NULL <> '0')` is NULL, not 0 -- absence of a reading is not a
        # fault, and must not read as one.
        "SELECT day, "
        "  SUM(COALESCE(str_break,    '0') <> '0') AS n_str_break, "
        "  SUM(COALESCE(str_unbalance,'0') <> '0') AS n_str_unbalance, "
        "  SUM(COALESCE(str_unmatch,  '0') <> '0') AS n_str_unmatch, "
        "  SUM(COALESCE(warn_code,    '0') <> '0') AS n_warn_code, "
        "  SUM(COALESCE(fault_code1,  '0') <> '0') AS n_fault_code1, "
        "  SUM(COALESCE(fault_code2,  '0') <> '0') AS n_fault_code2, "
        "  SUM(COALESCE(fault_type,   '0') <> '0') AS n_fault_type, "
        "  SUM(COALESCE(derating_mode,'0') <> '0') AS n_derating_mode, "
        "  MAX(CASE WHEN status='1' THEN temp_c  END) AS temp_c, "
        "  MAX(CASE WHEN status='1' THEN temp2_c END) AS temp2_c, "
        "  MAX(CASE WHEN status='1' THEN temp3_c END) AS temp3_c, "
        "  MAX(CASE WHEN status='1' THEN temp5_c END) AS temp5_c "
        "  FROM inverter_samples WHERE device_sn=? AND day>=? "
        " GROUP BY day ORDER BY day", (device_sn, since_day))]
