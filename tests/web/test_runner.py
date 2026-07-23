import json
import pytest
from solaranalysis.web import db, repo, crypto, runner
from solaranalysis.web.paths import Paths


@pytest.fixture(autouse=True)
def _stub_llm_calls(monkeypatch):
    # Never hit the network for LLM calls in runner tests (render_charts is
    # pure Python and stays real).
    monkeypatch.setattr(runner, "summarize_executive",
                        lambda report_md: "**סיכום בדיקה**", raising=False)
    monkeypatch.setattr(runner, "status_overview",
                        lambda report_md: "- ✅ **Good** — תקין", raising=False)
    monkeypatch.setattr(runner, "design_charts", lambda data_summary: [], raising=False)
    monkeypatch.setattr(runner, "compose_dashboard",
                        lambda summary_md, charts_html, **kw:
                        '<html><body style="margin:0">dash</body></html>',
                        raising=False)


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
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
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
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
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
    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
        progress({"event": "plant_done", "plant": "Good", "ok": False,
                  "reason": "auth failed for pw"})
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "auth failed for pw"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert "pw" not in out.replace("plant", "").replace("Good", "")
    assert "***" in out


def _seed_two(paths):
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    key = crypto.load_or_create_key(paths.key_path)
    repo.create_plant(conn, key, {"name": "Alpha", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "pw"})
    repo.create_plant(conn, key, {"name": "Beta", "platform": "growatt",
                                  "auth_mode": "password", "username": "u2",
                                  "password": "pw2"})
    return conn, key


def test_build_app_config_filters_to_plant_id(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    target = next(p for p in repo.list_plants(conn) if p["name"] == "Beta")
    cfg, names = runner.build_app_config(conn, key, plant_id=target["id"])
    assert [p.name for p in cfg.plants] == ["Beta"]
    assert names == {target["id"]: "Beta"}


def test_build_app_config_none_is_all_enabled(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    cfg, _ = runner.build_app_config(conn, key)
    assert {p.name for p in cfg.plants} == {"Alpha", "Beta"}


def test_run_job_scopes_pipeline_to_target(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed_two(paths)
    beta = next(p for p in repo.list_plants(conn) if p["name"] == "Beta")
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-23T00:00:00",
                    plant_id=beta["id"])
    conn.close()

    seen = {}
    from solaranalysis.core.schema import PlantData

    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
        seen["plants"] = [p.name for p in cfg.plants]
        return {"report_md": "# R", "plants": [PlantData(
                    plant_id="b", source_platform="growatt",
                    source_plant_id="1", plant_name="Beta")],
                "verify_missing": [], "skipped_plants": []}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)

    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))

    runner.run_analysis_job(paths, run_id=1)
    assert seen["plants"] == ["Beta"]              # pipeline saw only the target
    assert "Beta" in sent[0]                       # subject names the system
    assert "1 plants" not in sent[0]


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


def _seed_run(paths):
    conn, key = _seed(paths)
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()


def _success_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
    from solaranalysis.core.schema import PlantData
    return {"report_md": "# R", "plants": [PlantData(
                plant_id="g", source_platform="growatt",
                source_plant_id="1", plant_name="Good")],
            "verify_missing": [], "skipped_plants": []}


def test_run_job_emails_on_success(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append((subject, html)))
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    kinds = [json.loads(l[len("@@EVENT@@ "):])["event"]
             for l in out.splitlines() if l.startswith("@@EVENT@@ ")]
    assert "report_emailed" in kinds
    assert len(sent) == 1
    assert sent[0][0].startswith("Solar Fleet Analysis")


def test_run_job_emails_on_partial(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)

    def partial_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "boom"}]}

    monkeypatch.setattr(runner, "run_pipeline", partial_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "partial"
    assert len(sent) == 1 and "partial" in sent[0]


def test_run_job_skips_email_when_unconfigured(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: False)
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))
    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == []
    assert "email not configured" in out


def test_run_job_email_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(subject, html):
        raise RuntimeError("graph down")

    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report", boom)
    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "success"
    assert "email send failed" in out


def test_run_job_emails_email_safe_body(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(html))
    runner.run_analysis_job(paths, run_id=1)
    assert len(sent) == 1
    assert "var(" not in sent[0]     # CSS custom properties not used in email body
    assert "style=" in sent[0]       # styles are inlined


