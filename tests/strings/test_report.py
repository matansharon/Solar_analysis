from solaranalysis.strings.analyze import StringAnomaly
from solaranalysis.strings import report


def _anom(label="PV3", sev="underperforming", scope="mppt", reason="reason",
          metric=0.176, baseline=0.2, kwh=52.8, share=0.176):
    return StringAnomaly("MZHRF6K002", scope, label, sev, reason, "2026-07-27",
                         metric, baseline, kwh, share)


def _pair(label="strings 9+10", metric=34.1, baseline=18.2):
    return StringAnomaly("MZHRF6K002", "pair", label, "imbalance",
                         "current imbalance off its own median", "2026-07-27",
                         metric, baseline)


def test_build_anomaly_block_contains_grounded_figures():
    analyses = {"MZHRF6K002": [_anom(), _pair()], "OTHER": []}
    block = report.build_anomaly_block(analyses, "2026-07-27",
                                       device_names={"MZHRF6K002": "MAX 70KTL3 LV"})
    assert "MAX 70KTL3 LV" in block and "MZHRF6K002" in block
    assert "2026-07-27" in block
    assert "PV3" in block and "strings 9+10" in block
    assert "52.8" in block and "0.176" in block        # energy and share
    assert "34.1" in block and "18.2" in block         # pair metric + baseline
    assert "flagged=2" in block and "flagged=0" in block


def test_block_states_the_own_history_framing_the_narrator_must_respect():
    block = report.build_anomaly_block({"SN": [_anom()]}, "2026-07-27")
    assert "OWN trailing history" in block
    assert "peer comparison is meaningless" in block


def test_block_omits_fields_a_finding_does_not_carry():
    block = report.build_anomaly_block({"SN": [_pair()]}, "2026-07-27")
    assert "energy=" not in block and "share_of_total=" not in block
    assert "measured=" in block and "own_baseline=" in block


class FakeClient:
    """Minimal fake mirroring anthropic.Anthropic()'s client.messages.create."""
    last_kwargs = None

    class messages:
        @staticmethod
        def create(**kwargs):
            FakeClient.last_kwargs = kwargs
            block = type("Block", (), {"type": "text",
                                       "text": "Check strings 9 and 10."})()
            return type("Msg", (), {"content": [block]})()


def test_narrate_uses_client_and_returns_text():
    out = report.narrate("=== DATA ===\n1 flagged", lang="English",
                         client=FakeClient())
    assert out.strip() == "Check strings 9 and 10."
    assert FakeClient.last_kwargs["model"] == "claude-opus-4-8"


def test_narrate_passes_the_language_through():
    report.narrate("=== DATA ===", lang="Hebrew", client=FakeClient())
    assert "Hebrew" in FakeClient.last_kwargs["messages"][0]["content"]


def test_render_report_md_has_narrative_and_tables():
    analyses = {"MZHRF6K002": [_anom(), _pair()], "OTHER": []}
    md = report.render_report_md(analyses, "NARRATIVE HERE", "2026-07-27",
                                 device_names={"MZHRF6K002": "MAX 70KTL3 LV"})
    assert "Growatt Strings" in md and "2026-07-27" in md
    assert "NARRATIVE HERE" in md
    assert "| Severity |" in md
    assert "PV3" in md and "strings 9+10" in md
    assert "MAX 70KTL3 LV" in md
    assert "all clear" in md.lower()          # the second inverter


def test_render_report_md_without_narrative():
    md = report.render_report_md({"SN": [_anom(sev="watch")]}, None, "2026-07-27")
    assert "Growatt Strings" in md and "watch" in md


def test_table_leaves_absent_figures_blank_rather_than_printing_none():
    md = report.render_report_md({"SN": [_pair()]}, None, "2026-07-27")
    assert "None" not in md


def test_subject_counts_flagged():
    assert report.subject("2026-07-27", 3) == "Growatt Strings · 3 flagged · 2026-07-27"


def test_resolve_recipients_prefers_string_env(monkeypatch):
    monkeypatch.setenv("STRING_RECIPIENTS", "a@x.com, b@x.com, a@x.com")
    assert report.resolve_recipients() == ["a@x.com", "b@x.com"]


def test_resolve_recipients_falls_back_to_report_recipients(monkeypatch):
    monkeypatch.delenv("STRING_RECIPIENTS", raising=False)
    monkeypatch.setenv("REPORT_RECIPIENTS", "c@x.com")
    assert report.resolve_recipients() == ["c@x.com"]


def test_string_recipients_is_independent_of_the_optimizer_override(monkeypatch):
    monkeypatch.delenv("STRING_RECIPIENTS", raising=False)
    monkeypatch.setenv("OPTIMIZER_RECIPIENTS", "opt@x.com")
    monkeypatch.setenv("REPORT_RECIPIENTS", "c@x.com")
    assert report.resolve_recipients() == ["c@x.com"]
