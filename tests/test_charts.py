from solaranalysis.core.charts import design_charts, render_charts, CHART_METRICS
from solaranalysis.core.schema import PlantData, Metric


def _plant(name, kwp, today, month=None, co2=None):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(kwp, "kWp"),
                     energy_today_kwh=Metric(today, "kWh"),
                     energy_month_kwh=Metric(month, "kWh"),
                     co2_avoided_kg=Metric(co2, "kg"))


class _JsonMsg:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]


class _FakeClient:
    def __init__(self, text):
        self._text = text
        client = self
        class messages:
            @staticmethod
            def create(**kw):
                client.kwargs = kw
                return _JsonMsg(client._text)
        self.messages = messages


def test_design_charts_parses_injected_client_json():
    payload = ('[{"metric": "specific_yield_today", "title": "Spec yield today", '
               '"insight": "A leads"}]')
    specs = design_charts("DATA", client=_FakeClient(payload))
    assert len(specs) == 1
    assert specs[0]["metric"] == "specific_yield_today"
    assert specs[0]["title"] == "Spec yield today"


def test_design_charts_drops_unknown_metrics():
    payload = ('[{"metric": "specific_yield_today", "title": "ok", "insight": "i"},'
               ' {"metric": "made_up_metric", "title": "bad", "insight": "i"}]')
    specs = design_charts("DATA", client=_FakeClient(payload))
    metrics = [s["metric"] for s in specs]
    assert "specific_yield_today" in metrics
    assert "made_up_metric" not in metrics


def test_design_charts_tolerates_code_fence():
    payload = '```json\n[{"metric":"energy_today","title":"E","insight":"i"}]\n```'
    specs = design_charts("DATA", client=_FakeClient(payload))
    assert [s["metric"] for s in specs] == ["energy_today"]


def test_design_charts_request_shape():
    c = _FakeClient('[]')
    design_charts("DATA-BLOCK", client=c)
    assert c.kwargs["model"] == "claude-opus-4-8"
    assert c.kwargs["output_config"] == {"effort": "xhigh"}
    assert c.kwargs["thinking"] == {"type": "adaptive"}
    assert "DATA-BLOCK" in c.kwargs["messages"][0]["content"]
    assert "temperature" not in c.kwargs


def test_render_charts_uses_grounded_python_values():
    plants = [_plant("Alpha", 100.0, 150.0), _plant("Beta", 100.0, 75.0)]
    spec = {"metric": "specific_yield_today", "title": "Specific yield today",
            "insight": "Alpha leads Beta."}
    html = render_charts([spec], plants)
    assert "Specific yield today" in html
    assert "Alpha leads Beta." in html      # insight caption
    assert "Alpha" in html and "Beta" in html
    assert "1.5" in html and "0.75" in html  # 150/100 and 75/100, computed by Python


def test_render_charts_is_email_safe():
    plants = [_plant("Alpha", 100.0, 150.0)]
    html = render_charts([{"metric": "energy_today", "title": "E", "insight": "i"}], plants)
    assert "<svg" not in html and "<script" not in html and "<canvas" not in html
    assert "var(" not in html                 # no CSS custom properties
    assert "style=" in html                    # inline styled
    assert "<table" in html                    # table-based bars


def test_render_charts_skips_metric_with_no_data():
    # No plant has co2 data -> that chart is omitted, not rendered empty.
    plants = [_plant("Alpha", 100.0, 150.0)]
    html = render_charts([{"metric": "co2_avoided", "title": "CO2", "insight": "i"}], plants)
    assert "CO2" not in html


def test_chart_metrics_whitelist_has_expected_keys():
    for key in ("specific_yield_today", "specific_yield_month", "energy_today",
                "energy_month", "current_power", "co2_avoided"):
        assert key in CHART_METRICS
