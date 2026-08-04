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


import sys
import threading

from solaranalysis.web.paths import Paths


class FakeProc:
    """Same shape as run_manager's Popen: .stdout lines, .wait(), .pid."""

    def __init__(self, lines, code=0, block=None):
        self.stdout = iter(lines)
        self.pid = 4242
        self._code = code
        self._block = block          # threading.Event held open by wait()
        self.killed = False

    def wait(self):
        if self._block is not None:
            self._block.wait(timeout=5)
        return self._code

    def kill(self):
        self.killed = True
        if self._block is not None:
            self._block.set()


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    return Paths.create(str(tmp_path / "data"), str(app))


def _collect(lines=None):
    """A log sink that records what it was handed."""
    seen = lines if lines is not None else []

    def log(line):
        line = line.rstrip("\n")
        seen.append(line)
        return line
    log.seen = seen
    return log


def test_collector_cmd_passes_the_dirs_and_omits_no_email_by_default(tmp_path):
    paths = _paths(tmp_path)
    cmd = orch.build_collector_cmd("strings", paths, no_email=False)
    assert cmd[:3] == [sys.executable, "-m", "solaranalysis.strings"]
    assert "--data-dir" in cmd and paths.data_dir in cmd
    assert "--app-dir" in cmd and paths.app_dir in cmd
    assert "--no-email" not in cmd


def test_collector_cmd_forwards_no_email(tmp_path):
    cmd = orch.build_collector_cmd("optimizers", _paths(tmp_path), no_email=True)
    assert cmd[2] == "solaranalysis.optimizers"
    assert "--no-email" in cmd


def test_collector_stage_exit_zero_is_ok(tmp_path):
    log = _collect()
    proc = FakeProc(["MZHRF6K002 2026-08-03: 18 channels\n"], code=0)
    out = orch.run_collector_stage("strings", _paths(tmp_path), 60, False, log,
                                   spawn=lambda cmd: proc)
    assert out.status == "ok" and out.exit_code == 0 and out.name == "strings"
    assert any("18 channels" in l for l in log.seen)


def test_collector_stage_streams_every_line_through_the_log(tmp_path):
    log = _collect()
    proc = FakeProc(["one\n", "two\n", "three\n"], code=0)
    orch.run_collector_stage("strings", _paths(tmp_path), 60, False, log,
                             spawn=lambda cmd: proc)
    assert ["one", "two", "three"] == [l for l in log.seen
                                       if l in ("one", "two", "three")]


def test_collector_stage_maps_every_nonzero_exit_to_failed(tmp_path):
    # 2 = no enabled plant in app.db, 3 = empty/unauthorized list, 4 = a day
    # failed. On a server past DEPLOYMENT.md §10 all three are regressions.
    # One Paths for the whole loop: _paths() does a bare app.mkdir(), so
    # calling it per iteration raises FileExistsError on the second pass.
    paths = _paths(tmp_path)
    for code in (1, 2, 3, 4):
        out = orch.run_collector_stage(
            "optimizers", paths, 60, False, _collect(),
            spawn=lambda cmd, c=code: FakeProc(["boom\n"], code=c))
        assert out.status == "failed" and out.exit_code == code


def test_collector_stage_keeps_a_bounded_tail_for_the_alert(tmp_path):
    lines = [f"line {i}\n" for i in range(50)]
    out = orch.run_collector_stage(
        "strings", _paths(tmp_path), 60, False, _collect(),
        spawn=lambda cmd: FakeProc(lines, code=4))
    tail = out.detail.splitlines()
    assert len(tail) == orch.TAIL_LINES
    assert tail[-1] == "line 49"


def test_collector_stage_times_out_and_kills_the_process(tmp_path, monkeypatch):
    # kill_tree must be stubbed: FakeProc.pid is invented. Windows pids are
    # multiples of 4, so 4242 cannot exist there — but the suite must never run
    # a real kill against a fabricated pid, and it may run off-Windows, where
    # pids are sequential and 4242 is entirely plausible.
    killed = []
    monkeypatch.setattr(orch, "kill_tree", killed.append)
    gate = threading.Event()
    proc = FakeProc([], code=0, block=gate)
    out = orch.run_collector_stage(
        "strings", _paths(tmp_path), 0.05, False, _collect(),
        spawn=lambda cmd: proc)
    assert out.status == "timeout"
    assert killed == [4242] and proc.killed is True
    gate.set()


