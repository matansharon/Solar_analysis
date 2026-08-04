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
import html as _html
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .web import crypto, db, mailer, repo, run_manager
from .web.paths import Paths

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
    try:
        to = resolve_recipients()
        if not mailer.is_configured():
            return False, "alert not sent: email not configured"
        if not to:
            return False, "alert not sent: no recipients configured"
        (send or mailer.send_report)(alert_subject(outcomes, stamp),
                                     compose_alert_html(outcomes, log_path, stamp),
                                     to=to)
        return True, f"alert emailed to {', '.join(to)}"
    except Exception as e:
        return False, f"alert email failed: {e}"
