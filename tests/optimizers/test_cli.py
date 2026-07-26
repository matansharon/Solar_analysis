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


def _anom(serial, sev="dead"):
    from solaranalysis.optimizers.analyze import OptimizerAnomaly
    return OptimizerAnomaly(1, serial, "1.4.1", "1.4", sev, "reason",
                            "2026-07-22", 2500.0, 0.55, 5000.0, 0.5)


def test_compose_report_names_sites_in_block_and_markdown():
    seen = {}

    def narrator(block, lang):
        seen["block"] = block
        return "NARRATIVE"

    md, total = cli.compose_report({1: [_anom("A")]}, "2026-07-22", "English",
                                   site_names={1: "Baram"}, narrator=narrator)
    assert total == 1
    assert "Baram" in seen["block"]      # the grounded block Claude reads is named
    assert "Baram" in md and "NARRATIVE" in md


def test_compose_report_skips_narrative_when_all_clear():
    calls = []

    def narrator(block, lang):
        calls.append(block)
        return "SHOULD NOT APPEAR"

    md, total = cli.compose_report({1: []}, "2026-07-22", "English", narrator=narrator)
    assert total == 0
    assert calls == []                   # no model call on an all-clear day
    assert "SHOULD NOT APPEAR" not in md
    assert "all clear" in md.lower()


def test_compose_report_survives_narrator_failure():
    def narrator(block, lang):
        raise RuntimeError("api down")

    md, total = cli.compose_report({1: [_anom("A")]}, "2026-07-22", "English",
                                   narrator=narrator)
    assert total == 1
    assert "| Severity |" in md          # the table still renders without prose


def test_cli_has_no_email_flag():
    import argparse
    from solaranalysis.optimizers import cli
    # The parser must accept --no-email without error.
    parsed = cli._build_parser().parse_args(
        ["--data-dir", "d", "--app-dir", "a", "--no-email"])
    assert parsed.no_email is True
