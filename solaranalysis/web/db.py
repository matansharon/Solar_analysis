from __future__ import annotations
import sqlite3

# Migration policy: the DDL below is additive-only (CREATE ... IF NOT EXISTS),
# and init_db executescripts it on every startup, so older DBs pick up new
# tables automatically. Column additions to existing tables use a guarded
# ALTER (see init_db) since CREATE TABLE IF NOT EXISTS can't add columns to
# an existing table.
SCHEMA_VERSION = 7

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
  error TEXT,
  plant_id INTEGER
);
CREATE TABLE IF NOT EXISTS plant_snapshots(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                      -- NULL for CLI runs
  plant_uid TEXT NOT NULL,             -- PlantData.plant_id, e.g. 'growatt-10950561'
  source_platform TEXT NOT NULL,
  fetched_at_utc TEXT NOT NULL,
  time_range TEXT NOT NULL,
  kpis_json TEXT NOT NULL              -- PlantData.to_dict() minus the timeseries lists
);
CREATE INDEX IF NOT EXISTS ix_snapshots_plant
  ON plant_snapshots(plant_uid, fetched_at_utc);
CREATE TABLE IF NOT EXISTS energy_points(
  plant_uid TEXT NOT NULL,
  granularity TEXT NOT NULL CHECK
    (granularity IN ('quarter_hour','hour','day','month','year')),
  period TEXT NOT NULL,                -- 'YYYY-MM-DD' | 'YYYY-MM' | 'YYYY'
  energy_kwh REAL,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (plant_uid, granularity, period)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS device_snapshots(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                      -- NULL for CLI runs
  config_plant_id INTEGER,             -- NULL if not fetched via the web app
  plant_uid TEXT NOT NULL,
  device_id TEXT NOT NULL,
  device_type TEXT NOT NULL,
  model TEXT,
  manufacturer TEXT,
  status TEXT NOT NULL,
  current_power_kw REAL,
  energy_lifetime_kwh REAL,
  temperature_c REAL,
  last_seen_local TEXT,
  fetched_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_device_snapshots_plant
  ON device_snapshots(config_plant_id, device_id, fetched_at_utc);
CREATE TABLE IF NOT EXISTS alert_snapshots(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,
  config_plant_id INTEGER,
  plant_uid TEXT NOT NULL,
  alert_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  code TEXT,
  message TEXT,
  timestamp_local TEXT,
  resolved INTEGER,
  fetched_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alert_snapshots_plant
  ON alert_snapshots(config_plant_id, fetched_at_utc);
CREATE TABLE IF NOT EXISTS power_points(
  plant_uid TEXT NOT NULL,
  config_plant_id INTEGER,
  timestamp_local TEXT NOT NULL,
  power_kw REAL,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (plant_uid, timestamp_local)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS raw_payloads(
  id INTEGER PRIMARY KEY,
  run_id INTEGER,                  -- NULL for CLI runs
  config_plant_id INTEGER,
  plant_uid TEXT NOT NULL,         -- PlantData.plant_id (e.g. 'solaredge-2387929')
  platform TEXT NOT NULL,
  endpoint_label TEXT NOT NULL,    -- short tag from the URL, e.g. 'sitesMeasurements'
  url TEXT,
  method TEXT,
  status INTEGER,
  fetched_at_utc TEXT NOT NULL,
  payload_zjson BLOB NOT NULL      -- zlib-compressed UTF-8 JSON body
);
CREATE INDEX IF NOT EXISTS ix_raw_payloads_plant
  ON raw_payloads(plant_uid, fetched_at_utc);
CREATE TABLE IF NOT EXISTS optimizers(
  site_id INTEGER NOT NULL,
  optimizer_serial TEXT NOT NULL,
  label TEXT,                     -- displayOrder, e.g. '1.4.5'
  name TEXT,                      -- 'Optimizer 1.4.5'
  inverter_serial TEXT,
  inverter_name TEXT,             -- 'Inverter 1'
  string_label TEXT,              -- '1.4'
  string_name TEXT,               -- 'String 1.4'
  model TEXT,                     -- optimizer hardware model, e.g. 'P950-...'
  status TEXT,
  module_manufacturer TEXT,       -- not exposed by the tree; nullable in v1
  module_model TEXT,
  tilt REAL,
  azimuth REAL,
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  PRIMARY KEY (site_id, optimizer_serial)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS optimizer_energy(
  site_id INTEGER NOT NULL,
  optimizer_serial TEXT NOT NULL,
  day TEXT NOT NULL,              -- 'YYYY-MM-DD' (site-local day)
  energy_wh REAL,
  color REAL,                     -- SolarEdge normalized 0..1 (nullable)
  temperature_c REAL,             -- nullable
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (site_id, optimizer_serial, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_optenergy_day ON optimizer_energy(site_id, day);
CREATE TABLE IF NOT EXISTS inverter_channels(
  device_sn TEXT NOT NULL,
  channel_kind TEXT NOT NULL CHECK (channel_kind IN ('mppt','string')),
  channel_no INTEGER NOT NULL,
  parent_channel_no INTEGER,        -- for 'string': its MPPT input; unverifiable
                                    -- on this hardware, so left NULL in C1
  group_voltage REAL,               -- vpvN for 'mppt', vStringN for 'string';
                                    -- exactly-equal values group parallel strings
  plant_uid TEXT,                   -- e.g. 'growatt-10950561'
  lifetime_kwh REAL,                -- epvNTotal; 0 => never produced => excluded
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_kind, channel_no)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS channel_day_energy(
  device_sn TEXT NOT NULL,
  channel_no INTEGER NOT NULL,      -- MPPT inputs only (the only tier with energy)
  day TEXT NOT NULL,                -- 'YYYY-MM-DD', plant-local
  energy_kwh REAL,
  share_of_total REAL,              -- of the plant's PV day total; weather-immune
  peak_w REAL,
  peak_at TEXT,
  producing_minutes INTEGER,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_no, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chanenergy_day ON channel_day_energy(device_sn, day);
CREATE TABLE IF NOT EXISTS channel_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,         -- 'YYYY-MM-DD HH:MM:SS', plant-local
  day TEXT NOT NULL,
  channel_kind TEXT NOT NULL CHECK (channel_kind IN ('mppt','string')),
  channel_no INTEGER NOT NULL,
  power_w REAL,                     -- NULL for 'string' (not reported)
  voltage_v REAL,
  current_a REAL,
  PRIMARY KEY (device_sn, sampled_at, channel_kind, channel_no)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chansamples_day ON channel_samples(device_sn, day);
CREATE TABLE IF NOT EXISTS inverter_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,
  day TEXT NOT NULL,
  pac_w REAL,
  e_ac_today_kwh REAL,
  temp_c REAL, temp2_c REAL, temp3_c REAL, temp4_c REAL, temp5_c REAL,
  pv_iso_kohm REAL,
  gfci_ma REAL,
  status TEXT,
  derating_mode TEXT,
  str_break TEXT, str_unbalance TEXT, str_unmatch TEXT,
  warn_code TEXT, fault_code1 TEXT, fault_code2 TEXT, fault_type TEXT,
  PRIMARY KEY (device_sn, sampled_at)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_invsamples_day ON inverter_samples(device_sn, day);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    for table in ("plant_snapshots", "energy_points"):
        if not _has_column(conn, table, "config_plant_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN config_plant_id INTEGER")
    if not _has_column(conn, "runs", "plant_id"):
        conn.execute("ALTER TABLE runs ADD COLUMN plant_id INTEGER")
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()
