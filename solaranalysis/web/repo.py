from __future__ import annotations
import sqlite3


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)))
    conn.commit()


def get_app_settings(conn) -> dict:
    return {
        "model": get_setting(conn, "model", None),
        "max_input_tokens": int(get_setting(conn, "max_input_tokens", "60000")),
        "output_language": get_setting(conn, "output_language", "en"),
    }


def set_app_settings(conn, model, max_input_tokens, output_language) -> None:
    set_setting(conn, "model", model)
    set_setting(conn, "max_input_tokens", int(max_input_tokens))
    set_setting(conn, "output_language", output_language)


def get_session_epoch(conn) -> int:
    return int(get_setting(conn, "session_epoch", "0"))


def bump_session_epoch(conn) -> int:
    nxt = get_session_epoch(conn) + 1
    set_setting(conn, "session_epoch", nxt)
    return nxt


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
    conn.execute("DELETE FROM settings WHERE key='setup_token'")
    conn.commit()
