import json
from pathlib import Path
from solaranalysis.adapters.growatt import map_growatt_web
from solaranalysis.core.schema import DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))


def _mapped():
    return map_growatt_web(_fx("growatt_web_plant.json"), _fx("growatt_web_details.json"),
                           _fx("growatt_web_totals.json"), _fx("growatt_web_devices.json"))


def test_metadata_and_energy():
    pd = _mapped()
    assert pd.source_platform == "growatt"
    assert pd.source_plant_id == "10950561"
    assert pd.plant_name == "Elcam Baram"
    assert pd.peak_power_kwp.value == 50.0
    assert pd.energy_today_kwh.value == 314.2         # already kWh
    assert pd.energy_lifetime_kwh.value == 8414.0
    assert pd.currency == "NIS"


def test_month_year_and_current_power_not_exposed():
    pd = _mapped()
    assert pd.energy_month_kwh.data_source_status == "not_exposed"
    assert pd.energy_year_kwh.data_source_status == "not_exposed"
    assert pd.current_power_kw.data_source_status == "not_exposed"


def test_financial_and_environmental():
    pd = _mapped()
    assert pd.revenue.value == 15902.46
    assert pd.co2_avoided_kg.value == 3365.6
    assert pd.trees_equivalent.value == 463.0


def test_device_status_standby_at_night():
    pd = _mapped()
    assert len(pd.devices) == 1
    d = pd.devices[0]
    assert d.device_id == "MZHRF6K012"
    assert d.manufacturer == "Growatt"
    assert d.status == DeviceStatus.STANDBY          # status code "0"


def test_device_status_online_code_1():
    devs = _fx("growatt_web_devices.json")
    devs["max"][0][2] = "1"
    pd = map_growatt_web(_fx("growatt_web_plant.json"), _fx("growatt_web_details.json"),
                         _fx("growatt_web_totals.json"), devs)
    assert pd.devices[0].status == DeviceStatus.ONLINE


def test_defensive_against_empty():
    pd = map_growatt_web({}, {}, {}, {})
    assert pd.source_platform == "growatt"
    assert pd.energy_today_kwh.value is None
    assert pd.devices == []
    assert pd.alerts == []
