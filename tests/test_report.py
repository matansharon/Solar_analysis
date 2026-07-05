from pathlib import Path
from solaranalysis.core.report import render_html, write_report

def test_append_unavailable_section_escapes_and_skips_when_empty():
    from solaranalysis.core.report import append_unavailable_section
    assert append_unavailable_section("# R", []) == "# R"
    out = append_unavailable_section("# R", [
        {"name": "Bad <b>P</b>", "reason": "boom <script>x</script>"}])
    assert "## Unavailable Plants" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out

def test_render_html_is_self_contained():
    html = render_html("## Production & Performance\n\nPlant A leads.", "Solar Report", "12mo")
    assert "<style>" in html                 # inline CSS
    assert "http://" not in html and "https://" not in html  # no external assets
    assert "Production &amp; Performance" in html or "Production & Performance" in html
    assert "Solar Report" in html

def test_write_report_creates_file(tmp_path):
    html = render_html("## Health & Faults\n\nAll nominal.", "T", "snapshot")
    path = write_report(html, str(tmp_path))
    assert Path(path).exists()
    assert Path(path).name == "report.html"
    assert "Health" in Path(path).read_text(encoding="utf-8")