def test_collector_stage_records_a_spawn_failure_as_failed(tmp_path):
    def boom(cmd):
        raise OSError("interpreter not found")
    out = orch.run_collector_stage("strings", _paths(tmp_path), 60, False,
                                   _collect(), spawn=boom)
    assert out.status == "failed"
    assert "interpreter not found" in out.detail


from solaranalysis.web import crypto, db, repo, run_manager
from solaranalysis.web.events import EVENT_PREFIX


def _fleet_paths(tmp_path):
    """Paths with an initialized DB and one plant, as the fleet stage needs."""
    app = tmp_path / "app"; app.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(paths.db_path); db.init_db(conn)
    key = crypto.load_or_create_key(paths.key_path)
    repo.create_plant(conn, key, {"name": "Baram", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "sekret"})
    conn.close()
    return paths


def _ev(d):
    return EVENT_PREFIX + json.dumps(d) + "\n"


def _rm(paths, lines, code=0):
    proc = FakeProc(lines, code=code)
    return run_manager.RunManager(paths, spawn=lambda cmd: proc), proc


def test_fleet_stage_success_is_ok(tmp_path):
    paths = _fleet_paths(tmp_path)
    rm, _ = _rm(paths, [_ev({"event": "run_complete", "status": "success",
                             "report_path": "output/x/report.html",
                             "skipped": [], "plants_summary": [], "notes": {}})])
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=rm)
    assert out.status == "ok" and out.name == "fleet"


def test_fleet_stage_partial_is_ok_not_a_failure(tmp_path):
    # A partial run emails a report with its own "Unavailable Plants" section,
    # so the operator already sees the gap. Alerting would be noise.
    paths = _fleet_paths(tmp_path)
    rm, _ = _rm(paths, [_ev({"event": "run_complete", "status": "partial",
                             "report_path": "output/x/report.html",
                             "skipped": [{"name": "SMA", "reason": "login"}],
                             "plants_summary": [], "notes": {}})])
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=rm)
    assert out.status == "ok"
    assert orch.aggregate_exit_code([out]) == 0


def test_fleet_stage_failure_carries_the_runs_row_error_and_log_path(tmp_path):
    paths = _fleet_paths(tmp_path)
    rm, _ = _rm(paths, [_ev({"event": "run_complete", "status": "failed",
                             "error": "AdapterError: portal returned 502"})],
                code=1)
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=rm)
    assert out.status == "failed"
    assert "502" in out.detail
    # The fleet stage's own output lives in the web app's run log, not ours.
    assert out.log_ref and out.log_ref.startswith("logs/run-")


NOW = datetime(2026, 8, 4, 6, 0, 0, tzinfo=timezone.utc)


def _add_run(paths, *, started_at, pid=None):
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="snapshot",
                          log_path="logs/run-x.log", started_at=started_at)
    if pid is not None:
        repo.set_run_pid(conn, rid, pid)
    conn.close()
    return rid


def test_fleet_stage_is_skipped_when_a_live_run_is_already_active(tmp_path):
    paths = _fleet_paths(tmp_path)
    rid = _add_run(paths, started_at=NOW.isoformat(), pid=1234)
    log = _collect()
    out = orch.run_fleet_stage(paths, "snapshot", 5, log, rm=None, now=NOW,
                               pid_alive=lambda p: True)
    assert out.status == "skipped"
    assert orch.aggregate_exit_code([out]) == 0
    assert any("SKIPPED" in l for l in log.seen)
    # A live holder must be left strictly alone.
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "running"
    conn.close()


def test_fleet_stage_reclaims_a_stranded_row_and_runs_anyway(tmp_path):
    # A row left 'running' by a Ctrl-C, a 3h timeout kill, or a reboot must not
    # silence the fleet report forever.
    paths = _fleet_paths(tmp_path)
    rid = _add_run(paths, started_at=(NOW - timedelta(hours=26)).isoformat(),
                   pid=1234)
    rm, _ = _rm(paths, [_ev({"event": "run_complete", "status": "success",
                             "report_path": "output/x/report.html",
                             "skipped": [], "plants_summary": [], "notes": {}})])
    log = _collect()
    out = orch.run_fleet_stage(paths, "snapshot", 5, log, rm=rm, now=NOW,
                               pid_alive=lambda p: False)
    assert out.status == "ok", "a stranded row must not block the stage"
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "interrupted"
    conn.close()
    assert any("stranded" in l for l in log.seen)


