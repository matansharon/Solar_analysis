import pytest

from solaranalysis import orchestrator as orch


def _outcome(name, status):
    return orch.StageOutcome(name=name, status=status)


def test_stage_bits_are_distinct_powers_of_two():
    assert orch.STAGE_BITS == {"fleet": 1, "optimizers": 2, "strings": 4}
    # The orchestrator's own code must not collide with any stage combination.
    assert orch.ORCHESTRATOR_FAILED == 8
    assert orch.ORCHESTRATOR_FAILED > sum(orch.STAGE_BITS.values())


def test_only_defaults_to_all_stages_in_run_order():
    assert orch.parse_only(None) == ["fleet", "optimizers", "strings"]
    assert orch.parse_only("") == ["fleet", "optimizers", "strings"]


def test_only_selects_a_subset():
    assert orch.parse_only("optimizers,strings") == ["optimizers", "strings"]
    assert orch.parse_only("strings") == ["strings"]


def test_only_normalizes_order_whitespace_and_duplicates():
    # Run order is fixed regardless of how it was typed: the collectors must
    # follow the fleet snapshot so all three describe the same day's data.
    assert orch.parse_only("strings, fleet") == ["fleet", "strings"]
    assert orch.parse_only("strings,strings") == ["strings"]


def test_only_rejects_an_unknown_stage():
    with pytest.raises(ValueError) as ei:
        orch.parse_only("fleet,inverters")
    assert "inverters" in str(ei.value)


def test_only_rejects_a_selection_that_is_all_separators():
    with pytest.raises(ValueError):
        orch.parse_only(",,")


def test_exit_code_is_zero_when_every_stage_is_ok():
    assert orch.aggregate_exit_code(
        [_outcome("fleet", "ok"), _outcome("optimizers", "ok"),
         _outcome("strings", "ok")]) == 0


def test_exit_code_names_the_single_failing_stage():
    assert orch.aggregate_exit_code([_outcome("fleet", "failed")]) == 1
    assert orch.aggregate_exit_code([_outcome("optimizers", "failed")]) == 2
    assert orch.aggregate_exit_code([_outcome("strings", "failed")]) == 4


def test_exit_code_ors_multiple_failures():
    # All four combinations §13's decode table promises.
    assert orch.aggregate_exit_code(
        [_outcome("fleet", "failed"), _outcome("optimizers", "failed")]) == 3
    assert orch.aggregate_exit_code(
        [_outcome("fleet", "failed"), _outcome("strings", "failed")]) == 5
    assert orch.aggregate_exit_code(
        [_outcome("optimizers", "failed"), _outcome("strings", "failed")]) == 6
    assert orch.aggregate_exit_code(
        [_outcome("fleet", "failed"), _outcome("optimizers", "failed"),
         _outcome("strings", "failed")]) == 7


def test_exit_code_of_a_synthetic_orchestrator_outcome_is_8():
    # The last-resort error path builds one of these; a strict STAGE_BITS
    # lookup would raise KeyError inside the handler.
    assert orch.aggregate_exit_code(
        [_outcome("orchestrator", "failed")]) == orch.ORCHESTRATOR_FAILED


def test_timeout_counts_as_a_failure_but_skipped_does_not():
    assert orch.aggregate_exit_code([_outcome("optimizers", "timeout")]) == 2
    # A skipped fleet stage means a run is happening, just not ours.
    assert orch.aggregate_exit_code([_outcome("fleet", "skipped")]) == 0


def test_parser_reads_the_documented_defaults():
    args = orch._build_parser().parse_args(["--data-dir", "d", "--app-dir", "a"])
    assert args.data_dir == "d" and args.app_dir == "a"
    assert args.range == "snapshot"
    assert args.only is None and args.no_email is False
    assert args.timeout_fleet == 45
    assert args.timeout_optimizers == 60
    assert args.timeout_strings == 30
    assert args.log_retention_days == 30


def test_parser_usage_error_exits_8_not_argparse_2(capsys):
    # argparse's own usage-error code is 2, which would decode as
    # "optimizers failed" under the bitmask.
    with pytest.raises(SystemExit) as ei:
        orch._build_parser().parse_args(["--nonsense"])
    assert ei.value.code == orch.ORCHESTRATOR_FAILED
    with pytest.raises(SystemExit) as ei:
        orch._build_parser().parse_args([])          # missing required dirs
    assert ei.value.code == orch.ORCHESTRATOR_FAILED
