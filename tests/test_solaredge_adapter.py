import json
from pathlib import Path
from solaranalysis.adapters.solaredge import (
    map_solaredge_fleet, map_solaredge_equipment, map_solaredge_alerts,
    map_solaredge_comparative_monthly, map_solaredge_inverter_power_daily,
)
from solaranalysis.core.schema import DeviceStatus, AlertSeverity

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


# ---------------------------------------------------------------------------
# Deep-fetch pure mappers (equipment, alerts, monthly history, hourly power)
# ---------------------------------------------------------------------------

def test_equipment_maps_real_serials_and_connectivity_status():
    devs = map_solaredge_equipment(_fx("solaredge_equipment.json"),
                                   {"connectivityStatus": "ONLINE", "active": True})
    assert [d.device_id for d in devs] == ["11111111-AA", "22222222-BB", "33333333-CC"]
    assert all(d.model == "SE50K" for d in devs)
    assert all(d.status == DeviceStatus.ONLINE for d in devs)


def test_equipment_status_unknown_without_connectivity():
    devs = map_solaredge_equipment(_fx("solaredge_equipment.json"), None)
    assert all(d.status == DeviceStatus.UNKNOWN for d in devs)


def test_alerts_map_severity_and_fallback_fields():
    alerts = map_solaredge_alerts(_fx("solaredge_alerts.json"))
    assert len(alerts) == 2
    first, second = alerts
    assert first.alert_id == "987"
    assert first.severity == AlertSeverity.ERROR      # HIGH
    assert first.code == "INVERTER_BELOW_THRESHOLD_LIMIT"
    assert first.message == "Inverter production below threshold"
    assert first.timestamp_local == "2026-07-05T08:00:00+03:00"
    assert second.alert_id == "988"
    assert second.severity == AlertSeverity.INFO      # low
    assert second.message == "Communication gap"


def test_comparative_monthly_is_wh_to_kwh():
    pts = map_solaredge_comparative_monthly(_fx("solaredge_comparative_monthly.json"))
    assert len(pts) == 24
    assert pts[0].timestamp_local == "2025-01"
    assert pts[0].energy_kwh == 22406.56              # Wh -> kWh
    assert pts[-1].timestamp_local == "2026-12"
    assert all(p.granularity == "month" for p in pts)


def test_inverter_power_integrates_to_daily_kwh():
    pts = map_solaredge_inverter_power_daily(_fx("solaredge_inverter_power.json"))
    # 2026-07-08 has only null samples (comms gap) -> skipped, not zero.
    assert [p.timestamp_local for p in pts] == ["2026-07-06", "2026-07-07"]
    # 07-06: (40+41+39) + (50+49+51) kW × 1h = 270 kWh; nulls add nothing.
    assert pts[0].energy_kwh == 270.0
    assert pts[1].energy_kwh == 126.0
    assert all(p.granularity == "day" for p in pts)


def test_deep_mappers_defensive_against_empty_payloads():
    assert map_solaredge_equipment({}, {}) == []
    assert map_solaredge_equipment(None, None) == []
    assert map_solaredge_alerts(None) == []
    assert map_solaredge_alerts({"topAlerts": ["junk"]}) == []
    assert map_solaredge_comparative_monthly(None) == []
    assert map_solaredge_inverter_power_daily(None) == []
    assert map_solaredge_inverter_power_daily(
        {"invertersDatedPowerList": ["junk", {"measurementTime": "bad"}]}) == []