def test_fleet_stage_reclaims_a_stranded_row_even_from_a_live_recycled_pid(tmp_path):
    paths = _fleet_paths(tmp_path)
    _add_run(paths, started_at=(NOW - timedelta(hours=26)).isoformat(), pid=1234)
    rm, _ = _rm(paths, [_ev({"event": "run_complete", "status": "success",
                             "report_path": "output/x/report.html",
                             "skipped": [], "plants_summary": [], "notes": {}})])
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=rm, now=NOW,
                               pid_alive=lambda p: True)
    assert out.status == "ok"


def test_fleet_stage_yields_to_a_just_started_run_with_no_pid_yet(tmp_path):
    # create_run commits status='running' before set_run_pid; reclaiming that
    # window would start a second concurrent fleet run.
    paths = _fleet_paths(tmp_path)
    _add_run(paths, started_at=NOW.isoformat(), pid=None)
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=None, now=NOW,
                               pid_alive=lambda p: False)
    assert out.status == "skipped"


def test_live_run_holders_splits_live_from_stranded(tmp_path):
    paths = _fleet_paths(tmp_path)
    _add_run(paths, started_at=NOW.isoformat(), pid=1234)                  # live
    _add_run(paths, started_at=NOW.isoformat(), pid=None)                  # starting
    old = _add_run(paths, started_at=(NOW - timedelta(hours=3)).isoformat(),
                   pid=None)                                              # stranded
    conn = db.connect(paths.db_path)
    live, stranded = orch.live_run_holders(conn, NOW, pid_alive=lambda p: True)
    conn.close()
    assert len(live) == 2
    assert [r["id"] for r in stranded] == [old]


def test_fleet_stage_timeout_cancels_the_run(tmp_path, monkeypatch):
    paths = _fleet_paths(tmp_path)
    gate = threading.Event()
    proc = FakeProc([], code=0, block=gate)
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    # RunManager.cancel calls self._kill_tree(proc.pid) — FakeProc.pid is
    # invented, so never let the real psutil tree-kill run. Same guard as
    # tests/web/test_run_manager_cancel.py; patch the instance, not the class.
    monkeypatch.setattr(rm, "_kill_tree", lambda pid: None)
    cancelled = []
    real_cancel = rm.cancel

    def spy(rid):
        cancelled.append(rid)
        gate.set()                       # let the fake process finish
        return real_cancel(rid)
    rm.cancel = spy
    out = orch.run_fleet_stage(paths, "snapshot", 0.05, _collect(), rm=rm,
                               now=NOW, pid_alive=lambda p: False)
    assert out.status == "timeout"
    assert cancelled, "a run past its timeout must be cancelled, not left running"


def test_fleet_stage_records_a_busy_run_manager_as_failed(tmp_path):
    paths = _fleet_paths(tmp_path)

    class Busy:
        def start_run(self, *a, **k):
            raise run_manager.Busy({"kind": "test", "id": 3})
    out = orch.run_fleet_stage(paths, "snapshot", 5, _collect(), rm=Busy())
    assert out.status == "failed"
    assert "operation active" in out.detail or "Busy" in out.detail


def _failed(name, detail="boom", code=1):
    return orch.StageOutcome(name, "failed", exit_code=code, duration_s=12.0,
                             detail=detail)


def test_should_alert_only_on_a_real_failure():
    ok = [_outcome("fleet", "ok"), _outcome("strings", "ok")]
    assert orch.should_alert(ok, no_email=False) is False
    assert orch.should_alert([_failed("strings")], no_email=False) is True
    assert orch.should_alert([_outcome("optimizers", "timeout")],
                            no_email=False) is True


def test_should_alert_is_quiet_for_skipped_and_under_no_email():
    assert orch.should_alert([_outcome("fleet", "skipped")], no_email=False) is False
    assert orch.should_alert([_failed("strings")], no_email=True) is False


def test_alert_subject_names_the_failing_stages():
    subj = orch.alert_subject([_outcome("fleet", "ok"), _failed("strings"),
                               _failed("optimizers")], "20260805-060000")
    assert "FAILED" in subj
    assert "strings" in subj and "optimizers" in subj
    assert "fleet" not in subj
    assert "20260805-060000" in subj


