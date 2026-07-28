from solaranalysis.web import db
from solaranalysis.strings import store
from solaranalysis.strings.mappers import (
    ChannelInfo, ChannelDayEnergy, ChannelSample, InverterSample)


def _conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def test_save_channels_upserts_and_preserves_first_seen():
    conn = _conn()
    chans = [ChannelInfo(kind="mppt", no=1, group_voltage=409.0, lifetime_kwh=940.8),
             ChannelInfo(kind="string", no=1, group_voltage=408.30002)]
    store.save_channels(conn, "SN-A", "growatt-10950561", chans,
                        now="2026-07-10T00:00:00+00:00")
    chans[0].lifetime_kwh = 998.1
    store.save_channels(conn, "SN-A", "growatt-10950561", chans,
                        now="2026-07-26T00:00:00+00:00")
    rows = store.load_channels(conn, "SN-A")
    assert len(rows) == 2
    mppt = next(r for r in rows if r["channel_kind"] == "mppt")
    assert mppt["channel_no"] == 1
    assert mppt["group_voltage"] == 409.0
    assert mppt["lifetime_kwh"] == 998.1                        # refreshed
    assert mppt["plant_uid"] == "growatt-10950561"
    assert mppt["parent_channel_no"] is None
    assert mppt["first_seen_utc"] == "2026-07-10T00:00:00+00:00"  # preserved
    assert mppt["last_seen_utc"] == "2026-07-26T00:00:00+00:00"   # refreshed


def test_mppt_and_string_channel_one_do_not_collide():
    conn = _conn()
    store.save_channels(conn, "SN-A", "uid", [
        ChannelInfo(kind="mppt", no=1, lifetime_kwh=1.0),
        ChannelInfo(kind="string", no=1)], now="n")
    kinds = sorted(r["channel_kind"] for r in store.load_channels(conn, "SN-A"))
    assert kinds == ["mppt", "string"]


