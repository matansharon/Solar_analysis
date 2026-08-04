# Scheduled Pipeline Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Python entry point that Windows Task Scheduler invokes daily and that runs all three subsystems — fleet comparison, SolarEdge optimizer collector, Growatt string collector — isolating their failures, logging diagnosably, naming the failing stage in its exit code, and emailing an alert when something breaks.

**Architecture:** A new `solaranalysis/orchestrator.py` runs three stages sequentially, each in its own process. The fleet stage is driven through the existing `web/run_manager.RunManager`, so a scheduled run creates a normal `runs` row and still appears in the web UI's history. The two collectors are plain subprocesses whose output is tee'd, redacted, into one pipeline log. A root-level `daily_pipeline.py` wraps it with the venv-relaunch extracted from `app.py` into a shared `_venv.py`, so a bare `python daily_pipeline.py` cannot run under the wrong interpreter.

**Tech Stack:** Python 3.10, stdlib `argparse`/`subprocess`/`threading`/`json`, `psutil` (already a dependency, used for process-tree kills and liveness), `python-dotenv`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-scheduled-pipeline-design.md`

## Global Constraints

- Python is `python` on this machine, never `python3` (a broken Windows Store alias). Interpreter: `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`.
- Run tests with the project venv: `.venv\Scripts\python.exe -m pytest`. A bare `python` has an older `anthropic` SDK.
- Exit codes are a bitmask: `fleet=1`, `optimizers=2`, `strings=4`, OR'd. `8` means the orchestrator itself could not run. `0` means every attempted stage was ok.
- Failure statuses that set a bit: `failed`, `timeout`. Statuses that do not: `ok`, `skipped`.
- A `partial` fleet run is **ok**, not a failure — that report emails with its own "Unavailable Plants" section.
- Collector exit codes other than `0` — including `2` ("no enabled plant configured in app.db") — are failures.
- Email or redaction problems must never change an exit code, and must never fail a stage. Existing house rule throughout this codebase.
- Default stage timeouts in minutes: `fleet=45`, `optimizers=60`, `strings=30`.
- Only files matching `pipeline-*.log` may be pruned. `run-<id>.log` belongs to the web app.
- Never add AI attribution to commit messages.

---

### Task 1: Pure core — stage model, `--only` parsing, exit-code aggregation, parser

**Files:**
- Create: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ALL_STAGES: tuple[str, ...]` = `("fleet", "optimizers", "strings")`
  - `STAGE_BITS: dict[str, int]`, `ORCHESTRATOR_FAILED: int` = 8
  - `DEFAULT_TIMEOUTS: dict[str, int]` (minutes), `STAGE_MODULES: dict[str, str]`
  - `StageOutcome` dataclass with fields `name, status, exit_code, duration_s, detail, log_ref` and a `.failed` property
  - `parse_only(raw: str | None) -> list[str]` — raises `ValueError` on an unknown or empty selection
  - `aggregate_exit_code(outcomes: list[StageOutcome]) -> int`
  - `_build_parser() -> argparse.ArgumentParser` whose `.error()` exits `8`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.orchestrator'`

- [ ] **Step 3: Write the minimal implementation**

Create `solaranalysis/orchestrator.py`:

```python
"""One scheduled entry point for the whole pipeline.

    python -m solaranalysis.orchestrator --data-dir DIR --app-dir DIR

Runs three stages in order, each in its own process so one stage cannot take
the others down: the fleet comparison run (through the web app's RunManager, so
it still shows up in run history), then the SolarEdge optimizer collector, then
the Growatt string collector. Continue-on-failure; the exit code is a bitmask
naming which stage failed. See
docs/superpowers/specs/2026-08-04-scheduled-pipeline-design.md
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

ALL_STAGES = ("fleet", "optimizers", "strings")
STAGE_BITS = {"fleet": 1, "optimizers": 2, "strings": 4}
ORCHESTRATOR_FAILED = 8          # lock held, bad arguments, or a pre-stage error

DEFAULT_TIMEOUTS = {"fleet": 45, "optimizers": 60, "strings": 30}   # minutes
STAGE_MODULES = {"optimizers": "solaranalysis.optimizers",
                 "strings": "solaranalysis.strings"}

# Statuses that set the stage's failure bit. "skipped" deliberately does not.
FAILED_STATUSES = ("failed", "timeout")


@dataclass
class StageOutcome:
    name: str
    status: str                      # ok | failed | timeout | skipped
    exit_code: int | None = None
    duration_s: float = 0.0
    detail: str = ""                 # log tail, or the runs row's error
    log_ref: str | None = None       # a second log worth reading, if any

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


def parse_only(raw: str | None) -> list[str]:
    """Stage selection, normalized to canonical run order and deduped. The
    collectors must follow the fleet snapshot so all three cover the same day,
    so the order the operator types is not the order we run."""
    if not raw:
        return list(ALL_STAGES)
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in STAGE_BITS]
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(unknown)} "
                         f"(choose from {', '.join(ALL_STAGES)})")
    if not names:
        raise ValueError("--only was given but selected no stage")
    return [s for s in ALL_STAGES if s in names]


def aggregate_exit_code(outcomes) -> int:
    """OR the failing stages' bits. An outcome that is not one of the three
    stages — the synthetic "orchestrator" one built on the last-resort error
    path — contributes ORCHESTRATOR_FAILED rather than raising KeyError."""
    code = 0
    for o in outcomes:
        if o.failed:
            code |= STAGE_BITS.get(o.name, ORCHESTRATOR_FAILED)
    return code


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which the bitmask would read as
    'optimizers failed'. Usage errors are orchestrator failures."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(ORCHESTRATOR_FAILED)


def _build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="solaranalysis.orchestrator")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--range", default="snapshot",
                    choices=["snapshot", "30d", "12mo", "all"])
    ap.add_argument("--only", help="comma list: " + ",".join(ALL_STAGES))
    ap.add_argument("--no-email", action="store_true",
                    help="suppress every report and the failure alert")
    for stage, minutes in DEFAULT_TIMEOUTS.items():
        ap.add_argument(f"--timeout-{stage}", type=int, default=minutes,
                        metavar="MINUTES")
    ap.add_argument("--log-retention-days", type=int, default=30)
    return ap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: stage model and per-stage bitmask exit codes for the pipeline orchestrator

The exit code has to name the failing stage, because Task Scheduler shows
LastTaskResult and nothing else. argparse's own usage-error code is 2, which
would have decoded as "optimizers failed", so usage errors exit 8 instead.
EOF
)"
```

---

### Task 2: Log tee, UTF-8 stdout, retention pruning

**Files:**
- Modify: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 1's module.
- Produces:
  - `LOG_PREFIX: str` = `"pipeline-"`, `TAIL_LINES: int` = 20
  - `Tee(fp, redactor=None)` — callable; `tee(line)` writes the redacted line to stdout and `fp`, returns it
  - `prune_logs(logs_dir, retention_days, now=None) -> list[str]` — names removed
  - `force_utf8(stream=None) -> None`
  - `utc_stamp(now=None) -> str` — `"YYYYMMDD-HHMMSS"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k "tee or prune or utf8 or stamp"`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'Tee'`

- [ ] **Step 3: Write the minimal implementation**

Add to the imports in `solaranalysis/orchestrator.py`:

```python
import os
from datetime import datetime, timedelta, timezone
```

Add after `_build_parser`:

```python
LOG_PREFIX = "pipeline-"
TAIL_LINES = 20                  # lines of a failed stage carried into the alert


