import json
import zlib

from solaranalysis.web import db
from solaranalysis.core import measurements
from solaranalysis.core.schema import PlantData, RawPayload, TimeRange


def _plant_with_raw(records):
    pd = PlantData("solaredge-2387929", "solaredge", "2387929", "Baram")
    pd.config_plant_id = 7
    pd.fetched_at_utc = "2026-07-23T03:00:00+00:00"
    pd.raw_payloads = records
    return pd


def test_save_measurements_persists_raw_payloads():
    conn = db.connect(":memory:")
    db.init_db(conn)
    pd = _plant_with_raw([
        RawPayload("sitesMeasurements", "https://h/s/sitesMeasurements",
                   "POST", 200, [{"energyToday": 5.0}]),
    ])
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=42)
    conn.commit()
    row = conn.execute(
        "SELECT run_id, plant_uid, platform, endpoint_label, method, status, payload_zjson"
        " FROM raw_payloads").fetchone()
    assert row["run_id"] == 42
    assert row["plant_uid"] == "solaredge-2387929"
    assert row["platform"] == "solaredge"
    assert row["endpoint_label"] == "sitesMeasurements"
    assert row["method"] == "POST"
    body = json.loads(zlib.decompress(row["payload_zjson"]).decode("utf-8"))
    assert body == [{"energyToday": 5.0}]


def test_save_measurements_skips_unserializable_body_non_fatal():
    conn = db.connect(":memory:")
    db.init_db(conn)
    pd = _plant_with_raw([
        RawPayload("bad", "https://h/s/bad", "GET", 200, {1, 2, 3}),  # set: not JSON
        RawPayload("good", "https://h/s/good", "GET", 200, {"ok": 1}),
    ])
    measurements.save_measurements(conn, [pd], TimeRange.SNAPSHOT, run_id=1)
    conn.commit()
    labels = [r["endpoint_label"] for r in conn.execute(
        "SELECT endpoint_label FROM raw_payloads")]
    assert labels == ["good"]  # the unserializable one was skipped, not fatal
