from datetime import date

from solaranalysis.web import db
from solaranalysis.strings import cli


def test_resolve_days_default_is_yesterday():
    assert cli.resolve_days(None, 1, today=date(2026, 7, 26)) == ["2026-07-25"]


def test_resolve_days_explicit_date():
    assert cli.resolve_days("2026-07-11", 1, today=date(2026, 7, 26)) == ["2026-07-11"]


def test_resolve_days_backfill_counts_back_from_target():
    assert cli.resolve_days("2026-07-12", 3, today=date(2026, 7, 26)) == [
        "2026-07-10", "2026-07-11", "2026-07-12"]


def test_resolve_days_backfill_from_yesterday():
    got = cli.resolve_days(None, 90, today=date(2026, 7, 26))
    assert len(got) == 90 and got[-1] == "2026-07-25" and got[0] == "2026-04-27"


def test_growatt_plant_id_picks_the_enabled_growatt_plant():
    conn = db.connect(":memory:"); db.init_db(conn)
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('SE', 'solaredge', 1)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-off', 'growatt', 0)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-on', 'growatt', 1)")
    conn.commit()
    pid = cli.growatt_plant_id(conn)
    assert conn.execute("SELECT name FROM plants WHERE id=?",
                        (pid,)).fetchone()[0] == "G-on"


def test_growatt_plant_id_none_when_only_disabled_or_other_platforms():
    conn = db.connect(":memory:"); db.init_db(conn)
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('G-off', 'growatt', 0)")
    conn.execute("INSERT INTO plants(name, platform, enabled) "
                 "VALUES ('SE', 'solaredge', 1)")
    conn.commit()
    assert cli.growatt_plant_id(conn) is None


def test_first_plant_id_reads_the_portal_side_id():
    assert cli.first_plant_id(
        [{"id": "10950561", "plantName": "Elcam Baram"}]) == "10950561"
    # coerced to str, since the portal is inconsistent about quoting it
    assert cli.first_plant_id([{"id": 10950561}]) == "10950561"


def test_first_plant_id_skips_junk_and_returns_none_when_empty():
    assert cli.first_plant_id([]) is None
    assert cli.first_plant_id(["junk", {}, {"id": None}, {"id": "7"}]) == "7"
    assert cli.first_plant_id(None) is None


def test_parser_requires_data_and_app_dirs():
    parsed = cli._build_parser().parse_args(
        ["--data-dir", "d", "--app-dir", "a", "--backfill", "17"])
    assert parsed.data_dir == "d" and parsed.app_dir == "a"
    assert parsed.backfill == 17 and parsed.date is None


def test_main_exits_2_without_an_enabled_growatt_plant(tmp_path, capsys):
    rc = cli.main(["--data-dir", str(tmp_path / "data"),
                   "--app-dir", str(tmp_path / "app")])
    assert rc == 2
    assert "growatt" in capsys.readouterr().out.lower()


def test_exit_code_all_success_is_zero():
    assert cli.exit_code([{"day": "d1"}, {"day": "d2"}]) == 0


def test_exit_code_one_error_among_successes_is_four():
    assert cli.exit_code([{"day": "d1"}, {"day": "d2", "error": "boom"}]) == 4


def test_exit_code_all_errors_is_four():
    assert cli.exit_code(
        [{"day": "d1", "error": "x"}, {"day": "d2", "error": "y"}]) == 4


def test_exit_code_all_empty_is_zero():
    assert cli.exit_code(
        [{"day": "d1", "empty": True}, {"day": "d2", "empty": True}]) == 0


def test_exit_code_empty_mix_with_one_error_is_four():
    assert cli.exit_code(
        [{"day": "d1", "empty": True}, {"day": "d2", "error": "boom"}]) == 4


def test_exit_code_empty_list_is_zero():
    assert cli.exit_code([]) == 0


# --- analyze + report wiring ---------------------------------------------

def _anom(label="PV3", sev="underperforming"):
    from solaranalysis.strings.analyze import StringAnomaly
    return StringAnomaly("SN1", "mppt", label, sev, "reason", "2026-07-27",
                         0.176, 0.2, 52.8, 0.176)


def test_parser_no_email_flag_defaults_off():
    base = ["--data-dir", "d", "--app-dir", "a"]
    assert cli._build_parser().parse_args(base).no_email is False
    assert cli._build_parser().parse_args(base + ["--no-email"]).no_email is True


