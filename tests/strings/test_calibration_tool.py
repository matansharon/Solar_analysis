"""The calibration tool reaches into `analyze`'s private helpers on purpose --
it must measure exactly what the analyzer measures. These tests exist so that
renaming one of those helpers fails loudly here instead of silently rotting the
tool nobody runs until recalibration day."""
import pytest

from solaranalysis.web import db
from solaranalysis.strings import store
from solaranalysis.strings.mappers import (ChannelInfo, ChannelDayEnergy,
                                           ChannelSample, InverterSample)
from solaranalysis.tools import string_calibration as calib

DAYS = [f"2026-07-{d:02d}" for d in range(11, 28)]


def _db(share2=0.20):
    conn = db.connect(":memory:")
    db.init_db(conn)
    store.save_channels(conn, "SN1", "uid", [
        ChannelInfo(kind="mppt", no=1, lifetime_kwh=1000.0),
        ChannelInfo(kind="mppt", no=2, lifetime_kwh=1000.0),
        ChannelInfo(kind="string", no=1), ChannelInfo(kind="string", no=2)],
        now="n")
    for d in DAYS:
        store.save_day_energy(conn, "SN1", d, [
            ChannelDayEnergy(channel_no=1, energy_kwh=240.0,
                             share_of_total=1 - share2, producing_minutes=800),
            ChannelDayEnergy(channel_no=2, energy_kwh=60.0,
                             share_of_total=share2, producing_minutes=800)],
            now="n")
        store.save_inverter_samples(conn, "SN1", [
            InverterSample(sampled_at=f"{d} 12:00:00", day=d, pac_w=40000.0,
                           status="1", temp3_c=60.0)])
        store.save_channel_samples(conn, "SN1", [
            ChannelSample(sampled_at=f"{d} 12:00:00", day=d, kind="mppt", no=1,
                          power_w=8000.0, voltage_v=400.0, current_a=20.0),
            ChannelSample(sampled_at=f"{d} 12:00:00", day=d, kind="mppt", no=2,
                          power_w=2000.0, voltage_v=400.0, current_a=5.0),
            ChannelSample(sampled_at=f"{d} 12:00:00", day=d, kind="string", no=1,
                          power_w=4000.0, voltage_v=400.0, current_a=11.0),
            ChannelSample(sampled_at=f"{d} 12:00:00", day=d, kind="string", no=2,
                          power_w=4000.0, voltage_v=400.0, current_a=9.0)])
    conn.commit()
    return conn


def test_calibrate_runs_against_a_real_schema_and_reports_every_rule():
    res = calib.calibrate(_db(), "SN1")
    assert len(res["days"]) == len(DAYS)
    for key in ("share_rel", "degrade_rel", "pair_pp", "intraday_rel", "temp_c"):
        assert key in res["measured"] and key in res["shipped"]


def test_a_perfectly_steady_plant_measures_zero_deviation():
    res = calib.calibrate(_db(), "SN1")["measured"]
    assert res["share_rel"] == (0.0, 0.0)
    assert res["pair_pp"] == (0.0, 0.0)
    assert res["temp_c"] == (0.0, 0.0)


def test_the_absolute_pair_curve_is_reported_for_the_rule_6_sanity_check():
    m = calib.calibrate(_db(), "SN1")["measured"]
    # currents 11 vs 9 -> 20% imbalance on every day
    assert m["pair_n"] == len(DAYS)
    assert m["pair_absolute"] == pytest.approx((20.0, 20.0))
    assert m["pair_absolute_curve"][15] == len(DAYS)   # an absolute rule fires
    assert m["pair_absolute_curve"][25] == 0


def test_report_renders_and_names_each_rule():
    text = calib.report(calib.calibrate(_db(), "SN1"))
    for rule in ("4/5 share", "7 degrading", "6 pair imbalance",
                 "8 intraday", "9 temperature"):
        assert rule in text
    assert "healthy pair-days" in text


def test_report_flags_a_threshold_whose_headroom_has_eroded(monkeypatch):
    res = calib.calibrate(_db(), "SN1")
    res["measured"]["share_rel"] = (-0.04, 0.0)   # worst healthy now -4%
    res["shipped"]["share_rel"] = 0.05            # only 1.25x headroom
    assert "HEADROOM ERODED" in calib.report(res)


def test_verify_replays_the_shipped_analyzer_over_every_stored_day():
    text = calib.verify(_db(), "SN1")
    assert "0 finding(s) across" in text
    for d in DAYS:
        assert d in text


def test_verify_surfaces_a_real_finding():
    conn = _db()
    conn.execute("UPDATE channel_day_energy SET share_of_total=0.10, "
                 "energy_kwh=30.0 WHERE channel_no=2 AND day=?", (DAYS[-1],))
    conn.commit()
    assert "underperforming" in calib.verify(conn, "SN1")


def test_device_defaults_to_the_inverter_with_the_most_history():
    assert calib._device(_db()) == "SN1"


def test_calibrate_refuses_rather_than_guessing_on_too_little_history():
    conn = db.connect(":memory:"); db.init_db(conn)
    store.save_day_energy(conn, "SN1", DAYS[0], [
        ChannelDayEnergy(channel_no=1, energy_kwh=1.0, share_of_total=1.0,
                         producing_minutes=800)], now="n")
    conn.commit()
    with pytest.raises(SystemExit):
        calib.calibrate(conn, "SN1")


def test_helpers():
    assert calib._worst([-0.3, 0.1, -0.5]) == (-0.5, 0.1)
    assert calib._worst([]) == (0.0, 0.0)
    assert calib._curve([-0.1, -0.03], (0.02, 0.05)) == {0.02: 2, 0.05: 1}
    assert calib._curve([2.0, -3.0], (1.0, 2.5), mode="abs") == {1.0: 2, 2.5: 1}
    # rule 9 only fires on a rise, so a -3.0 C drop is not one of its breaches
    assert calib._curve([2.0, -3.0], (1.0, 2.5), mode="rise") == {1.0: 1, 2.5: 0}
    assert calib._headroom(0.0, 0.05) == "inf"
    assert calib._headroom(-0.01, 0.05) == "5.0x"