def test_collect_secrets_includes_graph_secret(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, _ = runner.build_app_config(conn, key)
    conn.close()
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "graphsecret")
    assert "graphsecret" in runner.collect_secrets(cfg)


def test_graph_is_unconfigured_in_tests():
    # Regression: the test environment must never expose real Graph creds. The
    # runner tests that don't stub the mailer would otherwise send REAL email
    # (fixture content) to the real recipient whenever a developer's shell has
    # GRAPH_* set. A conftest fixture scrubs them for every test.
    from solaranalysis.web import mailer
    assert mailer.is_configured() is False
    assert mailer.recipients() == []


def test_run_job_notes_executive_summary(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    notes = [e.get("reason", "") for e in events if e["event"] == "note"]
    assert any("executive summary" in r for r in notes)
    complete = [e for e in events if e["event"] == "run_complete"][0]
    assert complete["status"] == "success"


def test_run_job_summary_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(report_md):
        raise RuntimeError("opus down")
    monkeypatch.setattr(runner, "summarize_executive", boom)

    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "success"
    assert "executive summary skipped" in out


def test_run_job_writes_and_emails_dashboard(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    monkeypatch.setattr(runner, "design_charts",
                        lambda data_summary: [{"metric": "energy_today",
                                               "title": "E", "insight": "i"}])
    monkeypatch.setattr(runner, "compose_dashboard",
                        lambda summary_md, charts_html, **kw:
                        '<html><body style="margin:0">DASH-MARKER</body></html>')
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(html))
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    kinds = [e["event"] for e in events]
    assert "dashboard_written" in kinds
    emailed = [e for e in events if e["event"] == "report_emailed"][0]
    assert emailed["body"] == "dashboard"
    assert len(sent) == 1 and "DASH-MARKER" in sent[0]


def test_run_job_dashboard_failure_falls_back_to_report_email(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(data_summary):
        raise RuntimeError("charts down")
    monkeypatch.setattr(runner, "design_charts", boom)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(html))
    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "dashboard skipped" in out
    emailed = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
               if "report_emailed" in l][0]
    assert emailed["body"] == "report"            # fell back to detailed report
    assert len(sent) == 1


def test_run_job_notes_status_overview(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    notes = [e.get("reason", "") for e in events if e["event"] == "note"]
    assert any("status overview" in r for r in notes)
    complete = [e for e in events if e["event"] == "run_complete"][0]
    assert complete["status"] == "success"


def test_run_job_status_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(report_md):
        raise RuntimeError("opus down")
    monkeypatch.setattr(runner, "status_overview", boom)

    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "success"
    assert "status overview skipped" in out


def test_run_job_passes_status_to_dashboard(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    monkeypatch.setattr(runner, "design_charts",
                        lambda data_summary: [{"metric": "energy_today",
                                               "title": "E", "insight": "i"}])
    seen = {}
    monkeypatch.setattr(runner, "compose_dashboard",
                        lambda summary_md, charts_html, **kw:
                        (seen.update(kw), "<html><body>D</body></html>")[1])
    runner.run_analysis_job(paths, run_id=1)
    assert seen.get("status_md") == "- ✅ **Good** — תקין"


def test_run_job_status_sees_appendix_summary_sees_clean(tmp_path, monkeypatch, capsys):
    # Invariant: status_overview must receive base_md (report + "Unavailable
    # Plants" appendix) so unfetched/skipped systems surface as a problem,
    # while summarize_executive must receive the clean report (no appendix).
    paths = _paths(tmp_path)
    _seed_run(paths)

    from solaranalysis.core.schema import PlantData

    def fake_pipeline(cfg, tr, ss, progress=None, on_fetched=None, record_raw=False):
        return {"report_md": "# R", "plants": [PlantData(
                    plant_id="g", source_platform="growatt",
                    source_plant_id="1", plant_name="Good")],
                "verify_missing": [], "skipped_plants": [{"name": "Bad", "reason": "boom"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)

    seen = {}

    def fake_summarize(report_md):
        seen["summary_input"] = report_md
        return "**summary**"

    def fake_status(report_md):
        seen["status_input"] = report_md
        return "- ✅ **Good**"

    monkeypatch.setattr(runner, "summarize_executive", fake_summarize)
    monkeypatch.setattr(runner, "status_overview", fake_status)

    runner.run_analysis_job(paths, run_id=1)

    assert "Unavailable Plants" in seen["status_input"]
    assert "Unavailable Plants" not in seen["summary_input"]
