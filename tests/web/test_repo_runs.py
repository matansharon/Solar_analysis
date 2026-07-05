from solaranalysis.web import db, repo


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_schedule_crud(tmp_path):
    c = _conn(tmp_path)
    sid = repo.create_schedule(c, {"time_of_day": "06:00",
                                   "days_of_week": "0,1,2,3,4",
                                   "time_range": "30d", "enabled": True})
    assert len(repo.list_schedules(c)) == 1
    repo.update_schedule(c, sid, {"enabled": False})
    assert repo.list_schedules(c)[0]["enabled"] is False
    repo.delete_schedule(c, sid)
    assert repo.list_schedules(c) == []


def test_run_lifecycle(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    repo.set_run_pid(c, rid, 4321)
    assert repo.get_run(c, rid)["status"] == "running"
    assert repo.get_run(c, rid)["runner_pid"] == 4321
    assert [r["id"] for r in repo.running_runs(c)] == [rid]
    repo.finalize_run(c, rid, status="partial", finished_at="2026-07-04T00:01:00",
                      report_path="output/x/report.html",
                      plants_summary=[{"name": "A", "ok": True}],
                      skipped_plants=[{"name": "B", "reason": "boom"}],
                      notes={"verify_missing_count": 1}, error=None)
    r = repo.get_run(c, rid)
    assert r["status"] == "partial"
    assert r["plants_summary"] == [{"name": "A", "ok": True}]
    assert r["skipped_plants"][0]["reason"] == "boom"
    assert r["notes"]["verify_missing_count"] == 1
    assert repo.running_runs(c) == []


def test_mark_interrupted(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="scheduled", time_range="all",
                          log_path="logs/run-2.log", started_at="2026-07-04T00:00:00")
    repo.mark_interrupted(c, rid, finished_at="2026-07-04T00:05:00")
    assert repo.get_run(c, rid)["status"] == "interrupted"
