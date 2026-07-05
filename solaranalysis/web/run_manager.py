from __future__ import annotations
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone

from . import db, repo, crypto, events
from .paths import Paths


class Busy(Exception):
    def __init__(self, active: dict):
        super().__init__(f"operation active: {active}")
        self.active = active


def _default_spawn(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunManager:
    def __init__(self, paths: Paths, spawn=None):
        self.paths = paths
        self._spawn = spawn or _default_spawn
        self._lock = threading.Lock()
        self._active = None            # {"kind","id","proc","cancel":bool}
        self._threads: dict[int, threading.Thread] = {}
        self._subs: dict[int, set] = {}
        self._progress: dict[int, dict] = {}
        self._subs_lock = threading.Lock()

    # ---- introspection -------------------------------------------------
    def active(self):
        with self._lock:
            if not self._active:
                return None
            return {"kind": self._active["kind"], "id": self._active["id"]}

    def get_progress(self, run_id):
        return self._progress.get(run_id)

    # ---- SSE fan-out ---------------------------------------------------
    def subscribe(self, run_id) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._subs_lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id, q):
        with self._subs_lock:
            self._subs.get(run_id, set()).discard(q)

    def _broadcast(self, run_id, msg):
        with self._subs_lock:
            for q in list(self._subs.get(run_id, set())):
                q.put(msg)

    # ---- start a run ---------------------------------------------------
    def _secrets(self, conn):
        key = crypto.load_or_create_key(self.paths.key_path)
        out = []
        for p in repo.list_plants(conn):
            auth = repo.load_plant_auth(conn, key, p["id"])
            if auth and auth.password:
                out.append(auth.password)
            if auth and auth.token:
                out.append(auth.token)
        return out

    def start_run(self, trigger: str, time_range: str) -> int:
        with self._lock:
            if self._active:
                raise Busy({"kind": self._active["kind"], "id": self._active["id"]})
            conn = db.connect(self.paths.db_path)
            log_rel = ""  # set after we know the id
            rid = repo.create_run(conn, trigger=trigger, time_range=time_range,
                                  log_path="pending", started_at=_now())
            log_rel = f"logs/run-{rid}.log"
            conn.execute("UPDATE runs SET log_path=? WHERE id=?", (log_rel, rid))
            conn.commit()
            cmd = [sys.executable, "-m", "solaranalysis.web.runner", "--run",
                   "--run-id", str(rid),
                   "--data-dir", self.paths.data_dir, "--app-dir", self.paths.app_dir]
            proc = self._spawn(cmd)
            try:
                repo.set_run_pid(conn, rid, proc.pid)
                conn.close()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                raise
            self._active = {"kind": "run", "id": rid, "proc": proc, "cancel": False}
            self._progress[rid] = {"plants": {}, "last_event": None, "status": "running"}
            t = threading.Thread(target=self._pump, args=(rid, proc), daemon=True)
            self._threads[rid] = t
            t.start()
            return rid

    # ---- pump ----------------------------------------------------------
    def _apply_event(self, run_id, ev, result):
        if not isinstance(ev, dict) or "event" not in ev:
            return
        prog = self._progress.setdefault(run_id, {"plants": {}, "last_event": None,
                                                  "status": "running"})
        prog["last_event"] = ev
        name = ev.get("plant")
        if ev["event"] == "plant_start" and name:
            prog["plants"][name] = "running"
        elif ev["event"] == "plant_done" and name:
            prog["plants"][name] = "ok" if ev.get("ok") else "failed"
        elif ev["event"] == "run_complete":
            result.update({k: ev.get(k) for k in
                           ("status", "report_path", "skipped",
                            "plants_summary", "notes", "error")})

    def _pump(self, run_id, proc):
        conn = db.connect(self.paths.db_path)
        result = {"status": None, "report_path": None, "skipped": None,
                  "plants_summary": None, "notes": None, "error": None}
        tail = []
        code = -1
        try:
            red = events.Redactor(self._secrets(conn))
            log_path = os.path.join(self.paths.data_dir, f"logs/run-{run_id}.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as log_fp:
                for raw in proc.stdout:
                    line = red.redact(raw.rstrip("\n"))
                    log_fp.write(line + "\n"); log_fp.flush()
                    tail.append(line)
                    del tail[:-50]
                    kind, val = events.parse_line(line)
                    self._broadcast(run_id, {"type": "log", "line": line})
                    if kind == "event":
                        self._apply_event(run_id, val, result)
                        self._broadcast(run_id, {"type": "progress", "event": val})
                code = proc.wait()
        except Exception as e:
            # A pump failure must still finalize the run as failed, not strand it.
            if result["error"] is None:
                result["error"] = ("\n".join(tail)[-500:] or f"pump error: {e}")
        finally:
            try:
                self._finish(run_id, result, code, "\n".join(tail)[-500:], conn)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            self._broadcast(run_id, {"type": "end"})
            with self._lock:
                if self._active and self._active["id"] == run_id:
                    self._active = None

    def _finish(self, run_id, result, code, tail, conn):
        with self._lock:
            cancelled = bool(self._active and self._active.get("cancel"))
        if cancelled:
            status = "cancelled"
        elif result["status"] in ("success", "partial") and result["report_path"]:
            status = result["status"]
        else:
            status = "failed"
        self._progress.get(run_id, {})["status"] = status
        repo.finalize_run(
            conn, run_id, status=status, finished_at=_now(),
            report_path=result["report_path"],
            plants_summary=result["plants_summary"],
            skipped_plants=result["skipped"], notes=result["notes"],
            error=result["error"] or (None if status != "failed" else tail))

    # ---- test helper: wait for a run's pump thread ---------------------
    def join(self, run_id, timeout=None):
        t = self._threads.get(run_id)
        if t:
            t.join(timeout)
