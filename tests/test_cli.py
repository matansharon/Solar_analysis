import solaranalysis.cli as cli
from solaranalysis.config import AppConfig
from solaranalysis.core.schema import PlantData


def _pd(name):
    return PlantData(plant_id=name, source_platform="growatt",
                     source_plant_id="1", plant_name=name)


def _fake_pipeline_result(skipped):
    return {"report_md": "# Report", "plants": [_pd("Good")],
            "verify_missing": [], "skipped_plants": skipped}


def _run(tmp_path, monkeypatch, skipped, extra_args=()):
    monkeypatch.setattr(cli, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda cfg, tr, ss: _fake_pipeline_result(skipped))
    out = tmp_path / "out"
    rc = cli.main(["--config", "ignored.yaml", "--out", str(out),
                   "--cache-dir", str(tmp_path / "cache"), *extra_args])
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
