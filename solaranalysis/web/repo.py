from __future__ import annotations

from . import crypto as _crypto
from ..config import AuthConfig


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _upsert(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)))


def set_setting(conn, key, value) -> None:
    _upsert(conn, key, value)
    conn.commit()


def get_app_settings(conn) -> dict:
    return {
        "model": get_setting(conn, "model", None),
        "max_input_tokens": int(get_setting(conn, "max_input_tokens", "60000")),
        "output_language": get_setting(conn, "output_language", "en"),
    }


def set_app_settings(conn, model, max_input_tokens, output_language) -> None:
    _upsert(conn, "model", model)
    _upsert(conn, "max_input_tokens", int(max_input_tokens))
    _upsert(conn, "output_language", output_language)
    conn.commit()


def get_session_epoch(conn) -> int:
    return int(get_setting(conn, "session_epoch", "0"))


def bump_session_epoch(conn) -> int:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('session_epoch','1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(settings.value AS INTEGER) + 1")
    conn.commit()
    return get_session_epoch(conn)


def get_password_hash(conn):
    return get_setting(conn, "password_hash", None)


def set_password_hash(conn, h) -> None:
    set_setting(conn, "password_hash", h)


def setup_required(conn) -> bool:
    return get_password_hash(conn) is None


def get_setup_token_hash(conn):
    return get_setting(conn, "setup_token", None)


def set_setup_token_hash(conn, h) -> None:
    set_setting(conn, "setup_token", h)


def clear_setup_token(conn) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", ("setup_token",))
    conn.commit()


def plant_public(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "platform": row["platform"],
        "auth_mode": row["auth_mode"],
        "username": row["username"],
        "has_password": row["password_enc"] is not None,
        "has_token": row["token_enc"] is not None,
        "tariff_per_kwh": row["tariff_per_kwh"],
        "currency": row["currency"],
        "enabled": bool(row["enabled"]),
        "last_test_at": row["last_test_at"],
        "last_test_ok": None if row["last_test_ok"] is None else bool(row["last_test_ok"]),
        "last_test_error": row["last_test_error"],
    }


def list_plants(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM plants ORDER BY id").fetchall()
    return [plant_public(r) for r in rows]


def _row(conn, id):
    return conn.execute("SELECT * FROM plants WHERE id=?", (id,)).fetchone()


def get_plant(conn, id):
    r = _row(conn, id)
    return plant_public(r) if r else None


def create_plant(conn, key, data: dict) -> int:
    pw = data.get("password")
    tok = data.get("token")
    cur = conn.execute(
        "INSERT INTO plants(name,platform,auth_mode,username,password_enc,"
        "token_enc,tariff_per_kwh,currency,enabled) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (data["name"], data["platform"], data.get("auth_mode", "password"),
         data.get("username"),
         _crypto.encrypt(key, pw) if pw else None,
         _crypto.encrypt(key, tok) if tok else None,
         data.get("tariff_per_kwh"), data.get("currency"),
         1 if data.get("enabled", True) else 0))
    conn.commit()
    return cur.lastrowid


def update_plant(conn, key, id, data: dict) -> None:
    row = _row(conn, id)
    if row is None:
        raise KeyError(id)
    platform = data.get("platform", row["platform"])
    auth_mode = data.get("auth_mode", row["auth_mode"])
    token_enc = row["token_enc"]
    # Switching away from growatt forces password mode and drops the token.
    if platform != "growatt":
        auth_mode = "password"
        token_enc = None
    password_enc = row["password_enc"]
    if data.get("password"):
        password_enc = _crypto.encrypt(key, data["password"])
    if "token" in data and platform == "growatt":
        token_enc = _crypto.encrypt(key, data["token"]) if data["token"] else token_enc
    username = row["username"]
    if data.get("username"):
        username = data["username"]
    conn.execute(
        "UPDATE plants SET name=?,platform=?,auth_mode=?,username=?,"
        "password_enc=?,token_enc=?,tariff_per_kwh=?,currency=?,enabled=? "
        "WHERE id=?",
        (data.get("name", row["name"]), platform, auth_mode,
         username, password_enc, token_enc,
         data.get("tariff_per_kwh", row["tariff_per_kwh"]),
         data.get("currency", row["currency"]),
         1 if data.get("enabled", bool(row["enabled"])) else 0, id))
    conn.commit()


def delete_plant(conn, id) -> None:
    conn.execute("DELETE FROM plants WHERE id=?", (id,))
    conn.commit()


def set_plant_test_result(conn, id, ok: bool, error, at: str) -> None:
    conn.execute(
        "UPDATE plants SET last_test_at=?,last_test_ok=?,last_test_error=? WHERE id=?",
        (at, 1 if ok else 0, error, id))
    conn.commit()


def load_plant_auth(conn, key, id):
    r = _row(conn, id)
    if r is None:
        return None
    return AuthConfig(
        platform=r["platform"],
        mode=r["auth_mode"],
        username=r["username"],
        password=_crypto.decrypt(key, r["password_enc"]) if r["password_enc"] else None,
        token=_crypto.decrypt(key, r["token_enc"]) if r["token_enc"] else None,
    )


import json as _json


def list_schedules(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    return [{"id": r["id"], "time_of_day": r["time_of_day"],
             "days_of_week": r["days_of_week"], "time_range": r["time_range"],
             "enabled": bool(r["enabled"])} for r in rows]


def create_schedule(conn, data) -> int:
    cur = conn.execute(
        "INSERT INTO schedules(time_of_day,days_of_week,time_range,enabled) "
        "VALUES(?,?,?,?)",
        (data["time_of_day"], data["days_of_week"], data["time_range"],
         1 if data.get("enabled", True) else 0))
    conn.commit()
    return cur.lastrowid


def update_schedule(conn, id, data) -> None:
    row = conn.execute("SELECT * FROM schedules WHERE id=?", (id,)).fetchone()
    if row is None:
        raise KeyError(id)
    conn.execute(
        "UPDATE schedules SET time_of_day=?,days_of_week=?,time_range=?,enabled=? "
        "WHERE id=?",
        (data.get("time_of_day", row["time_of_day"]),
         data.get("days_of_week", row["days_of_week"]),
         data.get("time_range", row["time_range"]),
         1 if data.get("enabled", bool(row["enabled"])) else 0, id))
    conn.commit()


def delete_schedule(conn, id) -> None:
    conn.execute("DELETE FROM schedules WHERE id=?", (id,))
    conn.commit()


def run_public(row) -> dict:
    def _dec(v):
        return _json.loads(v) if v else None
    return {
        "id": row["id"], "status": row["status"], "trigger": row["trigger"],
        "time_range": row["time_range"], "runner_pid": row["runner_pid"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "report_path": row["report_path"], "log_path": row["log_path"],
        "plants_summary": _dec(row["plants_summary"]),
        "skipped_plants": _dec(row["skipped_plants"]),
        "notes": _dec(row["notes"]), "error": row["error"],
        "plant_id": row["plant_id"],
    }


def create_run(conn, trigger, time_range, log_path, started_at, plant_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO runs(status,trigger,time_range,started_at,log_path,plant_id) "
        "VALUES('running',?,?,?,?,?)",
        (trigger, time_range, started_at, log_path, plant_id))
    conn.commit()
    return cur.lastrowid


def set_run_pid(conn, id, pid) -> None:
    conn.execute("UPDATE runs SET runner_pid=? WHERE id=?", (pid, id))
    conn.commit()


def get_run(conn, id):
    r = conn.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone()
    return run_public(r) if r else None


def list_runs(conn, limit=50, offset=0) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset)).fetchall()
    return [run_public(r) for r in rows]


def running_runs(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs WHERE status='running'").fetchall()
    return [run_public(r) for r in rows]


def finalize_run(conn, id, *, status, finished_at, report_path,
                 plants_summary, skipped_plants, notes, error) -> None:
    conn.execute(
        "UPDATE runs SET status=?,finished_at=?,report_path=?,plants_summary=?,"
        "skipped_plants=?,notes=?,error=? WHERE id=?",
        (status, finished_at, report_path,
         _json.dumps(plants_summary) if plants_summary is not None else None,
         _json.dumps(skipped_plants) if skipped_plants is not None else None,
         _json.dumps(notes) if notes is not None else None, error, id))
    conn.commit()


def mark_interrupted(conn, id, finished_at) -> None:
    conn.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE id=?",
                 (finished_at, id))
    conn.commit()
