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


import io
import os
from datetime import datetime, timedelta, timezone

from solaranalysis.web.events import Redactor


def test_tee_writes_to_both_stdout_and_the_file(capsys):
    buf = io.StringIO()
    tee = orch.Tee(buf)
    assert tee("hello\n") == "hello"
    assert buf.getvalue() == "hello\n"
    assert capsys.readouterr().out == "hello\n"


def test_tee_redacts_secrets_before_they_reach_either_sink(capsys):
    buf = io.StringIO()
    tee = orch.Tee(buf, Redactor(["sekret"]))
    line = tee("login failed for pw=sekret\n")
    assert "sekret" not in line and "***" in line
    assert "sekret" not in buf.getvalue()
    assert "sekret" not in capsys.readouterr().out


def test_utc_stamp_format():
    assert orch.utc_stamp(datetime(2026, 8, 5, 6, 0, 30,
                                   tzinfo=timezone.utc)) == "20260805-060030"


def _write_log(logs_dir, name, text="x\n"):
    path = os.path.join(logs_dir, name)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    return path


def test_prune_logs_removes_only_expired_pipeline_logs(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    _write_log(str(logs), "pipeline-20260601-060000.log")   # old
    _write_log(str(logs), "pipeline-20260803-060000.log")   # fresh
    now = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    removed = orch.prune_logs(str(logs), 30, now=now)
    assert removed == ["pipeline-20260601-060000.log"]
    assert os.path.exists(str(logs / "pipeline-20260803-060000.log"))


def test_prune_logs_never_touches_the_web_apps_run_logs(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    _write_log(str(logs), "run-7.log")
    _write_log(str(logs), "pipeline-20260101-060000.log")
    now = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    removed = orch.prune_logs(str(logs), 30, now=now)
    assert removed == ["pipeline-20260101-060000.log"]
    assert os.path.exists(str(logs / "run-7.log"))


def test_prune_logs_ignores_unparseable_names_and_a_missing_dir(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    _write_log(str(logs), "pipeline-not-a-stamp.log")
    now = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    assert orch.prune_logs(str(logs), 30, now=now) == []
    assert os.path.exists(str(logs / "pipeline-not-a-stamp.log"))
    assert orch.prune_logs(str(tmp_path / "nope"), 30, now=now) == []


def test_force_utf8_is_a_noop_on_a_stream_that_cannot_reconfigure():
    # DEPLOYMENT.md §12: without UTF-8 stdout a Hebrew or "·" print dies with a
    # UnicodeEncodeError that reads like a collection failure. A stream that
    # can't be reconfigured must not raise.
    orch.force_utf8(io.StringIO())


import json


def test_acquire_lock_writes_our_pid_when_free(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    acquired, holder = orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False)
    assert acquired is True and holder is None
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["pid"] == 1234 and payload["started_at"]


def test_acquire_lock_refuses_while_the_holder_is_alive(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False)
    acquired, holder = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: True)
    assert acquired is False
    assert holder["pid"] == 1234
    # The live holder's lock must survive our refusal.
    assert json.loads(open(path, encoding="utf-8").read())["pid"] == 1234


def test_acquire_lock_reclaims_a_stale_lock_and_reports_the_dead_holder(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False)
    acquired, holder = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: False)
    assert acquired is True
    assert holder["pid"] == 1234          # so the caller can log who died
    assert json.loads(open(path, encoding="utf-8").read())["pid"] == 5678


def test_acquire_lock_reclaims_a_corrupt_lock_file(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    open(path, "w", encoding="utf-8").write("not json")
    acquired, holder = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: True)
    assert acquired is True and holder == {}


def test_acquire_lock_reclaims_an_expired_lock_even_from_a_live_pid(tmp_path):
    # The 3h ExecutionTimeLimit strands the lock holding our own pid; Windows
    # can then recycle that pid to a live service, which would otherwise block
    # the pipeline forever with no notification.
    path = str(tmp_path / "pipeline.lock")
    t0 = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False, now=t0)
    later = t0 + timedelta(hours=orch.MAX_LOCK_AGE_H, minutes=1)
    acquired, holder = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: True,
                                        now=later)
    assert acquired is True
    assert holder["pid"] == 1234
    assert json.loads(open(path, encoding="utf-8").read())["pid"] == 5678


def test_acquire_lock_still_yields_to_a_live_holder_inside_the_age_window(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    t0 = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False, now=t0)
    acquired, _ = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: True,
                                    now=t0 + timedelta(hours=2))
    assert acquired is False


def test_acquire_lock_treats_a_missing_or_skewed_timestamp_as_stale(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    now = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)
    for payload in ({"pid": 1234},                       # no started_at
                    {"pid": 1234, "started_at": "garbage"},
                    {"pid": 1234, "started_at": "2027-01-01T00:00:00+00:00"}):
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp)
        acquired, _ = orch.acquire_lock(path, pid=5678, pid_alive=lambda p: True,
                                        now=now)
        assert acquired is True, f"unusable timestamp must not block: {payload}"


def test_release_lock_removes_only_our_own(tmp_path):
    path = str(tmp_path / "pipeline.lock")
    orch.acquire_lock(path, pid=1234, pid_alive=lambda p: False)
    assert orch.release_lock(path, pid=5678) is False
    assert os.path.exists(path)
    assert orch.release_lock(path, pid=1234) is True
    assert not os.path.exists(path)


def test_release_lock_is_safe_when_the_file_is_already_gone(tmp_path):
    assert orch.release_lock(str(tmp_path / "pipeline.lock"), pid=1234) is False
