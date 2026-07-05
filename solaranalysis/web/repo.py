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
    conn.execute(
        "UPDATE plants SET name=?,platform=?,auth_mode=?,username=?,"
        "password_enc=?,token_enc=?,tariff_per_kwh=?,currency=?,enabled=? "
        "WHERE id=?",
        (data.get("name", row["name"]), platform, auth_mode,
         data.get("username", row["username"]), password_enc, token_enc,
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