def utc_stamp(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")


def force_utf8(stream=None) -> None:
    """DEPLOYMENT.md §12: without UTF-8 stdout, printing a Hebrew narrative or
    the "·" in a subject line dies with a UnicodeEncodeError that reads like a
    collection failure. Doing it here removes PYTHONIOENCODING from the task
    registration's prerequisites."""
    for s in ([stream] if stream is not None else [sys.stdout, sys.stderr]):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


class Tee:
    """Line sink: redact once, then write to both stdout and the log file.
    Returns the redacted line so callers can keep a tail for the alert."""

    def __init__(self, fp, redactor=None):
        self._fp = fp
        self._redact = redactor.redact if redactor is not None else (lambda s: s)

    def __call__(self, line: str) -> str:
        line = self._redact(line.rstrip("\n"))
        print(line)
        self._fp.write(line + "\n")
        self._fp.flush()          # a hung stage must still leave a usable log
        return line


def prune_logs(logs_dir: str, retention_days: int, now=None) -> list[str]:
    """Delete expired `pipeline-*.log` files; return the names removed. Only
    our own prefix is eligible — `run-<id>.log` belongs to the web app."""
    if not os.path.isdir(logs_dir):
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    removed = []
    for name in sorted(os.listdir(logs_dir)):
        if not (name.startswith(LOG_PREFIX) and name.endswith(".log")):
            continue
        try:
            when = datetime.strptime(name[len(LOG_PREFIX):-len(".log")],
                                     "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue                      # not one of ours; leave it alone
        if when < cutoff:
            try:
                os.remove(os.path.join(logs_dir, name))
                removed.append(name)
            except OSError:
                continue                  # locked or already gone; not fatal
    return removed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: redacted log tee, UTF-8 stdout, and pipeline-log retention

Task Scheduler discards stdout, so the log file is the only durable record;
lines are flushed per line so a hung stage still leaves something to read.
UTF-8 is forced in process, which removes DEPLOYMENT.md §12's
PYTHONIOENCODING prerequisite from the task registration. Pruning matches
only pipeline-*.log — run-<id>.log belongs to the web app.
EOF
)"
```

---

### Task 3: Overlap lock

**Files:**
- Modify: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Task 2's module.
- Produces:
  - `LOCK_NAME: str` = `"pipeline.lock"`, `MAX_LOCK_AGE_H: int` = 12
  - `pid_alive(pid: int) -> bool`
  - `acquire_lock(path, pid=None, pid_alive=pid_alive, now=None) -> tuple[bool, dict | None]` — `(acquired, holder)`; `holder` is the previous payload when there was one
  - `release_lock(path, pid=None) -> bool`

**Why the age guard.** Liveness alone is not enough. Task Scheduler's 3h
`ExecutionTimeLimit` terminates a run without letting the `finally` fire, so the
lock file survives holding our pid; Windows can then recycle that pid to an
unrelated service, `pid_alive` reports it alive every morning, and the pipeline
is dead forever with no notification. `MAX_LOCK_AGE_H = 12` bounds that: it
comfortably exceeds the 3h execution limit that produces the stranded lock, and
sits under the 24h schedule interval, so the next 06:00 run always recovers.
Matching the holder's psutil `create_time` would distinguish a recycled pid
exactly, but the age guard already bounds the damage to one missed day, so that
precision is deliberately not built.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k lock`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'acquire_lock'`

- [ ] **Step 3: Write the minimal implementation**

Add `import json` to the imports. Add after `prune_logs`:

```python
LOCK_NAME = "pipeline.lock"
# Exceeds §13's 3h ExecutionTimeLimit (which is what strands a lock) and stays
# under the 24h schedule interval, so the next run always recovers.
MAX_LOCK_AGE_H = 12


def pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False              # can't tell -> treat as dead, same as RunManager


def _lock_expired(holder, now) -> bool:
    """True when the holder's timestamp is older than MAX_LOCK_AGE_H, or is
    missing, unparseable, or in the future. An unusable timestamp must read as
    stale — never as infinite age, which would block the pipeline forever."""
    raw = (holder or {}).get("started_at")
    try:
        when = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when > now:                      # clock skew / DST: don't trust it
        return True
    return (now - when) > timedelta(hours=MAX_LOCK_AGE_H)


def acquire_lock(path: str, pid=None, pid_alive=pid_alive, now=None):
    """Take the overlap lock. Returns (acquired, holder). `-StartWhenAvailable`
    plus a slow run can otherwise re-enter while the previous one is still
    working. A dead — or merely expired — holder's lock is reclaimed and
    returned so the caller can log who died."""
    pid = os.getpid() if pid is None else pid
    now = now or datetime.now(timezone.utc)
    holder = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fp:
                holder = json.loads(fp.read())
        except Exception:
            holder = {}             # corrupt: treat as stale, not as a blocker
        hpid = holder.get("pid") if isinstance(holder, dict) else None
        if (isinstance(hpid, int) and hpid != pid and pid_alive(hpid)
                and not _lock_expired(holder, now)):
            return False, holder
    payload = {"pid": pid, "started_at": now.isoformat(timespec="seconds")}
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp)
    return True, holder


def release_lock(path: str, pid=None) -> bool:
    """Remove the lock only if we still hold it, so a reclaimed-from-us lock
    isn't deleted out from under its new owner."""
    pid = os.getpid() if pid is None else pid
    try:
        with open(path, encoding="utf-8") as fp:
            if json.loads(fp.read()).get("pid") != pid:
                return False
        os.remove(path)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: overlap lock so a slow pipeline run cannot re-enter

-StartWhenAvailable plus a run that outlives its window would otherwise start
a second one on top of the first. A dead holder's lock is reclaimed rather
than blocking forever, and release only removes a lock we still own.
EOF
)"
```

---

### Task 4: Collector stage runner

**Files:**
- Modify: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `build_collector_cmd(name, paths, no_email) -> list[str]`
  - `kill_tree(pid) -> None`
  - `run_collector_stage(name, paths, timeout_s, no_email, log, spawn=None, clock=time.monotonic) -> StageOutcome`

`paths` is a `solaranalysis.web.paths.Paths`. `log` is a `Tee` (or any `str -> str` callable). `spawn(cmd)` must return an object with `.stdout` (a line iterator), `.wait()`, and `.pid` — the same shape `run_manager._default_spawn` returns, so the existing `FakeProc` test double works.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k collector`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'build_collector_cmd'`

- [ ] **Step 3: Write the minimal implementation**

Add to the imports:

```python
import subprocess
import threading
import time
```

Add after `release_lock`:

```python
def kill_tree(pid) -> None:
    """Kill a stage and its children. A Playwright stage leaves a Chromium
    behind if only the parent is killed.

    Deliberately a near-copy of RunManager._kill_tree rather than a call to it:
    that one is a private method on a web-layer class, and reaching into it
    would couple this module to RunManager's internals for ten lines."""
    if not pid:
        return
    try:
        import psutil
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        parent.kill()
    except Exception:
        pass


def _default_spawn(cmd):
    # Parallel to run_manager._default_spawn, not a reuse of it: that one is
    # private, and this one must add env= (see below), which it does not.
    #
    # PYTHONIOENCODING for the child mirrors force_utf8() for ourselves: the
    # collectors print Hebrew narratives and a "·" in their subject lines.
    # "utf-8:replace" — not bare "utf-8", which gives the child errors="strict"
    # so an unencodable surrogate still raises. This matches force_utf8()'s own
    # errors="replace" and the parent pipe's.
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace",
                            env=dict(os.environ,
                                     PYTHONIOENCODING="utf-8:replace"))


def build_collector_cmd(name: str, paths, no_email: bool) -> list[str]:
    cmd = [sys.executable, "-m", STAGE_MODULES[name],
           "--data-dir", paths.data_dir, "--app-dir", paths.app_dir]
    if no_email:
        cmd.append("--no-email")
    return cmd


