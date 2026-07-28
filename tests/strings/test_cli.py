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
