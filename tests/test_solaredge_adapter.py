import json
from pathlib import Path
from solaranalysis.adapters.solaredge import map_solaredge_plant
from solaranalysis.core.schema import DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))

def test_maps_metadata_and_wh_conversion():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.source_platform == "solaredge"
    assert pd.plant_name == "SolarEdge Roof"
    assert pd.peak_power_kwp.value == 90.0             # already kW
    assert pd.timezone == "Asia/Jerusalem"
    assert pd.current_power_kw.value == 71.0           # 71000 W -> kW
    assert pd.energy_today_kwh.value == 38.0           # 38000 Wh -> kWh
    assert pd.energy_lifetime_kwh.value == 41000.0     # 41,000,000 Wh -> kWh

def test_alerts_and_co2_marked_not_exposed():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.alerts == []
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"

def test_inverters_listed_status_unknown_on_official_path():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    ids = {d.device_id for d in pd.devices}
    assert ids == {"SE-INV-1", "SE-INV-2"}
    assert all(d.status == DeviceStatus.UNKNOWN for d in pd.devices)

def test_revenue_mapped():
    pd = map_solaredge_plant(_fx("solaredge_details.json"),
                             _fx("solaredge_overview.json"),
                             _fx("solaredge_inventory.json"))
    assert pd.revenue.value == 5390.0
    assert pd.currency == "ILS"
