import json
from pathlib import Path
from solaranalysis.adapters.growatt import map_growatt_plant, GrowattAdapter
from solaranalysis.core.schema import DeviceStatus, AlertSeverity
from solaranalysis.core.session_store import SessionStore
from solaranalysis.config import AuthConfig

FX = json.loads((Path(__file__).parent / "fixtures" / "growatt_plant.json").read_text(encoding="utf-8"))

def test_map_basic_metadata_and_units():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.source_platform == "growatt"
    assert pd.plant_name == "Growatt Roof"
    assert pd.peak_power_kwp.value == 100.0            # 100000 W -> kWp
    assert pd.energy_today_kwh.value == 42.5           # already kWh
    assert pd.energy_lifetime_kwh.value == 125000.0
    assert pd.current_power_kw.value == 63.5           # 63500 W -> kW

def test_map_year_is_not_exposed():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.energy_year_kwh.value is None
    assert pd.energy_year_kwh.data_source_status == "not_exposed"

def test_device_status_and_fault_alert():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    statuses = {d.device_id: d.status for d in pd.devices}
    assert statuses["INV-A"] == DeviceStatus.ONLINE
    assert statuses["INV-B"] == DeviceStatus.FAULT
    # a non-zero warnCode becomes an alert
    codes = {a.code for a in pd.alerts}
    assert "203" in codes
    assert any(a.severity in (AlertSeverity.ERROR, AlertSeverity.WARNING) for a in pd.alerts)

def test_empty_latlon_becomes_none():
    pd = map_growatt_plant(FX["plant_meta"], FX["energy"], FX["devices"])
    assert pd.latitude is None and pd.longitude is None

def test_login_authenticates_client(tmp_path):
    class FakeClient:
        def __init__(self):
            self.login_called_with = None
        def login(self, u, p):
            self.login_called_with = (u, p)
            return {"success": True, "user": {"id": "u1"}}

    fake = FakeClient()
    auth = AuthConfig("growatt", username="u", password="p")
    adapter = GrowattAdapter(auth, SessionStore(str(tmp_path)), client=fake)
    adapter.login()
    assert fake.login_called_with == ("u", "p")
    assert adapter._user_id == "u1"