def test_alert_body_lists_every_stage_and_the_failed_stages_evidence():
    outcomes = [orch.StageOutcome("fleet", "ok", exit_code=0, duration_s=90.0,
                                  detail="run 12 partial",
                                  log_ref="logs/run-12.log"),
                _failed("optimizers", detail="Traceback: 401 unauthorized", code=3)]
    html = orch.compose_alert_html(outcomes, "data/logs/pipeline-x.log",
                                   "20260805-060000")
    assert "fleet" in html and "optimizers" in html      # every stage listed
    assert "401 unauthorized" in html                    # the actionable part
    assert "pipeline-x.log" in html                      # where to look next
    assert "style=" in html                              # Outlook/Gmail safe


def test_alert_body_escapes_html_in_a_stage_detail():
    html = orch.compose_alert_html([_failed("strings", detail="<script>x</script>")],
                                   "p.log", "20260805-060000")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_recipients_prefer_pipeline_over_report(monkeypatch):
    monkeypatch.setenv("REPORT_RECIPIENTS", "fleet@example.com")
    monkeypatch.setenv("PIPELINE_RECIPIENTS", "ops@example.com, ops@example.com ,"
                                              "oncall@example.com")
    assert orch.resolve_recipients() == ["ops@example.com", "oncall@example.com"]


def test_recipients_fall_back_to_the_apps_configured_list(monkeypatch):
    monkeypatch.setenv("REPORT_RECIPIENTS", "fleet@example.com")
    assert orch.resolve_recipients() == ["fleet@example.com"]


def test_send_alert_skips_cleanly_when_email_is_not_configured():
    sent, msg = orch.send_alert([_failed("strings")], "p.log", "20260805-060000",
                                send=lambda *a, **k: None)
    assert sent is False and "not configured" in msg


def test_send_alert_sends_to_the_resolved_recipients(monkeypatch):
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "GRAPH_SENDER"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("PIPELINE_RECIPIENTS", "ops@example.com")
    calls = []
    sent, msg = orch.send_alert([_failed("strings")], "p.log", "20260805-060000",
                                send=lambda s, b, to=None: calls.append((s, b, to)))
    assert sent is True and "ops@example.com" in msg
    assert calls and calls[0][2] == ["ops@example.com"]


def test_send_alert_swallows_a_send_failure(monkeypatch):
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "GRAPH_SENDER"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("PIPELINE_RECIPIENTS", "ops@example.com")

    def boom(*a, **k):
        raise RuntimeError("Graph sendMail failed 403")
    sent, msg = orch.send_alert([_failed("strings")], "p.log", "20260805-060000",
                                send=boom)
    assert sent is False and "403" in msg


def test_send_alert_never_raises_even_when_resolve_recipients_fails(monkeypatch):
    def boom():
        raise RuntimeError("recipient resolver failed")
    monkeypatch.setattr(orch, "resolve_recipients", boom)
    sent, msg = orch.send_alert([_failed("strings")], "p.log", "20260805-060000")
    assert sent is False and "recipient resolver failed" in msg


def test_send_alert_distinguishes_no_recipients_from_unconfigured(monkeypatch):
    monkeypatch.setenv("GRAPH_TENANT_ID", "x")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "x")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "x")
    monkeypatch.setenv("GRAPH_SENDER", "x")
    # PIPELINE_RECIPIENTS and REPORT_RECIPIENTS both unset, so no recipients
    sent, msg = orch.send_alert([_failed("strings")], "p.log", "20260805-060000")
    assert sent is False and "no recipients" in msg


def _fake_stage(status, code=0):
    def run(name_or_paths, *a, **k):
        # run_collector_stage(name, ...) and run_fleet_stage(paths, ...) differ
        # in their first argument; normalize to whichever we were given.
        name = name_or_paths if isinstance(name_or_paths, str) else "fleet"
        return orch.StageOutcome(name, status, exit_code=code)
    return run


def _main_args(tmp_path, *extra):
    return ["--data-dir", str(tmp_path / "data"),
            "--app-dir", str(tmp_path / "app"), *extra]


def test_main_returns_zero_when_every_stage_is_ok(tmp_path):
    (tmp_path / "app").mkdir()
    rc = orch.main(_main_args(tmp_path), fleet=_fake_stage("ok"),
                   collector=_fake_stage("ok"), alert=lambda *a, **k: (False, "x"))
    assert rc == 0


