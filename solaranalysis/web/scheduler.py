from __future__ import annotations
import logging

from . import db, repo
from .paths import Paths
from .run_manager import Busy

log = logging.getLogger("solar.scheduler")


class ScheduleService:
    def __init__(self, paths: Paths, run_manager, scheduler=None):
        self.paths = paths
        self.rm = run_manager
        self._sched = scheduler  # APScheduler instance; injected/lazy

    def build_jobs(self) -> list[dict]:
        conn = db.connect(self.paths.db_path)
        jobs = []
        for s in repo.list_schedules(conn):
            if not s["enabled"]:
                continue
            hh, mm = s["time_of_day"].split(":")
            jobs.append({"id": s["id"], "day_of_week": s["days_of_week"],
                         "hour": int(hh), "minute": int(mm),
                         "time_range": s["time_range"]})
        conn.close()
        return jobs

    def fire(self, time_range: str) -> None:
        try:
            self.rm.start_run("scheduled", time_range)
        except Busy:
            log.info("scheduled run (%s) skipped: an operation is active", time_range)

    def _ensure_sched(self):
        if self._sched is None:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._sched = BackgroundScheduler()
        return self._sched

    def reload(self) -> None:
        sched = self._ensure_sched()
        for job in list(sched.get_jobs()):
            job.remove()
        for spec in self.build_jobs():
            sched.add_job(self.fire, "cron", args=[spec["time_range"]],
                          day_of_week=spec["day_of_week"], hour=spec["hour"],
                          minute=spec["minute"], id=f"sched-{spec['id']}",
                          misfire_grace_time=300, coalesce=True)

    def start(self) -> None:
        sched = self._ensure_sched()
        self.reload()
        if not sched.running:
            sched.start()

    def shutdown(self) -> None:
        if self._sched and self._sched.running:
            self._sched.shutdown(wait=False)
