from datetime import date
from solaranalysis.optimizers import cli


def test_resolve_days_default_is_yesterday():
    assert cli.resolve_days(None, 1, today=date(2026, 7, 24)) == ["2026-07-23"]


def test_resolve_days_explicit_date():
    assert cli.resolve_days("2026-07-10", 1, today=date(2026, 7, 24)) == ["2026-07-10"]


def test_resolve_days_backfill_counts_back_from_target():
    assert cli.resolve_days("2026-07-10", 3, today=date(2026, 7, 24)) == [
        "2026-07-08", "2026-07-09", "2026-07-10"]


def test_resolve_days_backfill_from_yesterday():
    got = cli.resolve_days(None, 90, today=date(2026, 7, 24))
    assert len(got) == 90 and got[-1] == "2026-07-23" and got[0] == "2026-04-25"