def test_save_day_energy_upserts_latest_value():
    conn = _conn()
    store.save_day_energy(conn, "SN-A", "2026-07-25", [
        ChannelDayEnergy(1, 57.3, 0.1958, 7975.5, "2026-07-25 12:00:00", 780)],
        now="2026-07-26T00:00:00+00:00")
    store.save_day_energy(conn, "SN-A", "2026-07-25", [
        ChannelDayEnergy(1, 57.9, 0.1961, 8010.0, "2026-07-25 12:05:00", 785)],
        now="2026-07-26T06:00:00+00:00")
    rows = store.load_day_energy(conn, "SN-A", "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["energy_kwh"] == 57.9
    assert rows[0]["peak_w"] == 8010.0
    assert rows[0]["peak_at"] == "2026-07-25 12:05:00"
    assert rows[0]["producing_minutes"] == 785
    assert rows[0]["updated_at_utc"] == "2026-07-26T06:00:00+00:00"


def test_load_energy_window_filters_by_day_and_device():
    conn = _conn()
    for day in ("2026-07-11", "2026-07-18", "2026-07-25"):
        store.save_day_energy(conn, "SN-A", day,
                              [ChannelDayEnergy(1, 50.0, 0.2, 7000.0, None, 700)],
                              now="n")
    store.save_day_energy(conn, "SN-B", "2026-07-25",
                          [ChannelDayEnergy(1, 10.0, 0.5, 100.0, None, 60)],
                          now="n")
    rows = store.load_energy_window(conn, "SN-A", "2026-07-18")
    assert sorted(r["day"] for r in rows) == ["2026-07-18", "2026-07-25"]
    assert all(r["device_sn"] == "SN-A" for r in rows)


def test_save_channel_samples_is_idempotent():
    conn = _conn()
    s = [ChannelSample("2026-07-25 12:00:00", "2026-07-25", "mppt", 1,
                       7975.5, 409.0, 19.5),
         ChannelSample("2026-07-25 12:00:00", "2026-07-25", "string", 1,
                       None, 408.30002, 8.900001)]
    store.save_channel_samples(conn, "SN-A", s)
    store.save_channel_samples(conn, "SN-A", s)      # re-run the same day
    rows = store.load_channel_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 2
    mppt = next(r for r in rows if r["channel_kind"] == "mppt")
    assert mppt["power_w"] == 7975.5 and mppt["current_a"] == 19.5
    string = next(r for r in rows if r["channel_kind"] == "string")
    assert string["power_w"] is None and string["voltage_v"] == 408.30002


def test_save_inverter_samples_is_idempotent_and_keeps_flags():
    conn = _conn()
    s = [InverterSample("2026-07-25 12:00:00", "2026-07-25", pac_w=40460.9,
                        e_ac_today_kwh=135.2, temp_c=43.4, pv_iso_kohm=250.0,
                        gfci_ma=3.0, status="1", str_break="0",
                        str_unbalance="0", str_unmatch="0", warn_code="0")]
    store.save_inverter_samples(conn, "SN-A", s)
    store.save_inverter_samples(conn, "SN-A", s)
    rows = store.load_inverter_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["pac_w"] == 40460.9
    assert rows[0]["pv_iso_kohm"] == 250.0
    assert rows[0]["status"] == "1"
    assert rows[0]["str_unbalance"] == "0"


def test_save_channel_samples_round_trips_descending_input_ascending():
    # The collector concatenates pages newest-first, so a descending list is
    # the real-world input shape; regression risk is in the sort/rebuild, not
    # in the physical page-fill benefit (that's measured, not unit-tested).
    conn = _conn()
    s = [ChannelSample("2026-07-25 12:10:00", "2026-07-25", "mppt", 1,
                       120.0, 400.0, 3.0),
         ChannelSample("2026-07-25 12:10:00", "2026-07-25", "string", 2,
                       None, 401.0, 4.0),
         ChannelSample("2026-07-25 12:05:00", "2026-07-25", "mppt", 1,
                       110.0, 399.0, 2.5),
         ChannelSample("2026-07-25 12:00:00", "2026-07-25", "mppt", 1,
                       100.0, 398.0, 2.0)]
    store.save_channel_samples(conn, "SN-A", s)
    rows = store.load_channel_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 4
    mppt = [r for r in rows if r["channel_kind"] == "mppt"]
    assert [r["sampled_at"] for r in mppt] == [
        "2026-07-25 12:00:00", "2026-07-25 12:05:00", "2026-07-25 12:10:00"]
    assert [r["power_w"] for r in mppt] == [100.0, 110.0, 120.0]
    assert [r["current_a"] for r in mppt] == [2.0, 2.5, 3.0]
    string = next(r for r in rows if r["channel_kind"] == "string")
    assert string["voltage_v"] == 401.0 and string["power_w"] is None


def test_save_inverter_samples_round_trips_descending_input_ascending():
    conn = _conn()
    s = [InverterSample("2026-07-25 12:10:00", "2026-07-25", pac_w=300.0),
         InverterSample("2026-07-25 12:05:00", "2026-07-25", pac_w=200.0),
         InverterSample("2026-07-25 12:00:00", "2026-07-25", pac_w=100.0)]
    store.save_inverter_samples(conn, "SN-A", s)
    rows = store.load_inverter_samples(conn, "SN-A", "2026-07-25")
    assert len(rows) == 3
    assert [r["sampled_at"] for r in rows] == [
        "2026-07-25 12:00:00", "2026-07-25 12:05:00", "2026-07-25 12:10:00"]
    assert [r["pac_w"] for r in rows] == [100.0, 200.0, 300.0]


def test_save_helpers_tolerate_empty_input():
    conn = _conn()
    store.save_channels(conn, "SN-A", "uid", [], now="n")
    store.save_day_energy(conn, "SN-A", "2026-07-25", [], now="n")
    store.save_channel_samples(conn, "SN-A", [])
    store.save_inverter_samples(conn, "SN-A", [])
    assert store.load_channels(conn, "SN-A") == []
    assert store.load_channel_samples(conn, "SN-A", "2026-07-25") == []