def test_main_exit_code_names_the_failing_stages(tmp_path):
    (tmp_path / "app").mkdir()

    def collector(name, *a, **k):
        return orch.StageOutcome(name, "failed" if name == "strings" else "ok",
                                 exit_code=4 if name == "strings" else 0)
    rc = orch.main(_main_args(tmp_path), fleet=_fake_stage("ok"),
                   collector=collector, alert=lambda *a, **k: (False, "x"))
    assert rc == 4


def test_main_runs_only_the_selected_stages(tmp_path):
    (tmp_path / "app").mkdir()
    ran = []

    def collector(name, *a, **k):
        ran.append(name)
        return orch.StageOutcome(name, "ok", exit_code=0)

    def fleet(*a, **k):
        ran.append("fleet")
        return orch.StageOutcome("fleet", "ok", exit_code=0)
    orch.main(_main_args(tmp_path, "--only", "strings"), fleet=fleet,
              collector=collector, alert=lambda *a, **k: (False, "x"))
    assert ran == ["strings"]


def test_main_rejects_an_unknown_stage_with_8(tmp_path):
    (tmp_path / "app").mkdir()
    assert orch.main(_main_args(tmp_path, "--only", "inverters")) == \
        orch.ORCHESTRATOR_FAILED


def test_main_exits_8_while_another_run_holds_the_lock(tmp_path, capsys,
                                                      monkeypatch):
    # The holder pid must NOT be our own: acquire_lock skips the liveness check
    # when hpid == pid (deliberate re-entrancy tolerance), so seeding
    # os.getpid() would reclaim the lock and run every stage.
    (tmp_path / "app").mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(tmp_path / "app"))
    lock = os.path.join(paths.data_dir, orch.LOCK_NAME)
    other = os.getpid() + 1
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with open(lock, "w", encoding="utf-8") as fp:
        json.dump({"pid": other, "started_at": started}, fp)
    monkeypatch.setattr(orch, "pid_alive", lambda p: True)
    ran, alerts = [], []

    def fleet(*a, **k):
        # Must return a real StageOutcome. A None here would crash
        # aggregate_exit_code into main's catch-all, which returns 8 too — so a
        # regression in the lock guard would still show rc == 8 and the test
        # would pass for the wrong reason.
        ran.append("fleet")
        return orch.StageOutcome("fleet", "ok", exit_code=0)

    def collector(name, *a, **k):
        ran.append(name)
        return orch.StageOutcome(name, "ok", exit_code=0)

    rc = orch.main(_main_args(tmp_path), fleet=fleet, collector=collector,
                   alert=lambda *a, **k: alerts.append(1) or (True, "x"))
    assert rc == orch.ORCHESTRATOR_FAILED
    assert ran == [], "no stage may run while another pipeline holds the lock"
    assert alerts == [], "a lock-held exit must stay quiet (no alert email)"
    # Pins that acquire_lock refused rather than reclaiming, and that main's
    # early return never released a lock it does not own.
    assert json.loads(open(lock, encoding="utf-8").read())["pid"] == other
    assert "still active" in capsys.readouterr().out


def test_main_releases_the_lock_even_when_a_stage_fails(tmp_path):
    (tmp_path / "app").mkdir()
    orch.main(_main_args(tmp_path), fleet=_fake_stage("failed", code=1),
              collector=_fake_stage("ok"), alert=lambda *a, **k: (False, "x"))
    paths = Paths.create(str(tmp_path / "data"), str(tmp_path / "app"))
    assert not os.path.exists(os.path.join(paths.data_dir, orch.LOCK_NAME))


def test_main_writes_one_pipeline_log_per_run(tmp_path):
    (tmp_path / "app").mkdir()
    orch.main(_main_args(tmp_path), now=lambda: datetime(2026, 8, 5, 6, 0, 0,
                                                          tzinfo=timezone.utc),
              fleet=_fake_stage("ok"), collector=_fake_stage("ok"),
              alert=lambda *a, **k: (False, "x"))
    paths = Paths.create(str(tmp_path / "data"), str(tmp_path / "app"))
    log = os.path.join(paths.logs_dir, "pipeline-20260805-060000.log")
    assert os.path.exists(log)
    assert "exit 0" in open(log, encoding="utf-8").read()


