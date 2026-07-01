from solaranalysis.core.schema import PlantData, Metric
from solaranalysis.core.validate import validate_plant

def _plant(**kw):
    base = dict(plant_id="p", source_platform="growatt", source_plant_id="1", plant_name="P")
    base.update(kw)
    return PlantData(**base)

def test_negative_energy_flagged():
    pd = _plant(energy_today_kwh=Metric(-5.0, "kWh"))
    validate_plant(pd)
    assert any("energy_today_kwh" in f and "negative" in f for f in pd.data_quality_flags)

def test_pr_out_of_range_flagged():
    pd = _plant(performance_ratio=Metric(1.4, "ratio", is_derived=True))
    validate_plant(pd)
    assert any("performance_ratio" in f for f in pd.data_quality_flags)

def test_non_monotonic_lifetime_flagged():
    pd = _plant(energy_year_kwh=Metric(100.0, "kWh"),
                energy_lifetime_kwh=Metric(50.0, "kWh"))
    validate_plant(pd)
    assert any("lifetime" in f for f in pd.data_quality_flags)

def test_clean_plant_no_flags():
    pd = _plant(energy_today_kwh=Metric(10.0, "kWh"),
                energy_year_kwh=Metric(500.0, "kWh"),
                energy_lifetime_kwh=Metric(9000.0, "kWh"),
                performance_ratio=Metric(0.83, "ratio", is_derived=True))
    validate_plant(pd)
    assert pd.data_quality_flags == []
