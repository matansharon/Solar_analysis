from solaranalysis.web import db, repo, scheduler
from solaranalysis.web.paths import Paths
from solaranalysis.web.run_manager import Busy


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn)
    repo.create_schedule(conn, {"time_of_day": "06:30", "days_of_week": "0,4",
                                "time_range": "30d", "enabled": True})
    repo.create_schedule(conn, {"time_of_day": "23:00", "days_of_week": "1",
                                "time_range": "all", "enabled": False})
    conn.close()
    return p


class FakeRM:
    def __init__(self, busy=False):
        self.calls = []
        self._busy = busy
    def start_run(self, trigger, time_range):
        if self._busy:
            raise Busy({"kind": "run", "id": 1})
        self.calls.append((trigger, time_range))
        return 7


def test_build_jobs_only_enabled(tmp_path):
    paths = _paths(tmp_path)
    svc = scheduler.ScheduleService(paths, FakeRM(), scheduler=object())
    jobs = svc.build_jobs()
    assert len(jobs) == 1
    j = jobs[0]
    assert j["hour"] == 6 and j["minute"] == 30
    assert j["day_of_week"] == "0,4" and j["time_range"] == "30d"


def test_fire_starts_run(tmp_path):
    paths = _paths(tmp_path)
    rm = FakeRM()
    svc = scheduler.ScheduleService(paths, rm, scheduler=object())
    svc.fire("30d")
    assert rm.calls == [("scheduled", "30d")]


def test_fire_skips_when_busy(tmp_path):
    paths = _paths(tmp_path)
    rm = FakeRM(busy=True)
    svc = scheduler.ScheduleService(paths, rm, scheduler=object())
    svc.fire("30d")  # must not raise
    assert rm.calls == []
