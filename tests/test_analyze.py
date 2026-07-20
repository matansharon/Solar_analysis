from solaranalysis.core.schema import PlantData, Metric, EnergyPoint, TimeRange
from solaranalysis.core.analyze import (build_data_block, pick_model, verify_numbers,
                                        run_analysis, summarize_executive)
from solaranalysis.config import AppConfig

def _plant(name, kwp, life):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(kwp, "kWp"),
                     energy_lifetime_kwh=Metric(life, "kWh"),
                     energy_timeseries=[EnergyPoint("2025-01-01", 10.0, "day"),
                                        EnergyPoint("2025-02-01", 20.0, "day")])

def _plant_no_series(name="A", kwp=100.0, life=5000.0):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(kwp, "kWp"),
                     energy_lifetime_kwh=Metric(life, "kWh"))

def test_pick_model_defaults_and_upgrade():
    cfg = AppConfig()
    with_series = [_plant("A", 100.0, 5000.0)]
    assert pick_model(cfg, TimeRange.SNAPSHOT, with_series) == "claude-sonnet-5"
    assert pick_model(cfg, TimeRange.LAST_12MO, with_series) == "claude-opus-4-8"
    assert pick_model(AppConfig(model="claude-haiku-4-5"), TimeRange.ALL, with_series) == "claude-haiku-4-5"

def test_pick_model_no_upgrade_without_series():
    # 12mo/all with snapshot-only data must not silently pay for Opus:
    # there is no historical series for it to analyze.
    cfg = AppConfig()
    assert pick_model(cfg, TimeRange.LAST_12MO, [_plant_no_series()]) == "claude-sonnet-5"
    assert pick_model(cfg, TimeRange.ALL, []) == "claude-sonnet-5"

def test_data_block_notes_missing_series_for_ranged_reports():
    block = build_data_block([_plant_no_series()], TimeRange.LAST_12MO, {"currency": None})
    assert "no historical time series" in block
    snap = build_data_block([_plant_no_series()], TimeRange.SNAPSHOT, {"currency": None})
    assert "no historical time series" not in snap

def test_summary_includes_savings_and_timestamps():
    p = _plant_no_series()
    p.savings = Metric(2500.0, "currency", is_derived=True)
    p.fetched_at_utc = "2026-07-03T10:00:00+00:00"
    p.reporting_timestamp_utc = "2026-07-03T09:55:00Z"
    block = build_data_block([p], TimeRange.SNAPSHOT, {"currency": "ILS"})
    assert '"savings": 2500.0' in block
    assert "2026-07-03T10:00:00+00:00" in block
    assert "2026-07-03T09:55:00Z" in block

def test_build_data_block_contains_plants_and_csv():
    block = build_data_block([_plant("A", 100.0, 5000.0)], TimeRange.LAST_12MO,
                             {"currency": "ILS"})
    assert "A" in block
    assert "5000" in block
    # CSV header for the monthly rollup present
    assert "period,energy_kwh" in block

def test_verify_numbers_flags_hallucination():
    block = "plant A energy 5000 kWh"
    missing = verify_numbers("Plant A produced 5000 kWh, saving 9999.", block)
    assert "9999" in missing
    assert "5000" not in missing

def test_verify_numbers_no_substring_false_negative():
    # 500 is a substring of the real 5000 but is NOT a real value -> must be flagged
    missing = verify_numbers("Plant A produced 500 kWh.", "plant A energy 5000 kWh")
    assert "500" in missing

def test_verify_numbers_ignores_percentages():
    missing = verify_numbers("efficiency up 27% this month.", "plant A energy 5000")
    assert missing == []

class _FakeMsg:
    content = [type("B", (), {"type": "text", "text": "## Production & Performance\nok"})()]

class _RecordingClient:
    def __init__(self):
        client = self
        class messages:
            @staticmethod
            def create(**kw):
                client.kwargs = kw
                return _FakeMsg()
        self.messages = messages

def test_run_analysis_uses_injected_client():
    out = run_analysis([_plant("A", 100.0, 5000.0)], TimeRange.SNAPSHOT,
                       AppConfig(), client=_RecordingClient())
    assert "Production & Performance" in out

def test_run_analysis_raises_when_over_max_input_tokens():
    import pytest
    cfg = AppConfig(max_input_tokens=10)  # absurdly small: even summaries can't fit
    with pytest.raises(ValueError, match="max_input_tokens"):
        run_analysis([_plant("A", 100.0, 5000.0)], TimeRange.SNAPSHOT,
                     cfg, client=_RecordingClient())

