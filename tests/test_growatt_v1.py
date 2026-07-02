import json
from pathlib import Path

import pytest

from solaranalysis.adapters._growatt_v1 import GrowattV1Client, GrowattV1Error
from solaranalysis.adapters.growatt import map_growatt_v1
from solaranalysis.core.schema import DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"


def _fx(name):
    return json.loads((FXDIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# GrowattV1Client / _get
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}
        self.calls = []

    def update_headers(self, d):
        self.headers.update(d)

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(self._payload)


class FakeSessionHeaders(dict):
    """Minimal stand-in for requests.Session().headers (dict-like with .update)."""


def _make_fake_session(payload):
    session = FakeSession(payload)
    session.headers = FakeSessionHeaders()
    return session


def test_get_success_returns_data_and_sets_token_header_and_params():
    fake = _make_fake_session({"data": {"ok": 1}, "error_code": 0, "error_msg": ""})
    client = GrowattV1Client(token="TESTTOKEN", session=fake)

    result = client._get("plant/details", {"plant_id": "9001"})

    assert result == {"ok": 1}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == client.base + "plant/details"
    assert call["params"] == {"plant_id": "9001"}
    assert fake.headers.get("token") == "TESTTOKEN"


def test_get_error_code_raises_growatt_v1_error():
    fake = _make_fake_session({"data": None, "error_code": 10011, "error_msg": "permission denied"})
    client = GrowattV1Client(token="TESTTOKEN", session=fake)

    with pytest.raises(GrowattV1Error) as exc_info:
        client._get("plant/list")

    assert exc_info.value.error_code == 10011
    assert exc_info.value.error_msg == "permission denied"


def test_client_methods_build_expected_paths_and_params():
    fake = _make_fake_session({"data": {"plants": []}, "error_code": 0, "error_msg": ""})
    client = GrowattV1Client(token="TESTTOKEN", session=fake)

    client.plant_list()
    client.plant_details("9001")
    client.plant_energy_overview("9001")
    client.device_list("9001")

    urls = [c["url"] for c in fake.calls]
    assert client.base + "plant/list" in urls
    assert client.base + "plant/details" in urls
    assert client.base + "plant/data" in urls
    assert client.base + "device/list" in urls


# ---------------------------------------------------------------------------
# map_growatt_v1
# ---------------------------------------------------------------------------

def test_map_growatt_v1_basic_metadata_and_energy():
    details = _fx("growatt_v1_details.json")
    overview = _fx("growatt_v1_overview.json")
    devices = _fx("growatt_v1_devices.json")["devices"]

    pd = map_growatt_v1(details, overview, devices)

    assert pd.source_platform == "growatt"
    assert pd.plant_name == "Growatt Roof"
    assert pd.energy_today_kwh.value == 42.5          # already kWh, no conversion
    assert pd.energy_month_kwh.value == 980.0
    assert pd.energy_lifetime_kwh.value == 125000.0
    assert pd.current_power_kw.value == 63.5          # CONFIRM LIVE: treated as kW


def test_map_growatt_v1_device_mapping():
    details = _fx("growatt_v1_details.json")
    overview = _fx("growatt_v1_overview.json")
    devices = _fx("growatt_v1_devices.json")["devices"]

    pd = map_growatt_v1(details, overview, devices)

    by_id = {d.device_id: d for d in pd.devices}
    assert set(by_id.keys()) == {"ZT00100001", "ZT00100002"}
    # lost == False, status == 1 -> ONLINE
    assert by_id["ZT00100001"].status == DeviceStatus.ONLINE
    # lost == True -> OFFLINE regardless of status field
    assert by_id["ZT00100002"].status == DeviceStatus.OFFLINE
    assert by_id["ZT00100001"].manufacturer == "Growatt"
    assert by_id["ZT00100001"].last_seen_local == "2026-07-01 12:00:00"


def test_map_growatt_v1_alerts_empty_and_co2():
    details = _fx("growatt_v1_details.json")
    overview = _fx("growatt_v1_overview.json")
    devices = _fx("growatt_v1_devices.json")["devices"]

    pd = map_growatt_v1(details, overview, devices)

    assert pd.alerts == []
    assert pd.co2_avoided_kg.value == 1024.3
    assert pd.co2_avoided_kg.data_source_status == "ok"


def test_map_growatt_v1_empty_latlon_and_peak_power():
    details = _fx("growatt_v1_details.json")
    overview = _fx("growatt_v1_overview.json")
    devices = _fx("growatt_v1_devices.json")["devices"]

    pd = map_growatt_v1(details, overview, devices)

    assert pd.latitude is None and pd.longitude is None
    assert pd.peak_power_kwp.value == 100.0


def test_map_growatt_v1_missing_co2_marked_not_exposed():
    details = _fx("growatt_v1_details.json")
    overview = {k: v for k, v in _fx("growatt_v1_overview.json").items() if k != "co2"}
    devices = _fx("growatt_v1_devices.json")["devices"]

    pd = map_growatt_v1(details, overview, devices)

    assert pd.co2_avoided_kg.value is None
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"


def test_map_growatt_v1_defensive_against_missing_fields():
    # Mapper must not blow up if details/overview are mostly empty.
    pd = map_growatt_v1({}, {}, [])
    assert pd.source_platform == "growatt"
    assert pd.energy_today_kwh.value is None
    assert pd.devices == []
    assert pd.alerts == []