def run_collector_stage(name, paths, timeout_s, no_email, log,
                        spawn=None, clock=time.monotonic) -> StageOutcome:
    """Run one collector as its own process, tee-ing its output. A watchdog
    kills the tree on timeout — the stdout iteration below has no timeout of
    its own, so a stage that hangs mid-stream would otherwise block forever.
    Same watchdog approach as RunManager.run_test."""
    started = clock()
    log(f"--- stage {name}: starting ---")
    try:
        proc = (spawn or _default_spawn)(build_collector_cmd(name, paths, no_email))
    except Exception as e:
        log(f"!!! stage {name}: could not start: {e}")
        return StageOutcome(name, "failed", duration_s=clock() - started,
                            detail=f"could not start: {e}")

    fired = threading.Event()

    def _fire():
        fired.set()
        kill_tree(getattr(proc, "pid", None))
        try:
            proc.kill()
        except Exception:
            pass

    watchdog = threading.Timer(timeout_s, _fire)
    watchdog.start()
    tail: list[str] = []
    code = None
    try:
        for raw in proc.stdout or []:
            tail.append(log(raw))
            del tail[:-TAIL_LINES]
        code = proc.wait()
    except Exception as e:
        tail.append(log(f"!!! stage {name}: reading output failed: {e}"))
    finally:
        watchdog.cancel()

    duration = clock() - started
    detail = "\n".join(tail)
    if fired.is_set():
        log(f"!!! stage {name}: TIMED OUT after {timeout_s / 60:.0f} min — killed")
        return StageOutcome(name, "timeout", exit_code=code, duration_s=duration,
                            detail=detail)
    if code == 0:
        log(f"--- stage {name}: ok ({duration:.0f}s) ---")
        return StageOutcome(name, "ok", exit_code=0, duration_s=duration,
                            detail=detail)
    log(f"!!! stage {name}: FAILED exit {code} ({duration:.0f}s)")
    return StageOutcome(name, "failed", exit_code=code, duration_s=duration,
                        detail=detail)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: run the optimizer and string collectors as isolated stages

Each collector gets its own process, a watchdog that kills the whole tree
(Playwright leaves a Chromium behind otherwise), and a bounded tail kept for
the alert email. Every nonzero exit is a failure, including 2 — on a server
past §10, "no enabled plant in app.db" is a regression, not a steady state.
EOF
)"
```

---

### Task 5: Fleet stage runner

**Files:**
- Modify: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `CANCEL_GRACE_S: int` = 30, `STRANDED_RUN_GRACE_MIN: int` = 15
  - `live_run_holders(conn, now, pid_alive=pid_alive, grace_min=STRANDED_RUN_GRACE_MIN) -> tuple[list[dict], list[dict]]` — `(live, stranded)`
  - `run_fleet_stage(paths, time_range, timeout_s, log, rm=None, clock=time.monotonic, now=None, pid_alive=pid_alive) -> StageOutcome`

`rm` defaults to a fresh `run_manager.RunManager(paths)`. The stage opens and closes its own short-lived DB connections, so the status read-back cannot see a stale snapshot of what the pump thread wrote on its own connection.

**Why a stranded row must be reclaimed, not obeyed.** Treating *any*
`status='running'` row as a live run is a silent-forever trap, and it is trivially
reachable: one Ctrl-C during §13's own by-hand run raises `KeyboardInterrupt`,
which `except Exception` does not catch, so the daemon pump thread dies before
`_finish()` writes a status. Task Scheduler's 3h limit and a 06:30 reboot do the
same. The row then stays `running` until the NSSM service restarts — only the web
app's FastAPI startup calls `reconcile_on_startup()`. From that morning on, every
run logs `SKIPPED`, sets no bit, exits `0`, and mails nothing. That is exactly the
"looks healthy for weeks" failure this feature exists to eliminate, and it is the
same silent-skip this plan already refuses to accept for collector exit 2.

Three rules make the reclamation safe:

- **A live pid is yielded to, never killed.** It may be a legitimate UI run.
  This is why `reconcile_on_startup()` is not reused — it kills the holder.
- **A NULL `runner_pid` is not "dead".** `repo.create_run` commits
  `status='running'` before `set_run_pid` runs after the spawn returns, so a
  genuinely starting run has a real window with no pid. Reclaiming it
  immediately would clobber that run and start the second concurrent fleet run
  the check exists to prevent. NULL-pid rows are therefore judged on age only.
- **Age is the backstop for a recycled pid.** `pid_exists` can report a reused
  pid as alive; anything older than the grace window is stranded regardless.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k fleet`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'run_fleet_stage'`

- [ ] **Step 3: Write the minimal implementation**

Add to the imports:

```python
from .web import crypto, db, repo, run_manager
from .web.paths import Paths
```

Add after `run_collector_stage`:

```python
CANCEL_GRACE_S = 30          # let _finish() write "cancelled" after a kill
# A `running` row younger than this is presumed live even with no pid recorded:
# create_run commits before set_run_pid, so a starting run has a pidless window.
STRANDED_RUN_GRACE_MIN = 15


def live_run_holders(conn, now, pid_alive=pid_alive,
                     grace_min=STRANDED_RUN_GRACE_MIN):
    """Split `status='running'` rows into (live, stranded).

    Live: a recorded pid that is still alive, or any row inside the grace
    window — including one with no pid yet. Stranded: everything older than the
    window whose pid is gone or was never recorded. Age is also the backstop for
    a recycled pid, which `pid_exists` would wrongly call alive."""
    live, stranded = [], []
    for r in repo.running_runs(conn):
        try:
            age = now - datetime.fromisoformat(str(r.get("started_at")))
        except (TypeError, ValueError):
            age = timedelta(days=365)          # unusable timestamp -> stranded
        fresh = age <= timedelta(minutes=grace_min)
        pid = r.get("runner_pid")
        if fresh or (pid and pid_alive(pid) and age <= timedelta(hours=24)):
            live.append(r)
        else:
            stranded.append(r)
    return live, stranded


def run_fleet_stage(paths, time_range, timeout_s, log, rm=None,
                    clock=time.monotonic, now=None,
                    pid_alive=pid_alive) -> StageOutcome:
    """Drive the fleet run through the web app's RunManager, so a scheduled run
    creates a normal `runs` row and still appears in run history. Short-lived
    connections only: the pump writes from another thread on its own
    connection."""
    started = clock()
    now = now or datetime.now(timezone.utc)
    conn = db.connect(paths.db_path)
    try:
        active, stranded = live_run_holders(conn, now, pid_alive=pid_alive)
        for r in stranded:
            # Never kill the holder — only mark the row done. A row left
            # 'running' by a Ctrl-C, a 3h timeout kill, or a reboot would
            # otherwise silence the fleet report until the service restarts.
            log(f"[note] reclaimed a stranded runs row {r['id']} "
                f"(started {r.get('started_at')}, pid {r.get('runner_pid')} gone)")
            try:
                repo.mark_interrupted(
                    conn, r["id"],
                    finished_at=now.isoformat(timespec="seconds"))
            except Exception as e:
                log(f"[warn] could not reclaim runs row {r['id']}: {e}")
    finally:
        conn.close()
    if active:
        ids = ", ".join(str(r["id"]) for r in active)
        log(f"!!! stage fleet: SKIPPED — run(s) {ids} already active. Is a "
            f"Settings -> Schedules row or the web UI driving a run? "
            f"DEPLOYMENT.md §13 says there should be no schedule row.")
        return StageOutcome("fleet", "skipped", duration_s=clock() - started,
                            detail=f"run(s) {ids} were already active")

    rm = rm if rm is not None else run_manager.RunManager(paths)
    log(f"--- stage fleet: starting ({time_range}) ---")
    try:
        rid = rm.start_run("scheduled", time_range)
    except Exception as e:
        log(f"!!! stage fleet: could not start: {e}")
        return StageOutcome("fleet", "failed", duration_s=clock() - started,
                            detail=f"could not start: {e}")

    log(f"--- stage fleet: run {rid} — detail in logs/run-{rid}.log ---")
    rm.join(rid, timeout=timeout_s)
    act = rm.active()
    timed_out = bool(act and act.get("id") == rid)
    if timed_out:
        log(f"!!! stage fleet: TIMED OUT after {timeout_s / 60:.0f} min — "
            f"cancelling run {rid}")
        rm.cancel(rid)
        rm.join(rid, timeout=CANCEL_GRACE_S)

    conn = db.connect(paths.db_path)
    try:
        run = repo.get_run(conn, rid) or {}
    finally:
        conn.close()
    status = run.get("status")
    duration = clock() - started
    log_ref = run.get("log_path")
    if timed_out:
        return StageOutcome("fleet", "timeout", duration_s=duration,
                            detail=run.get("error") or f"run {rid} cancelled "
                                                       f"after timeout",
                            log_ref=log_ref)
    if status in ("success", "partial"):
        log(f"--- stage fleet: {status} ({duration:.0f}s) ---")
        return StageOutcome("fleet", "ok", exit_code=0, duration_s=duration,
                            detail=f"run {rid} {status}", log_ref=log_ref)
    log(f"!!! stage fleet: FAILED — run {rid} ended '{status}' ({duration:.0f}s)")
    return StageOutcome("fleet", "failed", duration_s=duration,
                        detail=run.get("error") or f"run {rid} ended '{status}'",
                        log_ref=log_ref)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: drive the fleet stage through RunManager

