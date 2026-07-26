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


def test_channel_inventory_finds_six_live_mppt_inputs():
    chans = m.channel_inventory(history_rows())
    mppt = [c for c in chans if c.kind == "mppt"]
    assert [c.no for c in mppt] == [1, 2, 3, 4, 5, 6]
    by_no = {c.no: c for c in mppt}
    assert isinstance(by_no[1], m.ChannelInfo)
    assert by_no[1].lifetime_kwh == 940.8
    assert by_no[4].lifetime_kwh == 616.2
    # group_voltage comes from the daylight (peak) row, not the night rows
    assert by_no[1].group_voltage == 409.0
    assert by_no[6].group_voltage == 412.30002


def test_channel_inventory_excludes_never_produced_inputs():
    chans = m.channel_inventory(history_rows())
    # PV7 reads ~227 V open-circuit with zero current and epv7Total == 0:
    # an unused input, not a fault. It must not appear as a live channel.
    assert 7 not in [c.no for c in chans if c.kind == "mppt"]


def test_channel_inventory_finds_twelve_live_strings_grouped_by_voltage():
    chans = m.channel_inventory(history_rows())
    strings = [c for c in chans if c.kind == "string"]
    assert [c.no for c in strings] == list(range(1, 13))
    by_no = {c.no: c for c in strings}
    # parallel strings on one MPPT share an exactly-equal voltage
    assert by_no[1].group_voltage == by_no[2].group_voltage == 408.30002
    assert by_no[3].group_voltage == by_no[4].group_voltage == 402.7
    assert by_no[11].group_voltage == by_no[12].group_voltage == 414.1
    # strings 13-16 sit at 228.6 V on the never-produced MPPT7 -> excluded
    assert 13 not in by_no


def test_channel_inventory_leaves_parent_no_unset():
    # Pair-current sums do not cleanly biject to ipvN on this hardware, so the
    # string->MPPT link is deliberately not guessed (spec section 3(b)).
    chans = m.channel_inventory(history_rows())
    assert all(c.parent_no is None for c in chans)


def test_channel_inventory_lifetime_only_for_mppt():
    chans = m.channel_inventory(history_rows())
    assert all(c.lifetime_kwh is None for c in chans if c.kind == "string")


def test_best_row_picks_the_highest_pac_sample():
    rows = history_rows()
    assert m.best_row(rows)["time"] == "2026-07-25 12:00:00"
    assert m.best_row([]) == {}


def test_channel_inventory_empty_and_night_only():
    assert m.channel_inventory([]) == []
    night = [r for r in history_rows() if r["time"].endswith("00:02:56")]
    chans = m.channel_inventory(night)
    # lifetime counters are present even at night, so MPPT inputs are still
    # discovered; strings need daylight current and so are not.
    assert [c.no for c in chans if c.kind == "mppt"] == [1, 2, 3, 4, 5, 6]
    assert [c for c in chans if c.kind == "string"] == []
