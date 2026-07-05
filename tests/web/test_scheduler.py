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


def test_build_jobs_skips_malformed_row(tmp_path):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    # Seed a malformed row directly (bypassing route-level validation) to
    # simulate data that got into the DB some other way.
    repo.create_schedule(conn, {"time_of_day": "nope", "days_of_week": "0,4",
                                "time_range": "30d", "enabled": True})
    conn.close()
    svc = scheduler.ScheduleService(paths, FakeRM(), scheduler=object())
    jobs = svc.build_jobs()  # must not raise
    # Only the one valid, enabled row from _paths() should survive.
    assert len(jobs) == 1
    assert jobs[0]["hour"] == 6 and jobs[0]["minute"] == 30
