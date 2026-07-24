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


def test_analysis_window_days_constant():
    from solaranalysis.optimizers import cli
    assert cli.ANALYSIS_WINDOW_DAYS >= 14  # enough for the degradation trend


def test_cli_has_no_email_flag():
    import argparse
    from solaranalysis.optimizers import cli
    # The parser must accept --no-email without error.
    parsed = cli._build_parser().parse_args(
        ["--data-dir", "d", "--app-dir", "a", "--no-email"])
    assert parsed.no_email is True
