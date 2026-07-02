import json
from pathlib import Path
from solaranalysis.adapters.solaredge import map_solaredge_fleet
from solaranalysis.core.schema import DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))


def _mapped():
    return map_solaredge_fleet(_fx("solaredge_site.json"), _fx("solaredge_meas.json"),
                               _fx("solaredge_env.json"), _fx("solaredge_live.json"))


def test_metadata_and_energy_kwh_no_conversion():
    pd = _mapped()
    assert pd.source_platform == "solaredge"
    assert pd.source_plant_id == "2387929"
    assert pd.peak_power_kwp.value == 208.39          # already kW
    assert pd.timezone == "Asia/Jerusalem"
    assert pd.install_date == "2021-07-27T00:00:00Z"
    # sitesMeasurements is already kWh — verbatim, no /1000.
    assert pd.energy_today_kwh.value == 1393.099
    assert pd.energy_month_kwh.value == 2774.325
    assert pd.energy_lifetime_kwh.value == 1630497.6


def test_current_power_w_to_kw():
    pd = _mapped()
    assert pd.current_power_kw.value == 0.0           # 0 W (night) -> 0 kW
    assert pd.current_power_kw.data_source_status == "ok"


def test_co2_and_trees_from_env_benefits():
    pd = _mapped()
    assert pd.co2_avoided_kg.value == 639155.06
    assert pd.trees_equivalent.value == 19076.82


def test_devices_from_count_online_when_active():
    pd = _mapped()
    assert len(pd.devices) == 3                       # inverterCount
    assert all(d.status == DeviceStatus.ONLINE for d in pd.devices)  # site ACTIVE
    assert any("inferred from site ACTIVE" in f for f in pd.data_quality_flags)


def test_no_alerts_when_count_zero():
    pd = _mapped()
    assert pd.alerts == []


def test_alerts_created_from_count():
    site = _fx("solaredge_site.json")
    site["alertsCount"] = 2
    pd = map_solaredge_fleet(site, _fx("solaredge_meas.json"),
                             _fx("solaredge_env.json"), _fx("solaredge_live.json"))
    assert len(pd.alerts) == 2


def test_devices_unknown_when_not_active():
    site = _fx("solaredge_site.json")
    site["status"] = "PENDING"
    pd = map_solaredge_fleet(site, _fx("solaredge_meas.json"),
                             _fx("solaredge_env.json"), _fx("solaredge_live.json"))
    assert all(d.status == DeviceStatus.UNKNOWN for d in pd.devices)


def test_defensive_against_empty_payloads():
    pd = map_solaredge_fleet({}, {}, {}, {})
    assert pd.source_platform == "solaredge"
    assert pd.energy_today_kwh.value is None
    assert pd.devices == []
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"
