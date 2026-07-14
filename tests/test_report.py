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


from solaranalysis.core.report import render_email_html


def test_render_email_html_has_no_css_variables():
    html = render_email_html("# Title\n\nSome text.", "Solar Fleet Analysis", "3 plants")
    assert "var(" not in html
    assert ":root" not in html


def test_render_email_html_inlines_table_styles():
    md_table = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = render_email_html(md_table, "T", "S")
    assert "<table style=" in html
    assert "<th style=" in html
    assert "<td style=" in html


def test_render_email_html_includes_title_subtitle_body():
    html = render_email_html("**bold** words", "My Title", "my subtitle")
    assert "My Title" in html
    assert "my subtitle" in html
    assert "<strong>bold</strong>" in html


def test_render_email_html_light_theme_and_inlined_paragraph():
    html = render_email_html("plain paragraph", "T", "S")
    assert "#f4f6f8" in html      # light page background
    assert "<p style=" in html    # paragraph styled inline


from solaranalysis.core.report import prepend_summary


def test_prepend_summary_places_summary_first_rtl():
    out = prepend_summary("## Production & Performance\n\nMain body.",
                          "**סיכום:** הכל תקין.")
    assert 'dir="rtl"' in out
    assert "סיכום מנהלים" in out
    # The summary and its content precede the detailed report body.
    assert out.index("סיכום מנהלים") < out.index("Production & Performance")
    assert out.index("הכל תקין") < out.index("Main body")


def test_render_html_renders_rtl_summary():
    doc = prepend_summary("## Production & Performance\n\nMain body.",
                          "- שורה ראשונה")
    html = render_html(doc, "T", "S")
    # RTL wrapper is preserved and its inner markdown is rendered (md_in_html on).
    assert 'dir="rtl"' in html
    assert "<h2" in html and "סיכום מנהלים" in html
    assert "<li>שורה ראשונה</li>" in html


def test_render_email_html_renders_rtl_summary():
    doc = prepend_summary("## Production & Performance\n\nMain body.",
                          "טקסט בעברית")
    html = render_email_html(doc, "T", "S")
    assert 'dir="rtl"' in html
    assert "טקסט בעברית" in html