def test_compose_report_calls_the_narrator_when_something_is_flagged():
    calls = []

    def narrator(block, lang):
        calls.append((block, lang))
        return "NARRATIVE"

    md, total = cli.compose_report({"SN1": [_anom()]}, "2026-07-27", "English",
                                   narrator=narrator)
    assert total == 1 and "NARRATIVE" in md
    assert len(calls) == 1 and calls[0][1] == "English"
    assert "PV3" in calls[0][0]


def test_compose_report_skips_the_narrator_entirely_on_an_all_clear_day():
    """The Opus call is the expensive part of the run; an all-clear day must
    not pay for it."""
    def narrator(block, lang):
        raise AssertionError("narrator must not be called")

    md, total = cli.compose_report({"SN1": []}, "2026-07-27", "English",
                                   narrator=narrator)
    assert total == 0 and "all clear" in md.lower()


def test_compose_report_degrades_to_tables_when_the_narrator_fails(capsys):
    def narrator(block, lang):
        raise RuntimeError("overloaded")

    md, total = cli.compose_report({"SN1": [_anom()]}, "2026-07-27", "English",
                                   narrator=narrator)
    assert total == 1
    assert "PV3" in md and "| Severity |" in md      # the table survived
    assert "narrative skipped" in capsys.readouterr().out


def test_compose_report_counts_findings_across_inverters():
    analyses = {"SN1": [_anom(), _anom("PV4")], "SN2": [_anom("PV1")]}
    _, total = cli.compose_report(analyses, "2026-07-27", "English",
                                  narrator=lambda b, l: "N")
    assert total == 3


def test_compose_report_passes_device_names_through():
    seen = {}

    def narrator(block, lang):
        seen["block"] = block
        return "N"

    md, _ = cli.compose_report({"SN1": [_anom()]}, "2026-07-27", "English",
                               device_names={"SN1": "MAX 70KTL3 LV"},
                               narrator=narrator)
    assert "MAX 70KTL3 LV" in md and "MAX 70KTL3 LV" in seen["block"]


def test_analyze_device_reads_the_stored_window_and_returns_findings():
    """End-to-end over a real (in-memory) v7 database: the CLI helper must
    wire every loader into the analyzer correctly."""
    from solaranalysis.strings import store
    from solaranalysis.strings.mappers import (ChannelInfo, ChannelDayEnergy,
                                               ChannelSample, InverterSample)
    conn = db.connect(":memory:"); db.init_db(conn)
    store.save_channels(conn, "SN1", "uid",
                        [ChannelInfo(kind="mppt", no=1, lifetime_kwh=1000.0),
                         ChannelInfo(kind="mppt", no=2, lifetime_kwh=1000.0)],
                        now="n")
    days = [f"2026-07-{d:02d}" for d in range(11, 28)]
    for d in days:
        # channel 2 collapses on the final day only
        share2 = 0.20 if d != days[-1] else 0.10
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
                          power_w=2000.0, voltage_v=400.0, current_a=5.0)])
    conn.commit()

    found = cli.analyze_device(conn, "SN1", days[-1], days[0])
    assert [(a.label, a.severity) for a in found] == [("PV2", "underperforming")]


def test_analyze_device_is_all_clear_on_a_healthy_stored_window():
    from solaranalysis.strings import store
    from solaranalysis.strings.mappers import (ChannelInfo, ChannelDayEnergy,
                                               InverterSample)
    conn = db.connect(":memory:"); db.init_db(conn)
    store.save_channels(conn, "SN1", "uid",
                        [ChannelInfo(kind="mppt", no=1, lifetime_kwh=1000.0)],
                        now="n")
    days = [f"2026-07-{d:02d}" for d in range(11, 28)]
    for d in days:
        store.save_day_energy(conn, "SN1", d, [
            ChannelDayEnergy(channel_no=1, energy_kwh=300.0, share_of_total=1.0,
                             producing_minutes=800)], now="n")
        store.save_inverter_samples(conn, "SN1", [
            InverterSample(sampled_at=f"{d} 12:00:00", day=d, pac_w=40000.0,
                           status="1", temp3_c=60.0)])
    conn.commit()
    assert cli.analyze_device(conn, "SN1", days[-1], days[0]) == []