Reusing the web app's own run path means a scheduled run creates a normal runs
row and still shows up in the UI's history, instead of a second copy of the
pump/finalize logic. A run already in flight is skipped rather than doubled,
and partial stays a success — that report already names its own gaps.
EOF
)"
```

---

### Task 6: Failure alert email

**Files:**
- Modify: `solaranalysis/orchestrator.py`, `tests/conftest.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `resolve_recipients() -> list[str]`
  - `should_alert(outcomes, no_email: bool) -> bool`
  - `alert_subject(outcomes, stamp: str) -> str`
  - `compose_alert_html(outcomes, log_path: str, stamp: str) -> str`
  - `send_alert(outcomes, log_path, stamp, send=None) -> tuple[bool, str]` — `(sent, message_to_log)`

- [ ] **Step 1: Write the failing tests**

First extend the autouse scrub in `tests/conftest.py`. Two keys are being added
for two different reasons, so the tuple is renamed and the comment extended —
`SOLAR_NO_EMAIL` is not a Graph credential:

```python
# Two groups, both scrubbed for every test.
#
# The Graph credentials the mailer reads: scrubbed so the suite can never send
# real email — this covers both the in-process mailer (is_configured()/
# recipients() read os.getenv) AND any real runner subprocess a test spawns,
# which inherits this process's environment. Without this, a developer whose
# shell has real GRAPH_* set would have the runner tests that don't stub the
# mailer email fixture content to the real recipient.
#
# SOLAR_NO_EMAIL is the opposite hazard: an inherited or leaked suppression flag
# silently turns the runner's email path off, so tests that assert a report was
# emailed fail somewhere far from the cause. Task 8's orchestrator test writes
# it into os.environ through main(), so without this every later test in the
# session — all of tests/web/test_runner.py — would run suppressed.
_SCRUBBED_ENV_KEYS = (
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
    "GRAPH_SENDER", "REPORT_RECIPIENTS", "PIPELINE_RECIPIENTS",
    "SOLAR_NO_EMAIL",
)


@pytest.fixture(autouse=True)
def _scrub_graph_env(monkeypatch):
    for key in _SCRUBBED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
```

This is load-bearing, not hygiene. Verified empirically: with `SOLAR_NO_EMAIL=1`
in the environment and Task 7's branch in place, `tests/web/test_runner.py` goes
from **8 failed, 17 passed** to **25 passed** purely by adding that one key.
`tests/test_*.py` is collected before `tests/web/`, so the leak flows downstream.

Append to `tests/test_orchestrator.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k "alert or recipients"`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'should_alert'`

- [ ] **Step 3: Write the minimal implementation**

Add `import html as _html` to the imports, and `mailer` to the web import:

```python
from .web import crypto, db, mailer, repo, run_manager
```

Add after `run_fleet_stage`:

```python
_STATUS_COLOR = {"ok": "#1a7f37", "skipped": "#9a6700",
                 "failed": "#b42318", "timeout": "#b42318"}


def resolve_recipients() -> list[str]:
    """PIPELINE_RECIPIENTS overrides the app's configured list — the same
    pattern as OPTIMIZER_RECIPIENTS and STRING_RECIPIENTS."""
    raw = os.getenv("PIPELINE_RECIPIENTS", "").strip()
    if not raw:
        return mailer.recipients()
    out, seen = [], set()
    for part in raw.split(","):
        addr = part.strip()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def should_alert(outcomes, no_email: bool) -> bool:
    """Only a real failure. A `partial` fleet run already emailed a report that
    names its own gaps, and a skipped stage means the work is happening
    elsewhere."""
    return (not no_email) and any(o.failed for o in outcomes)


def alert_subject(outcomes, stamp: str) -> str:
    bad = ", ".join(o.name for o in outcomes if o.failed)
    return f"Solar pipeline · FAILED · {bad} · {stamp} UTC"


def compose_alert_html(outcomes, log_path: str, stamp: str) -> str:
    """Inline styles only: Outlook and Gmail ignore the on-disk report's CSS
    variables, the same constraint render_email_html works around."""
    esc = _html.escape
    rows = []
    for o in outcomes:
        color = _STATUS_COLOR.get(o.status, "#57606a")
        code = "—" if o.exit_code is None else str(o.exit_code)
        rows.append(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #d8dee4">'
            f'{esc(o.name)}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #d8dee4;'
            f'color:{color};font-weight:600">{esc(o.status)}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #d8dee4;'
            f'text-align:right">{esc(code)}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #d8dee4;'
            f'text-align:right">{o.duration_s:.0f}s</td></tr>')
    details = []
    for o in outcomes:
        if not (o.failed and (o.detail or o.log_ref)):
            continue
        ref = (f'<div style="color:#57606a;font-size:13px">also: '
               f'{esc(o.log_ref)}</div>' if o.log_ref else "")
        details.append(
            f'<h3 style="font:600 15px system-ui;margin:18px 0 6px">'
            f'{esc(o.name)}</h3>{ref}'
            f'<pre style="background:#f6f8fa;border:1px solid #d8dee4;'
            f'border-radius:6px;padding:10px;font:12px/1.45 Consolas,monospace;'
            f'white-space:pre-wrap;overflow-x:auto">{esc(o.detail)}</pre>')
    return (
        f'<div style="font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#1f2328;'
        f'max-width:760px">'
        f'<h2 style="font:600 18px system-ui;margin:0 0 4px">'
        f'Solar pipeline failed</h2>'
        f'<div style="color:#57606a;margin-bottom:14px">{esc(stamp)} UTC</div>'
        f'<table style="border-collapse:collapse;width:100%;'
        f'border:1px solid #d8dee4;border-radius:6px">'
        f'<tr style="background:#f6f8fa">'
        f'<th style="padding:6px 12px;text-align:left">stage</th>'
        f'<th style="padding:6px 12px;text-align:left">outcome</th>'
        f'<th style="padding:6px 12px;text-align:right">exit</th>'
        f'<th style="padding:6px 12px;text-align:right">took</th></tr>'
        f'{"".join(rows)}</table>'
        f'{"".join(details)}'
        f'<div style="color:#57606a;font-size:13px;margin-top:16px">'
        f'full log: {esc(log_path)}</div></div>')


