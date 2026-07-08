import hashlib
from fastapi.testclient import TestClient
from solaranalysis.core import measurements
from solaranalysis.core.schema import (
    PlantData, TimeRange, Device, DeviceStatus, Alert, AlertSeverity,
    PowerPoint, EnergyPoint,
)
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def _create_plant(client):
    return client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]


def test_devices_404_for_unknown_plant(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/plants/999/devices").status_code == 404
    assert client.get("/api/plants/999/alerts").status_code == 404
    assert client.get("/api/plants/999/power").status_code == 404
    assert client.get("/api/plants/999/energy").status_code == 404


def test_devices_empty_list_for_never_fetched_plant(tmp_path):
    client, _ = _client(tmp_path)
    pid = _create_plant(client)
    r = client.get(f"/api/plants/{pid}/devices")
    assert r.status_code == 200
    assert r.json() == []


def test_devices_alerts_power_energy_round_trip(tmp_path):
    client, paths = _client(tmp_path)
    pid = _create_plant(client)

    conn = db.connect(paths.db_path)
    pd = PlantData(plant_id="growatt-1", source_platform="growatt",
                   source_plant_id="1", plant_name="G")
    pd.fetched_at_utc = "2026-07-07T10:00:00+00:00"
    pd.config_plant_id = pid
    pd.devices = [Device(device_id="inv-1", status=DeviceStatus.ONLINE)]
    pd.alerts = [Alert(alert_id="A1", severity=AlertSeverity.WARNING, message="Low output")]
    pd.power_timeseries = [PowerPoint("2026-07-07T10:00", 3.1)]
    pd.energy_timeseries = [EnergyPoint("2026-07-06", 42.0, "day")]
    measurements.save_measurements(conn, [pd], TimeRange.LAST_30D, run_id=None)
    conn.commit(); conn.close()

    devices = client.get(f"/api/plants/{pid}/devices").json()
    assert devices[0]["device_id"] == "inv-1" and devices[0]["status"] == "online"

    alerts = client.get(f"/api/plants/{pid}/alerts").json()
    assert alerts[0]["message"] == "Low output"

    power = client.get(f"/api/plants/{pid}/power").json()
    assert power == [{"timestamp_local": "2026-07-07T10:00", "power_kw": 3.1}]

    energy = client.get(f"/api/plants/{pid}/energy").json()
    assert energy == [{"timestamp_local": "2026-07-06", "energy_kwh": 42.0}]
