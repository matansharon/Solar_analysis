from solaranalysis.core.schema import (
    PlantData, Device, Alert, Metric, EnergyPoint, PowerPoint,
    TimeRange, DeviceStatus, AlertSeverity,
)

def test_plantdata_minimal_construct_and_serialize():
    pd = PlantData(
        plant_id="se-1",
        source_platform="solaredge",
        source_plant_id="123",
        plant_name="Roof",
        peak_power_kwp=Metric(100.0, "kWp"),
        currency="ILS",
    )
    d = pd.to_dict()
    assert d["plant_id"] == "se-1"
    assert d["peak_power_kwp"]["value"] == 100.0
    assert d["peak_power_kwp"]["data_source_status"] == "ok"
    assert d["devices"] == []
    assert d["alerts"] == []

def test_metric_missing_marks_status():
    m = Metric(None, "kWh", data_source_status="not_exposed")
    assert m.value is None
    assert m.data_source_status == "not_exposed"

def test_enums_have_expected_values():
    assert TimeRange.LAST_12MO.value == "12mo"
    assert DeviceStatus.FAULT.value == "fault"
    assert AlertSeverity.CRITICAL.value == "critical"

def test_extras_round_trip_through_to_dict():
    pd = PlantData(plant_id="sma-1", source_platform="sma", source_plant_id="G1",
                   plant_name="Barn")
    pd.extras["yield_yesterday_kwh"] = 12.5
    pd.extras["note"] = "ok"
    d = pd.to_dict()
    assert d["extras"] == {"yield_yesterday_kwh": 12.5, "note": "ok"}

def test_device_and_alert_serialize():
    dev = Device(device_id="SN1", device_type="inverter", status=DeviceStatus.ONLINE,
                 current_power_kw=3.2)
    al = Alert(alert_id="a1", severity=AlertSeverity.WARNING, code="W01",
               message="grid", timestamp_local="2026-06-01T10:00:00", resolved=False)
    pd = PlantData(plant_id="g-1", source_platform="growatt", source_plant_id="9",
                   plant_name="G", devices=[dev], alerts=[al])
    d = pd.to_dict()
    assert d["devices"][0]["status"] == "online"
    assert d["alerts"][0]["severity"] == "warning"
