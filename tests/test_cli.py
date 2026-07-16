import pytest

import solaranalysis.cli as cli
from solaranalysis.config import AppConfig
from solaranalysis.core.schema import PlantData


@pytest.fixture(autouse=True)
def _stub_llm_calls(monkeypatch):
    # Never hit the network for the LLM calls in CLI tests (render_charts is
    # pure Python and stays real).
    monkeypatch.setattr(cli, "summarize_executive",
                        lambda report_md: "**סיכום בדיקה**", raising=False)
    monkeypatch.setattr(cli, "design_charts", lambda data_summary: [], raising=False)
    monkeypatch.setattr(cli, "compose_dashboard",
                        lambda summary_md, charts_html, **kw: "<html>dash</html>",
                        raising=False)


def _pd(name):
    return PlantData(plant_id=name, source_platform="growatt",
                     source_plant_id="1", plant_name=name)


def _fake_pipeline_result(skipped):
    return {"report_md": "# Report", "plants": [_pd("Good")],
            "verify_missing": [], "skipped_plants": skipped}


def _run(tmp_path, monkeypatch, skipped, extra_args=()):
    monkeypatch.setattr(cli, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda cfg, tr, ss, **kw: _fake_pipeline_result(skipped))
    out = tmp_path / "out"
    rc = cli.main(["--config", "ignored.yaml", "--out", str(out),
                   "--cache-dir", str(tmp_path / "cache"), "--no-persist",
                   *extra_args])
    assert rc == 0
    return (out / "report.html").read_text(encoding="utf-8")


def test_cli_renders_unavailable_plants_section(tmp_path, monkeypatch):
    html = _run(tmp_path, monkeypatch,
                [{"name": "Bad", "reason": "auth failed"}])
    assert "Unavailable Plants" in html
    assert "auth failed" in html


def test_cli_escapes_portal_controlled_text_in_report(tmp_path, monkeypatch):
    # Exception text can carry portal-controlled content; it must not become
    # live HTML in the report.
    html = _run(tmp_path, monkeypatch,
                [{"name": "Bad <b>Plant</b>",
                  "reason": "boom <script>alert(1)</script>"}])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_cli_warns_when_ranged_report_has_no_series(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, [], extra_args=("--range", "12mo"))
    err = capsys.readouterr().err
    assert "counters" in err  # no historical series -> counters-only note


def test_cli_prepends_hebrew_executive_summary(tmp_path, monkeypatch):
    html = _run(tmp_path, monkeypatch, [])
    assert "סיכום מנהלים" in html        # the summary heading
    assert "סיכום בדיקה" in html         # the stubbed summary body
    # Summary appears above the detailed report.
    assert html.index("סיכום מנהלים") < html.index("Report")


def test_cli_summary_failure_is_nonfatal(tmp_path, monkeypatch):
    def boom(report_md):
        raise RuntimeError("opus down")
    monkeypatch.setattr(cli, "summarize_executive", boom)
    html = _run(tmp_path, monkeypatch, [])   # still returns 0 and writes report
    assert "סיכום מנהלים" not in html         # summary skipped, not fatal
    assert "Report" in html


def test_cli_writes_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda cfg, tr, ss, **kw: _fake_pipeline_result([]))
    monkeypatch.setattr(cli, "compose_dashboard",
                        lambda summary_md, charts_html, **kw: "<html>DASH-MARKER</html>")
    out = tmp_path / "out"
    rc = cli.main(["--config", "x", "--out", str(out),
                   "--cache-dir", str(tmp_path / "c"), "--no-persist"])
    assert rc == 0
    dash = out / "dashboard.html"
    assert dash.exists()
    assert "DASH-MARKER" in dash.read_text(encoding="utf-8")


def test_cli_dashboard_failure_is_nonfatal(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda cfg, tr, ss, **kw: _fake_pipeline_result([]))
    def boom(data_summary):
        raise RuntimeError("charts down")
    monkeypatch.setattr(cli, "design_charts", boom)
    out = tmp_path / "out"
    rc = cli.main(["--config", "x", "--out", str(out),
                   "--cache-dir", str(tmp_path / "c"), "--no-persist"])
    assert rc == 0                                   # non-fatal
    assert (out / "report.html").exists()            # report still written
    assert not (out / "dashboard.html").exists()     # dashboard skipped
