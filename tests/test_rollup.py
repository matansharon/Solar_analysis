from solaranalysis.core.schema import EnergyPoint, PlantData, TimeRange
from solaranalysis.core.rollup import rollup_energy, worst_periods, plan_rollup

def _days():
    return [
        EnergyPoint("2025-01-01", 10.0, "day"),
        EnergyPoint("2025-01-02", 20.0, "day"),
        EnergyPoint("2025-02-01", 5.0, "day"),
        EnergyPoint("2025-02-03", 7.0, "day"),
    ]

def test_rollup_daily_to_monthly():
    out = rollup_energy(_days(), "month")
    by = {p.timestamp_local: p.energy_kwh for p in out}
    assert by["2025-01"] == 30.0
    assert by["2025-02"] == 12.0
    assert all(p.granularity == "month" for p in out)

def test_worst_periods():
    w = worst_periods(_days(), 2)
    assert [p.energy_kwh for p in w] == [5.0, 7.0]

def test_plan_rollup_12mo_uses_monthly_and_worst():
    pd = PlantData(plant_id="p", source_platform="growatt", source_plant_id="1",
                   plant_name="P", energy_timeseries=_days())
    res = plan_rollup(pd, TimeRange.LAST_12MO)
    assert res["granularity"] == "month"
    assert len(res["series"]) == 2
    assert len(res["worst"]) == 4  # only 4 days available; min(n, len)

def test_plan_rollup_snapshot_empty_series():
    pd = PlantData(plant_id="p", source_platform="growatt", source_plant_id="1",
                   plant_name="P", energy_timeseries=_days())
    res = plan_rollup(pd, TimeRange.SNAPSHOT)
    assert res["series"] == []
