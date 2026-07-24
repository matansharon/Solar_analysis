from solaranalysis.optimizers.analyze import OptimizerAnomaly
from solaranalysis.optimizers import report


def _anom(serial, sev, ratio=0.5):
    return OptimizerAnomaly(1, serial, f"1.4.{serial[-1]}", "1.4", sev,
                            "reason", "2026-07-22", 2500.0, 0.55, 5000.0, ratio)


def test_build_anomaly_block_contains_grounded_figures():
    analyses = {1: [_anom("A", "dead"), _anom("B", "underperforming")], 2: []}
    block = report.build_anomaly_block(analyses, site_names={1: "Baram", 2: "Golan"})
    assert "Baram" in block and "Golan" in block
    assert "dead" in block and "underperforming" in block
    assert "136" not in block  # (no fake serials) — our serials are A/B here
    assert "A" in block and "2500" in block  # energy figure present


class FakeClient:
    """Minimal fake mirroring anthropic.Anthropic()'s client.messages.create."""
    last_kwargs = None

    class messages:
        @staticmethod
        def create(**kwargs):
            FakeClient.last_kwargs = kwargs
            block = type("Block", (), {"type": "text", "text": "Two optimizers need attention."})()
            return type("Msg", (), {"content": [block]})()


def test_narrate_uses_client_and_returns_text():
    out = report.narrate("=== DATA ===\nsite 1: 2 flagged", lang="English",
                         client=FakeClient())
    assert out.strip() == "Two optimizers need attention."
    assert FakeClient.last_kwargs["model"] == "claude-opus-4-8"


def test_narrate_empty_when_nothing_flagged_block():
    # A block with zero flagged still returns whatever the client says; the
    # caller decides whether to include it. Just verify no crash on empty-ish input.
    out = report.narrate("=== DATA ===\nno anomalies", lang="Hebrew", client=FakeClient())
    assert isinstance(out, str)


def test_render_report_md_has_narrative_and_tables():
    analyses = {1: [_anom("A", "dead")], 2: []}
    md = report.render_report_md(analyses, "NARRATIVE HERE", "2026-07-22",
                                 site_names={1: "Baram", 2: "Golan"})
    assert "SolarEdge Optimizers" in md and "2026-07-22" in md
    assert "NARRATIVE HERE" in md
    assert "| Severity |" in md            # a markdown table header
    assert "dead" in md and "Baram" in md
    assert "Golan" in md and "all clear" in md.lower()  # empty site


def test_render_report_md_without_narrative():
    md = report.render_report_md({1: [_anom("A", "watch")]}, None, "2026-07-22")
    assert "SolarEdge Optimizers" in md
    assert "watch" in md


def test_subject_counts_flagged():
    assert report.subject("2026-07-22", 3) == "SolarEdge Optimizers · 3 flagged · 2026-07-22"


def test_resolve_recipients_prefers_optimizer_env(monkeypatch):
    monkeypatch.setenv("OPTIMIZER_RECIPIENTS", "a@x.com, b@x.com")
    assert report.resolve_recipients() == ["a@x.com", "b@x.com"]


def test_resolve_recipients_falls_back_to_report_recipients(monkeypatch):
    monkeypatch.delenv("OPTIMIZER_RECIPIENTS", raising=False)
    monkeypatch.setenv("REPORT_RECIPIENTS", "c@x.com")
    assert report.resolve_recipients() == ["c@x.com"]