def send_alert(outcomes, log_path, stamp, send=None):
    """(sent, message_to_log). Never raises: an alert problem must not change
    the exit code that describes the actual work."""
    to = resolve_recipients()
    if not (mailer.is_configured() and to):
        return False, "alert not sent: email not configured"
    try:
        (send or mailer.send_report)(alert_subject(outcomes, stamp),
                                     compose_alert_html(outcomes, log_path, stamp),
                                     to=to)
        return True, f"alert emailed to {', '.join(to)}"
    except Exception as e:
        return False, f"alert email failed: {e}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/orchestrator.py tests/conftest.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: alert email when a pipeline stage fails

A failed fleet run mails nothing at all, so an unattended failure was pure
silence. The alert carries each stage's outcome plus the failed stage's own
evidence — the runs row's error for the fleet stage, the tee'd tail for the
collectors — so it is actionable without an RDP session. Clean days stay
quiet, and a send failure never changes the exit code.
EOF
)"
```

---

### Task 7: Teach `web/runner.py` to honour `--no-email`

**Files:**
- Modify: `solaranalysis/web/runner.py:156-169`
- Test: `tests/web/test_runner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the `SOLAR_NO_EMAIL` environment contract — when set to `"1"`, `run_analysis_job` skips sending and emits a `note` event instead. Task 8 sets it.

Without this, the orchestrator's `--no-email` would be a half-truth and §13's dry run would fire a real fleet report email at the live recipient list.

- [ ] **Step 1: Write the failing test**

Read `tests/web/test_runner.py` first and follow its existing fixture style. Append:

```python
def test_no_email_env_suppresses_the_report_email(tmp_path, monkeypatch):
    # The orchestrator's --no-email has to cover the fleet stage too, or
    # DEPLOYMENT.md §13's dry run mails a real report to the live list.
    monkeypatch.setenv("SOLAR_NO_EMAIL", "1")
    assert runner.email_suppressed() is True


def test_email_is_not_suppressed_by_default(monkeypatch):
    monkeypatch.delenv("SOLAR_NO_EMAIL", raising=False)
    assert runner.email_suppressed() is False
    monkeypatch.setenv("SOLAR_NO_EMAIL", "0")
    assert runner.email_suppressed() is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_runner.py -v -k email`
Expected: FAIL with `AttributeError: module 'solaranalysis.web.runner' has no attribute 'email_suppressed'`

- [ ] **Step 3: Write the minimal implementation**

Add to `solaranalysis/web/runner.py`, above `run_analysis_job`:

```python
def email_suppressed() -> bool:
    """The orchestrator sets SOLAR_NO_EMAIL=1 for its --no-email dry runs. The
    web app never sets it, so its behaviour is unchanged."""
    return os.getenv("SOLAR_NO_EMAIL") == "1"
```

Then change the mail block's condition (currently `runner.py:157`) from:

```python
            if mailer.is_configured() and mailer.recipients():
```

to:

```python
            if email_suppressed():
                events.emit_event({"event": "note",
                                   "reason": "email suppressed (SOLAR_NO_EMAIL)"})
            elif mailer.is_configured() and mailer.recipients():
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/web/ -v`
Expected: PASS — the two new tests plus every existing web test unchanged

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/web/runner.py tests/web/test_runner.py
git commit -m "$(cat <<'EOF'
feat: SOLAR_NO_EMAIL suppresses the fleet run's report email

The orchestrator's --no-email has to reach the fleet stage too, or a dry run
mails a real report to the live recipient list. The web app never sets the
variable, so nothing about its behaviour changes.
EOF
)"
```

---

### Task 8: Wire `main()`

**Files:**
- Modify: `solaranalysis/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `collect_secrets(paths) -> tuple[list[str], str | None]` — `(secrets, warning)`
  - `main(argv=None, now=None, fleet=None, collector=None, alert=None) -> int`
  - `python -m solaranalysis.orchestrator` via an `if __name__ == "__main__"` guard

`fleet`, `collector`, and `alert` are seams for the tests, defaulting to `run_fleet_stage`, `run_collector_stage`, and `send_alert`. Production callers pass none of them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
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