def test_run_analysis_drops_series_to_fit_token_budget():
    # Summaries fit the budget but the day-granularity CSV series does not:
    # the series must be omitted (with a note) rather than blowing the cap.
    p = _plant("A", 100.0, 5000.0)
    p.energy_timeseries = [EnergyPoint(f"2025-01-{(i % 28) + 1:02d}", 10.0, "day")
                           for i in range(560)]
    cfg = AppConfig(max_input_tokens=1000)
    client = _RecordingClient()
    run_analysis([p], TimeRange.LAST_30D, cfg, client=client)
    user_content = client.kwargs["messages"][0]["content"]
    assert "period,energy_kwh" not in user_content
    assert "series omitted" in user_content


class _HebrewMsg:
    content = [type("B", (), {"type": "text",
                              "text": "**סיכום:** התחנה המובילה תקינה."})()]


class _HebrewClient:
    def __init__(self):
        client = self
        class messages:
            @staticmethod
            def create(**kw):
                client.kwargs = kw
                return _HebrewMsg()
        self.messages = messages


def test_summarize_executive_uses_injected_client():
    out = summarize_executive("## Production & Performance\nPlant A leads.",
                              client=_HebrewClient())
    assert out == "**סיכום:** התחנה המובילה תקינה."


class _TextClient:
    def __init__(self, text):
        client = self

        class messages:
            @staticmethod
            def create(**kw):
                client.kwargs = kw
                return type("M", (), {"content": [
                    type("B", (), {"type": "text", "text": text})()]})()
        self.messages = messages


def test_summarize_executive_inserts_blank_line_before_lists():
    # Models often emit "**header:**\n- item" with no blank line before the
    # list; python-markdown then renders the bullets as literal " - " text
    # inside one run-on <p>. The summary must come back normalized.
    raw = "**תקלות:**\n- אחת\n- שתיים\n\nפסקה."
    out = summarize_executive("report", client=_TextClient(raw))
    assert "**תקלות:**\n\n- אחת\n- שתיים" in out


def test_summarize_executive_leaves_wellformed_lists_alone():
    raw = "פסקה.\n\n- אחת\n- שתיים"
    out = summarize_executive("report", client=_TextClient(raw))
    assert out == raw


def test_summarize_executive_request_shape():
    report_md = "## Production & Performance\nPlant A produced 5000 kWh."
    client = _HebrewClient()
    summarize_executive(report_md, client=client)
    kw = client.kwargs
    # Opus 4.8 at "xhigh" reasoning = effort xhigh + adaptive thinking.
    assert kw["model"] == "claude-opus-4-8"
    assert kw["output_config"] == {"effort": "xhigh"}
    assert kw["thinking"] == {"type": "adaptive"}
    # The report being summarized is handed to the model.
    assert report_md in kw["messages"][0]["content"]
    # Sampling params 400 on Opus 4.8 — they must not be sent.
    assert "temperature" not in kw and "top_p" not in kw and "top_k" not in kw


from solaranalysis.core.analyze import status_overview


def test_status_overview_uses_injected_client():
    out = status_overview("## Health & Faults\nPlant A: inverter fault.",
                          client=_HebrewClient())
    assert out == "**סיכום:** התחנה המובילה תקינה."


def test_status_overview_reuses_list_break_normalization():
    # A headline line immediately followed by bullets must be separated by a
    # blank line, exactly like the executive summary (shared _ensure_list_breaks).
    raw = "סטטוס כללי: 2 מערכות\n- ✅ א' — תקין\n- ❌ ב' — תקלה"
    out = status_overview("report", client=_TextClient(raw))
    assert "סטטוס כללי: 2 מערכות\n\n- ✅ א' — תקין" in out


def test_status_overview_request_shape():
    report_md = "## Health & Faults\nPlant A inverter offline."
    client = _HebrewClient()
    status_overview(report_md, client=client)
    kw = client.kwargs
    assert kw["model"] == "claude-opus-4-8"
    assert kw["output_config"] == {"effort": "xhigh"}
    assert kw["thinking"] == {"type": "adaptive"}
    # The report being judged is handed to the model.
    assert report_md in kw["messages"][0]["content"]
    # Sampling params 400 on Opus 4.8 — they must not be sent.
    assert "temperature" not in kw and "top_p" not in kw and "top_k" not in kw
