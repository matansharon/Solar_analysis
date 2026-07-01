from solaranalysis.core.schema import PlantData, Metric, EnergyPoint, TimeRange
from solaranalysis.core.analyze import build_data_block, pick_model, verify_numbers, run_analysis
from solaranalysis.config import AppConfig

def _plant(name, kwp, life):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(kwp, "kWp"),
                     energy_lifetime_kwh=Metric(life, "kWh"),
                     energy_timeseries=[EnergyPoint("2025-01-01", 10.0, "day"),
                                        EnergyPoint("2025-02-01", 20.0, "day")])

def test_pick_model_defaults_and_upgrade():
    cfg = AppConfig()
    assert pick_model(cfg, TimeRange.SNAPSHOT) == "claude-sonnet-5"
    assert pick_model(cfg, TimeRange.LAST_12MO) == "claude-opus-4-8"
    assert pick_model(AppConfig(model="claude-haiku-4-5"), TimeRange.ALL) == "claude-haiku-4-5"

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

def test_run_analysis_uses_injected_client():
    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": "## Production & Performance\nok"})()]
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw): return FakeMsg()
    out = run_analysis([_plant("A", 100.0, 5000.0)], TimeRange.SNAPSHOT,
                       AppConfig(), client=FakeClient())
    assert "Production & Performance" in out