def test_main_alerts_once_on_failure_and_stays_quiet_otherwise(tmp_path):
    (tmp_path / "app").mkdir()
    calls = []

    def alert(outcomes, log_path, stamp, **k):
        calls.append([o.name for o in outcomes if o.failed])
        return True, "alert emailed"
    orch.main(_main_args(tmp_path), fleet=_fake_stage("ok"),
              collector=_fake_stage("ok"), alert=alert)
    assert calls == []
    orch.main(_main_args(tmp_path), fleet=_fake_stage("failed", code=1),
              collector=_fake_stage("ok"), alert=alert)
    assert calls == [["fleet"]]


def test_main_no_email_suppresses_the_alert_and_sets_the_env(tmp_path,
                                                             monkeypatch):
    (tmp_path / "app").mkdir()
    # setenv, not delenv: monkeypatch records an undo entry only for a key it
    # actually touches, and delenv on an absent key records nothing. main()
    # writes os.environ directly, so without a recorded baseline the "1" leaks
    # into every later test in the session — including all of
    # tests/web/test_runner.py, which would then run with email suppressed.
    monkeypatch.setenv("SOLAR_NO_EMAIL", "0")
    seen = {}

    def fleet(*a, **k):
        seen["suppressed"] = os.getenv("SOLAR_NO_EMAIL")
        return orch.StageOutcome("fleet", "failed", exit_code=1)
    calls = []
    orch.main(_main_args(tmp_path, "--no-email"), fleet=fleet,
              collector=_fake_stage("ok"),
              alert=lambda *a, **k: calls.append(1) or (True, "x"))
    assert calls == [], "--no-email must silence the alert too"
    assert seen["suppressed"] == "1", "the fleet stage must inherit --no-email"


def test_main_exports_utf8_for_every_child_including_the_fleet_runner(tmp_path,
                                                                    monkeypatch):
    # DEPLOYMENT.md §13 tells the operator PYTHONIOENCODING is not needed.
    # That is only true if the variable reaches the fleet stage's runner
    # subprocess, which RunManager._default_spawn launches with no env= and so
    # inherits ours. Without this test a refactor could silently re-break the
    # doc's promise, and the 06:00 run would die on a Hebrew print.
    (tmp_path / "app").mkdir()
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    seen = {}

    def fleet(*a, **k):
        seen["enc"] = os.getenv("PYTHONIOENCODING")
        return orch.StageOutcome("fleet", "ok", exit_code=0)
    orch.main(_main_args(tmp_path, "--only", "fleet"), fleet=fleet,
              collector=_fake_stage("ok"), alert=lambda *a, **k: (False, "x"))
    assert seen["enc"] == "utf-8:replace"


def test_main_isolates_a_stage_that_raises_unexpectedly(tmp_path):
    # A stage raising is that STAGE's failure, not the orchestrator's: the
    # exit code must name it (fleet = 1), and the later stages must still run.
    # Letting it reach the outer handler would report 8 and skip both
    # collectors, which defeats continue-on-failure.
    (tmp_path / "app").mkdir()
    ran = []

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    def collector(name, *a, **k):
        ran.append(name)
        return orch.StageOutcome(name, "ok", exit_code=0)
    rc = orch.main(_main_args(tmp_path), fleet=boom, collector=collector,
                   alert=lambda *a, **k: (False, "x"))
    assert rc == orch.STAGE_BITS["fleet"]
    assert ran == ["optimizers", "strings"], "later stages must still run"


def test_main_returns_8_when_something_outside_the_stage_loop_fails(tmp_path,
                                                                   monkeypatch):
    # Exit 8 is reserved for the orchestrator itself failing. Here the summary
    # path breaks, which is outside any stage.
    (tmp_path / "app").mkdir()
    monkeypatch.setattr(orch, "aggregate_exit_code",
                        lambda outcomes: 1 / 0)
    rc = orch.main(_main_args(tmp_path), fleet=_fake_stage("ok"),
                   collector=_fake_stage("ok"),
                   alert=lambda *a, **k: (False, "x"))
    assert rc == orch.ORCHESTRATOR_FAILED


def test_collect_secrets_returns_stored_credentials(tmp_path):
    paths = _fleet_paths(tmp_path)
    secrets, warning = orch.collect_secrets(paths)
    assert "sekret" in secrets and warning is None


def test_collect_secrets_warns_instead_of_raising_on_a_bad_data_dir(tmp_path):
    paths = Paths.create(str(tmp_path / "data"), str(tmp_path / "app"))
    os.makedirs(paths.db_path, exist_ok=True)      # a directory where the DB goes
    secrets, warning = orch.collect_secrets(paths)
    assert secrets == [] and warning is not None
