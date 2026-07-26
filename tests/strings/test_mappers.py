import json
from pathlib import Path

from solaranalysis.strings import mappers as m

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def history_payload() -> dict:
    return json.loads((FIXTURES / "growatt_max_history.json").read_text(encoding="utf-8"))


def history_rows() -> list[dict]:
    return history_payload()["obj"]["datas"]


def device_payload() -> dict:
    return json.loads(
        (FIXTURES / "growatt_web_devices_list.json").read_text(encoding="utf-8"))


def test_parse_devices_reads_serial_model_and_datalogger():
    devs = m.parse_devices(device_payload())
    assert len(devs) == 1
    d = devs[0]
    assert isinstance(d, m.InverterInfo)
    assert d.serial == "SN-TEST-1"
    assert d.model == "MAX 70KTL3 LV"
    assert d.datalog_sn == "DL-TEST-1"
    assert d.nominal_power_w == 70000.0     # arrives as the string "70000.0"
    assert d.plant_id == "10950561"


def test_parse_devices_skips_rows_without_a_serial():
    payload = {"obj": {"datas": [{"deviceModel": "MAX"}, {"sn": "OK"}, "junk"]}}
    assert [d.serial for d in m.parse_devices(payload)] == ["OK"]


def test_parse_devices_empty():
    assert m.parse_devices({}) == []
    assert m.parse_devices({"obj": {}}) == []


def test_sample_day_parses_time_not_the_zero_based_calendar():
    rows = history_rows()
    # calendar.month is 6 for July -- using it would yield '2026-06-25'
    assert rows[0]["calendar"]["month"] == 6
    assert m.sample_day(rows[0]) == "2026-07-25"
    assert m.sample_day({}) is None
    assert m.sample_day({"time": "nope"}) is None


def test_num_coerces_strings_and_rejects_junk():
    assert m._num(1) == 1.0
    assert m._num("70000.0") == 70000.0
    assert m._num("") is None
    assert m._num(None) is None
    assert m._num("abc") is None


def test_text_preserves_the_raw_token():
    assert m._text("0") == "0"
    assert m._text(0) == "0"
    assert m._text(None) is None
