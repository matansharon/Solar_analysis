from __future__ import annotations
import sqlite3

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS plants(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL CHECK (platform IN ('solaredge','growatt','sma')),
  auth_mode TEXT NOT NULL DEFAULT 'password' CHECK (auth_mode IN ('password','token')),
  username TEXT,
  password_enc BLOB,
  token_enc BLOB,
  tariff_per_kwh REAL,
  currency TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_test_at TEXT,
  last_test_ok INTEGER,
  last_test_error TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY,
  time_of_day TEXT NOT NULL,
  days_of_week TEXT NOT NULL,
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('running','success','partial','failed','cancelled','interrupted')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduled')),
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  runner_pid INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  report_path TEXT,
  log_path TEXT NOT NULL,
  plants_summary TEXT,
  skipped_plants TEXT,
  notes TEXT,
  error TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()
