import json
from solaranalysis.web import db, repo, crypto, runner
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    return Paths.create(str(tmp_path / "data"), str(app))


def _seed(paths):
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    key = crypto.load_or_create_key(paths.key_path)
    repo.create_plant(conn, key, {"name": "Good", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "pw", "tariff_per_kwh": 0.5})
    return conn, key


def test_build_app_config_from_db(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, names = runner.build_app_config(conn, key)
    assert len(cfg.plants) == 1
    assert cfg.plants[0].auth.username == "u"
    assert cfg.plants[0].auth.password == "pw"
    assert cfg.max_input_tokens == 60000


def test_build_app_config_sets_config_id(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    pid = repo.list_plants(conn)[0]["id"]
    cfg, _ = runner.build_app_config(conn, key)
    assert cfg.plants[0].config_id == pid


def test_collect_secrets(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, _ = runner.build_app_config(conn, key)
    assert "pw" in runner.collect_secrets(cfg)


def test_run_job_emits_events_and_writes_report(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    # The RunManager creates the run row (with its time_range) before spawning
    # the runner; mirror that so run_analysis_job can read run_id=1.
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()

    # Stub the pipeline so no browser/network is touched; drive progress + result.
    from solaranalysis.core.schema import PlantData
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
        progress({"event": "plant_start", "plant": "Good"})
        progress({"event": "plant_done", "plant": "Good", "ok": True})
        progress({"event": "analyze_start"})
        return {"report_md": "# Report", "plants": [PlantData(
                    plant_id="g", source_platform="growatt",
                    source_plant_id="1", plant_name="Good")],
                "verify_missing": ["123"], "skipped_plants": []}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)

    rc = runner.run_analysis_job(paths, run_id=1)
    assert rc == 0
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    kinds = [e["event"] for e in events]
    assert "run_start" in kinds and "report_written" in kinds
    complete = [e for e in events if e["event"] == "run_complete"][0]
    assert complete["status"] == "success"
    assert complete["notes"]["verify_missing_count"] == 1


def test_run_job_partial_when_skipped(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "boom"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "partial"


def test_run_job_redacts_secret_in_events(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
        progress({"event": "plant_done", "plant": "Good", "ok": False,
                  "reason": "auth failed for pw"})
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "auth failed for pw"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert "pw" not in out.replace("plant", "").replace("Good", "")
    assert "***" in out


def test_test_job_reports_result(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    pid = repo.list_plants(conn)[0]["id"]; conn.close()

    class FakeAdapter:
        def verify_login(self): return None
    monkeypatch.setattr(runner, "get_adapter", lambda auth, ss: FakeAdapter())
    rc = runner.run_test_job(paths, plant_id=pid)
    assert rc == 0
    out = capsys.readouterr().out
    res = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
           if "test_result" in l][0]
    assert res["ok"] is True