def test_main_returns_8_when_a_stage_raises_unexpectedly(tmp_path):
    (tmp_path / "app").mkdir()

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    rc = orch.main(_main_args(tmp_path), fleet=boom, collector=_fake_stage("ok"),
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py -v -k "main or collect_secrets"`
Expected: FAIL with `AttributeError: module 'solaranalysis.orchestrator' has no attribute 'main'`

- [ ] **Step 3: Write the minimal implementation**

Add `import traceback` to the imports and `from dotenv import load_dotenv`. Add at the end of `solaranalysis/orchestrator.py`:

```python
def collect_secrets(paths):
    """(secrets, warning). Redaction seed: every stored plant credential plus
    GRAPH_CLIENT_SECRET. Best-effort — a DB problem degrades redaction but must
    not stop the run, so it is reported as a warning to log, not raised."""
    out, warning = [], None
    try:
        conn = db.connect(paths.db_path)
        try:
            key = crypto.load_or_create_key(paths.key_path)
            for p in repo.list_plants(conn):
                auth = repo.load_plant_auth(conn, key, p["id"])
                if auth and auth.password:
                    out.append(auth.password)
                if auth and auth.token:
                    out.append(auth.token)
        finally:
            conn.close()
    except Exception as e:
        warning = f"could not load credentials for log redaction: {e}"
    secret = os.getenv("GRAPH_CLIENT_SECRET")
    if secret:
        out.append(secret)
    return out, warning


def main(argv=None, now=None, fleet=None, collector=None, alert=None) -> int:
    force_utf8()
    # force_utf8() covers our own streams; this covers every child's. The fleet
    # runner is spawned by RunManager._default_spawn, which passes no env=, so
    # it inherits ours — this is what makes §13's "no PYTHONIOENCODING needed"
    # true for all three stages. Unconditional assignment, never setdefault: a
    # leftover machine-scope cp1255 from the §11/§12 era would otherwise win.
    os.environ["PYTHONIOENCODING"] = "utf-8:replace"
    args = _build_parser().parse_args(argv)
    try:
        stages = parse_only(args.only)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return ORCHESTRATOR_FAILED

    # utc_stamp does no I/O, so it is safe out here — and the handlers below
    # reference `stamp`, so it must be bound before anything can raise.
    stamp = utc_stamp(now() if callable(now) else now)
    lock_path = None
    log_path = None
    try:
        paths = Paths.create(args.data_dir, args.app_dir)
        load_dotenv(paths.env_file)
        if args.no_email:
            # Reaches the fleet stage's runner subprocess, which inherits our env.
            os.environ["SOLAR_NO_EMAIL"] = "1"
        lock_path = os.path.join(paths.data_dir, LOCK_NAME)
        # Pass pid_alive explicitly: acquire_lock binds it as a *default
        # argument* at def time, so monkeypatching orch.pid_alive would be a
        # no-op. Naming it here makes the module global resolve at call time,
        # which is what the lock-held test patches. Same object in production.
        acquired, holder = acquire_lock(lock_path, pid_alive=pid_alive)
    except Exception as e:
        # No log file can exist yet, so stderr is the only channel. This guard
        # exists because an uncaught raise here would exit 1 — which §13's decode
        # table reads as "fleet failed". Reachable: a full or ACL-denied data
        # volume makes Paths.create's makedirs or the lock's open() throw.
        print(f"orchestrator failed before staging: {e}\n{traceback.format_exc()}",
              file=sys.stderr)
        return ORCHESTRATOR_FAILED

    if not acquired:
        print(f"previous pipeline run still active (pid {(holder or {}).get('pid')}, "
              f"started {(holder or {}).get('started_at')}); exiting")
        lock_path = None          # not ours — the finally must not touch it
        return ORCHESTRATOR_FAILED

    timeouts = {"fleet": args.timeout_fleet * 60,
                "optimizers": args.timeout_optimizers * 60,
                "strings": args.timeout_strings * 60}
    log_path = os.path.join(paths.logs_dir, f"{LOG_PREFIX}{stamp}.log")
    try:
        prune_logs(paths.logs_dir, args.log_retention_days)
        # Schema first: collect_secrets below reads `plants`, so on a fresh data
        # dir the other order logs a spurious "no such table: plants" warning.
        # Additive only (CREATE TABLE IF NOT EXISTS); the fleet stage's
        # create_run still precedes anything the runner would migrate.
        try:
            conn = db.connect(paths.db_path)
            try:
                db.init_db(conn)
            finally:
                conn.close()
        except Exception as e:
            # A handled failure here must be as loud as an unhandled one: a bare
            # `return` would skip the outer handler's alert, and a corrupt or
            # locked app.db would then produce three missing reports and total
            # silence. `log` is not bound yet, so report independently.
            detail = f"could not open {paths.db_path}: {e}\n{traceback.format_exc()}"
            print(f"!!! {detail}", file=sys.stderr)
            try:
                with open(log_path, "a", encoding="utf-8") as fp:
                    fp.write(f"!!! {detail}\n")
            except Exception:
                pass
            outcome = StageOutcome("orchestrator", "failed", detail=detail)
            if should_alert([outcome], args.no_email):
                try:
                    (alert or send_alert)([outcome], log_path, stamp)
                except Exception:
                    pass          # never let an alert problem mask the DB error
            return ORCHESTRATOR_FAILED

        secrets, secrets_warning = collect_secrets(paths)
        with open(log_path, "a", encoding="utf-8") as fp:
            log = Tee(fp, events.Redactor(secrets))
            log(f"=== solar pipeline {stamp} UTC — stages: {', '.join(stages)} ===")
            if holder:
                log(f"[note] reclaimed a stale lock from pid {holder.get('pid')} "
                    f"(started {holder.get('started_at')})")
            if secrets_warning:
                log(f"[warn] {secrets_warning}")

            outcomes = []
            for name in stages:
                if name == "fleet":
                    outcomes.append((fleet or run_fleet_stage)(
                        paths, args.range, timeouts["fleet"], log))
                else:
                    outcomes.append((collector or run_collector_stage)(
                        name, paths, timeouts[name], args.no_email, log))

            code = aggregate_exit_code(outcomes)
            log("=== summary ===")
            for o in outcomes:
                log(f"  {o.name:<11} {o.status:<8} exit "
                    f"{'—' if o.exit_code is None else o.exit_code:<4} "
                    f"{o.duration_s:.0f}s")
            log(f"pipeline finished: exit {code}")
            if should_alert(outcomes, args.no_email):
                log((alert or send_alert)(outcomes, log_path, stamp)[1])
            return code
    except Exception as e:
        # Last resort: say so on stdout, in the log if we can, and by email.
        detail = f"{e}\n{traceback.format_exc()}"
        print(f"orchestrator failed: {detail}", file=sys.stderr)
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as fp:
                    fp.write(f"orchestrator failed: {detail}\n")
            except Exception:
                pass
        if not args.no_email:
            try:
                # A synthetic stage name: this failure was the orchestrator's,
                # and naming a real stage in the subject would misdirect.
                (alert or send_alert)(
                    [StageOutcome("orchestrator", "failed", detail=detail)],
                    log_path or "(no pipeline log was created)", stamp)
            except Exception:
                pass
        return ORCHESTRATOR_FAILED
    finally:
        # Guarded: an exception raised in a `finally` discards the pending
        # return and propagates, which would exit 1 — the very mis-decode this
        # function works to avoid. `lock_path` is None when we never took the
        # lock, or when a live holder owns it.
        if lock_path:
            release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
```

Also add `events` to the web import line:

```python
from .web import crypto, db, events, mailer, repo, run_manager
```

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — every existing test plus the new orchestrator tests

- [ ] **Step 5: Verify the real entry point starts and reports honestly**

Run:
```powershell
.venv\Scripts\python.exe -m solaranalysis.orchestrator --data-dir data --app-dir . --only strings --no-email
```
Expected: it acquires the lock, writes `data\logs\pipeline-*.log`, runs the string collector as a real subprocess, and exits `0` or `4` depending on whether the local `app.db` has an enabled Growatt plant. Either is acceptable here — what must be true is that the log exists, names the stage, and the exit code matches the summary line. Confirm with `echo $LASTEXITCODE`.

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/orchestrator.py tests/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat: wire the orchestrator entry point

python -m solaranalysis.orchestrator takes the overlap lock, prunes old logs,
seeds redaction from the stored credentials, runs the selected stages in
order, and exits with the bitmask. An unhandled error still logs, still
alerts, and still releases the lock.
EOF
)"
```

---

### Task 9: `daily_pipeline.py` and the shared venv relaunch

**Files:**
- Create: `_venv.py`, `daily_pipeline.py`
- Modify: `app.py:15-39`
- Test: `tests/test_entrypoints.py`

**Interfaces:**
- Consumes: Task 8's `orchestrator.main`.
- Produces: `_venv.relaunch(script_path, guard="SOLAR_APP_IN_VENV") -> int | None` — `None` means the current interpreter is already correct.

`app.py` currently owns this logic privately at lines 15-39 (ending at `_relaunch_in_venv`'s final `return 0`; `def main()` starts at 42). Extracting it is what lets `daily_pipeline.py` reuse it instead of holding a second copy; the guard variable name stays `SOLAR_APP_IN_VENV` so nothing about `python app.py` changes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entrypoints.py`:

```python
"""The two root entry points must run under .venv.

Under a non-venv interpreter the installed anthropic SDK is old enough that the
Claude narrative calls fail *silently*: the run exits 0 and still emails, with
no narrative in the report. A scheduled task that got this wrong would look
healthy for weeks.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import _venv                                          # noqa: E402
import app                                            # noqa: E402
import daily_pipeline                                 # noqa: E402


def test_app_still_delegates_to_the_shared_relaunch(monkeypatch):
    # `python app.py --help` cannot catch a broken delegation: usage prints
    # either way whenever the system interpreter has the deps installed, which
    # on this machine it does (just an older anthropic). Pin the wiring here.
    assert callable(app.main) and callable(app._relaunch_in_venv)
    assert app.ROOT == _venv.ROOT and app.VENV_PY == _venv.VENV_PY
    seen = {}
    monkeypatch.setattr(_venv, "relaunch",
                        lambda path, **kw: seen.update(path=path) or 3)
    assert app._relaunch_in_venv() == 3
    assert os.path.basename(seen["path"]) == "app.py"


def test_relaunch_is_a_noop_once_the_guard_is_set(monkeypatch):
    monkeypatch.setenv("SOLAR_APP_IN_VENV", "1")
    assert _venv.relaunch(str(ROOT / "app.py")) is None


def test_relaunch_is_a_noop_when_no_venv_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("SOLAR_APP_IN_VENV", raising=False)
    monkeypatch.setattr(_venv, "VENV_PY", str(tmp_path / "nope" / "python.exe"))
    assert _venv.relaunch(str(ROOT / "app.py")) is None


def test_relaunch_reexecutes_under_the_venv_and_sets_the_guard(monkeypatch,
                                                              tmp_path):
    monkeypatch.delenv("SOLAR_APP_IN_VENV", raising=False)
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(_venv, "VENV_PY", str(fake_py))
    seen = {}

    class Done:
        returncode = 7
    monkeypatch.setattr(_venv.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env"))
                        or Done())
    assert _venv.relaunch(str(ROOT / "daily_pipeline.py")) == 7
    assert seen["cmd"][0] == str(fake_py)
    assert seen["env"]["SOLAR_APP_IN_VENV"] == "1"


def test_daily_pipeline_defaults_both_dirs_to_the_repo(monkeypatch):
    seen = {}
    monkeypatch.setattr(daily_pipeline, "_relaunch", lambda: None)
    monkeypatch.setattr(daily_pipeline, "_orchestrator_main",
                        lambda argv: seen.update(argv=argv) or 0)
    assert daily_pipeline.main([]) == 0
    argv = seen["argv"]
    assert argv[argv.index("--data-dir") + 1] == os.path.join(str(ROOT), "data")
    assert argv[argv.index("--app-dir") + 1] == str(ROOT)


def test_daily_pipeline_does_not_override_explicit_dirs(monkeypatch):
    seen = {}
    monkeypatch.setattr(daily_pipeline, "_relaunch", lambda: None)
    monkeypatch.setattr(daily_pipeline, "_orchestrator_main",
                        lambda argv: seen.update(argv=argv) or 0)
    daily_pipeline.main(["--data-dir", "D:\\d", "--app-dir", "D:\\a", "--only",
                         "strings"])
    argv = seen["argv"]
    assert argv.count("--data-dir") == 1 and "D:\\d" in argv
    assert argv.count("--app-dir") == 1 and "D:\\a" in argv
    assert "--only" in argv and "strings" in argv
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_entrypoints.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named '_venv'`

- [ ] **Step 3: Create `_venv.py`**

```python
"""Shared venv relaunch for the root entry points (`app.py`, `daily_pipeline.py`).

Under a non-venv interpreter the installed anthropic SDK is old enough that the
Claude narrative calls fail *silently* — the run exits 0 and still emails, with
no narrative in the report. Stdlib only, so it is importable before any
dependency is installed.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = (os.path.join(ROOT, ".venv", "Scripts", "python.exe") if os.name == "nt"
           else os.path.join(ROOT, ".venv", "bin", "python"))
GUARD = "SOLAR_APP_IN_VENV"


def same_interpreter(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == \
            os.path.normcase(os.path.realpath(b))


def relaunch(script_path: str, guard: str = GUARD) -> int | None:
    """Re-run `script_path` with the venv's Python. None = already correct."""
    if os.environ.get(guard) == "1":
        return None
    if not os.path.isfile(VENV_PY) or same_interpreter(VENV_PY, sys.executable):
        return None
    env = dict(os.environ, **{guard: "1"})
    try:
        return subprocess.run([VENV_PY, os.path.abspath(script_path),
                               *sys.argv[1:]], cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        return 0
```

- [ ] **Step 4: Create `daily_pipeline.py`**

```python
"""Scheduled entry point for the whole pipeline: `python daily_pipeline.py`.

Thin wrapper over `python -m solaranalysis.orchestrator`, mirroring `app.py`: it
re-launches itself under the project's `.venv` when started with some other
Python, and defaults `--data-dir`/`--app-dir` to this repo. All other arguments
pass straight through, e.g. `python daily_pipeline.py --only strings --no-email`.

The registered Scheduled Task calls `-m solaranalysis.orchestrator` directly
(DEPLOYMENT.md §13); this file is the by-hand form.
"""
from __future__ import annotations

import os
import sys

import _venv

ROOT = _venv.ROOT


def _relaunch():
    return _venv.relaunch(__file__)


def _orchestrator_main(argv):
    sys.path.insert(0, ROOT)
    from solaranalysis.orchestrator import main as orchestrator_main
    return orchestrator_main(argv)


def _with_default(argv: list[str], flag: str, value: str) -> list[str]:
    if any(a == flag or a.startswith(flag + "=") for a in argv):
        return argv
    return argv + [flag, value]


def main(argv=None) -> int:
    rc = _relaunch()
    if rc is not None:
        return rc
    if not os.path.isfile(_venv.VENV_PY):
        print(f"warning: no virtualenv at {os.path.join(ROOT, '.venv')} — "
              f"running with {sys.executable}; Claude narratives may fail "
              f"silently", file=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _with_default(argv, "--data-dir", os.path.join(ROOT, "data"))
    argv = _with_default(argv, "--app-dir", ROOT)
    try:
        return _orchestrator_main(argv)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Refactor `app.py` onto `_venv`**

Replace, in `app.py`, everything from the `ROOT = os.path.dirname(...)` line
through the `return 0` that ends `_relaunch_in_venv` — lines 15-39, stopping at
the blank line before `def main()`. Trust the anchor text over the numbers; a
prior edit to the file shifts them. That block is the `ROOT`/`VENV_PY`/`_GUARD`
constants, `_same_interpreter`, and `_relaunch_in_venv`. Replace it with:

```python
import _venv

ROOT = _venv.ROOT
VENV_PY = _venv.VENV_PY


def _relaunch_in_venv() -> int | None:
    return _venv.relaunch(__file__)
```

Leave `main()` and everything below it untouched — `_relaunch_in_venv()`,
`ROOT`, and `VENV_PY` keep the same names and meanings.

Also delete `import subprocess` from `app.py:12`. Its only use was inside the
extracted `_relaunch_in_venv`, so it is now dead. Keep `import os` and
`import sys` — `main()` still uses both.

**If this edit goes wrong it fails quietly.** Deleting through line 45 would take
`def main()` and its first statements with it, leaving the rest of `main`'s body
nested inside `_relaunch_in_venv`. That still imports and compiles; it fails only
when run, with `NameError: name 'main' is not defined`. Step 6's test below is
what catches it, because `tests/test_entrypoints.py` imports `app` directly.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_entrypoints.py -v`
Expected: PASS — every test in the file, including the ones just added

- [ ] **Step 7: Verify both entry points still work for real**

Run:
```powershell
python daily_pipeline.py --only strings --no-email
python app.py --help
```
Expected: `daily_pipeline.py` relaunches under `.venv` and runs the string stage
(exit `0` or `4` as in Task 8); `app.py --help` prints the server's usage, proving
the refactor did not break the entry point in daily use.

- [ ] **Step 8: Commit**

```bash
git add _venv.py daily_pipeline.py app.py tests/test_entrypoints.py
git commit -m "$(cat <<'EOF'
feat: python daily_pipeline.py entry point for the whole pipeline

Both root entry points now share one venv relaunch instead of two copies.
This is the guard that matters most for a scheduled task: under a non-venv
interpreter the anthropic SDK is old enough that narrative calls fail
silently, so a misconfigured task would email figure-less reports for weeks
and still exit 0.
EOF
)"
```

---

### Task 10: Documentation

**Files:**
- Modify: `DEPLOYMENT.md` (new §13; notes on §10, §11, §12), `README.md`, `NextTODO.md:203`

**Interfaces:**
- Consumes: the CLI surface from Tasks 1 and 8, the exit codes from Task 1.
- Produces: nothing code-facing.

- [ ] **Step 1: Add `DEPLOYMENT.md` §13**

Insert after §12 (before `## Update an existing deployment`). Follow §11/§12's
shape exactly: every step carries a **Verify**.

````markdown
### 13. Daily pipeline — one scheduled task for all three subsystems

This supersedes the three separate schedules for *scheduling only*. §11 and §12
remain the reference for manual runs and the backfills, which are still
prerequisites — **run their 90-day backfills before enabling this task**, or the
first week of string reports will be all-clear by construction.

**Do not also:**
- create a row in Settings → Schedules (disable any that exists) — it would run
  a second fleet report, from inside the web service, and email it;
- register `SolarAnalysis-Optimizers` or `SolarAnalysis-Strings`. On a server
  where they already exist:
  ```powershell
  Unregister-ScheduledTask -TaskName "SolarAnalysis-Optimizers" -Confirm:$false
  Unregister-ScheduledTask -TaskName "SolarAnalysis-Strings" -Confirm:$false
  ```

**Do step 10 first.** Both collectors read their plant's stored credentials from
`app.db`, which only exist once the plant is added through the web UI.

**Take a copy of `app.db` before the first run** if the deployment already holds
data you care about — the first run applies §12's additive v7 migration.

Dry run the two collectors, no email:

```powershell
cd C:\apps\solar-analysis
.\.venv\Scripts\python.exe -m solaranalysis.orchestrator `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis `
  --only optimizers,strings --no-email
```

**Verify:** `data\logs\pipeline-<stamp>.log` exists and ends with a `=== summary
===` block naming both stages, and `echo $LASTEXITCODE` is `0`. A `2` means the
optimizer plant is missing from `app.db`, `4` the Growatt one.

Then the whole pipeline, mail included:

```powershell
.\.venv\Scripts\python.exe -m solaranalysis.orchestrator `
  --data-dir C:\apps\solar-analysis\data --app-dir C:\apps\solar-analysis
```

**Verify:** three emails arrive (fleet dashboard, optimizer anomalies, string
anomalies), the run appears in the web UI's Runs history with trigger
`scheduled`, and the exit code is `0`. As in §11/§12, **a 0 exit code does not by
itself prove mail went out** — check the log's final lines.

Register the daily task at 06:00:

```powershell
$app = "C:\apps\solar-analysis"
$action = New-ScheduledTaskAction -Execute "$app\.venv\Scripts\python.exe" `
  -Argument "-m solaranalysis.orchestrator --data-dir $app\data --app-dir $app" `
  -WorkingDirectory $app
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SolarAnalysis-Pipeline" -Action $action `
  -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest
```

`-WorkingDirectory` must be the repo root: `.env` resolves relative to it, and
that is where `ANTHROPIC_API_KEY`, `GRAPH_*`, and the recipient lists come from.
Unlike §11/§12, `PYTHONIOENCODING` is **not** required — the orchestrator forces
UTF-8 on its own stdout and on every stage it spawns.

**Verify:** `Start-ScheduledTask -TaskName "SolarAnalysis-Pipeline"`, then
`(Get-ScheduledTaskInfo "SolarAnalysis-Pipeline").LastTaskResult` → `0`.

**Exit codes.** A bitmask, so the code names the stage:

| Code | Meaning |
|------|---------|
| 0 | every attempted stage ok |
| 1 | fleet failed |
| 2 | optimizers failed |
| 4 | strings failed |
| 3 / 5 / 6 / 7 | the combinations (e.g. 5 = fleet + strings) |
| 8 | the orchestrator itself could not run — a previous run still holds `data\pipeline.lock`, bad arguments, or an error before any stage |

A `partial` fleet run — some plants unavailable — is **not** a failure; that
report emails with its own "Unavailable Plants" section.

**When something breaks** you get one alert email listing every stage's outcome
plus the failed stage's own evidence. Recipients: `PIPELINE_RECIPIENTS` if set,
otherwise `REPORT_RECIPIENTS`. Clean days send no alert. Note the alert is the
*only* signal for a failed fleet run, which emails no report at all.

**Where to look:**
- `data\logs\pipeline-<stamp>.log` — stage boundaries, outcomes, timings, and the
  collectors' full output. Pruned after 30 days.
- `data\logs\run-<id>.log` — the fleet stage's own detail; the pipeline log names
  the id and path.
- `data\pipeline.lock` — present only while a run is in flight. A stale one from
  a killed run is reclaimed automatically on the next run: immediately if its pid
  is gone, and after 12 hours regardless, which covers a pid Windows has recycled
  to some other live process. You should never need to delete it by hand.

**Self-healing after a killed run.** A run killed mid-fleet-stage — Ctrl-C, the
3h limit, a reboot — leaves its `runs` row at `running`. The next run detects
that the holder is gone, logs `[note] reclaimed a stranded runs row <id>`, marks
it `interrupted`, and proceeds. A row younger than 15 minutes, or one whose pid
is still alive, is treated as a genuine run and yielded to instead: you will see
`SKIPPED` and no fleet report that morning, which is correct if someone is
running one from the UI. Two `SKIPPED` days in a row with nobody using the UI is
worth investigating.

**Tuning:** `--only fleet` to re-run a single stage by hand,
`--timeout-fleet N` / `--timeout-optimizers N` / `--timeout-strings N` in minutes
(defaults 45/60/30), `--range 30d` for a different window,
`--log-retention-days N`.
````

- [ ] **Step 2: Cross-reference from §10, §11, §12**

- In §10, after "The scheduler runs **inside this service**…", add: *"§13
  replaces this — do not create the schedule row there. The service is then only
  the UI and run history; scheduled reports come from the Scheduled Task."*
- At the top of §11 and §12, add: *"Superseded for scheduling by §13, which runs
  this collector as one of its stages. The dry run and backfill below are still
  the reference, and the backfill is still a prerequisite."*

- [ ] **Step 3: Add a README section**

After the "Email delivery" subsection, add:

````markdown
### Scheduled daily pipeline

One entry point runs everything the app produces, for Windows Task Scheduler:

```bash
python daily_pipeline.py                        # all three stages
python daily_pipeline.py --only strings --no-email
```

It runs the fleet comparison (through the same code path the web UI uses, so the
run appears in run history), then the SolarEdge optimizer collector, then the
Growatt string collector — each in its own process, continuing past a failure.
The exit code is a bitmask naming the failing stage (`1` fleet, `2` optimizers,
`4` strings, `8` the orchestrator itself), one log lands in
`data/logs/pipeline-<stamp>.log`, and any failure emails an alert to
`PIPELINE_RECIPIENTS` (falling back to `REPORT_RECIPIENTS`) — which matters
because a failed fleet run emails no report at all.

`python -m solaranalysis.orchestrator --data-dir DIR --app-dir DIR` is the same
thing without the venv wrapper; that is the form the Scheduled Task uses. See
`DEPLOYMENT.md` §13.
````

- [ ] **Step 4: Update `NextTODO.md`**

Replace line 203's three-schedule item with:

```markdown
- [ ] **Schedule (ops, on the server):** one `SolarAnalysis-Pipeline` task at
  ~06:00 running all three subsystems (`DEPLOYMENT.md` §13). Supersedes the
  three-schedule plan. Still to do on the server: §11/§12's backfills first,
  then the §13 dry run, then register. Do not create a Settings → Schedules row.
```

- [ ] **Step 5: Verify the docs match the code**

Run: `.venv\Scripts\python.exe -m solaranalysis.orchestrator --help`
Expected: every flag named in §13 and the README appears in the output with the
documented default. Fix the docs, not the code, if they disagree.

- [ ] **Step 6: Commit**

```bash
git add DEPLOYMENT.md README.md NextTODO.md
git commit -m "$(cat <<'EOF'
docs: DEPLOYMENT.md §13 — one scheduled task for the whole pipeline

Dry run, first real run, registration, and verifies, plus the exit-code decode
table and where to look when it breaks. States what it supersedes: the
Settings → Schedules row and §11/§12's separate tasks, with the unregister
commands, since leaving them live means duplicate runs and duplicate mail.
EOF
)"
```

---

## Final verification

- [ ] Run the whole suite: `.venv\Scripts\python.exe -m pytest -q` — all pass.
- [ ] `.venv\Scripts\python.exe -m solaranalysis.orchestrator --data-dir data --app-dir . --only optimizers,strings --no-email`, then confirm `data\logs\pipeline-*.log` holds a summary block and the exit code matches it.
- [ ] `python app.py --help` still prints the server usage (the `_venv` refactor did not break daily use).
- [ ] `git log --oneline` shows one commit per task, none carrying AI attribution.
