import json
from pathlib import Path
from solaranalysis.adapters.sma import map_sma_row

FXDIR = Path(__file__).parent / "fixtures"
def _rows(): return json.loads((FXDIR / "sma_rows.json").read_text(encoding="utf-8"))


def test_maps_full_row():
    pd = map_sma_row(_rows()[0])
    assert pd.source_platform == "sma"
    assert pd.source_plant_id == "cf2dbf6b-ed79-4695-a372-3fad78ff0357"
    assert pd.plant_name == "GTO Dalton דלתון"
    assert pd.peak_power_kwp.value == 55.0
    assert pd.energy_today_kwh.value == 345.84
    assert pd.energy_month_kwh.value == 679.07
    assert pd.energy_lifetime_kwh.value == 1433089.88   # comma-thousands stripped


def test_no_data_becomes_none():
    pd = map_sma_row(_rows()[1])
    assert pd.energy_today_kwh.value is None            # "No data"
    assert pd.energy_month_kwh.value is None
    assert pd.energy_lifetime_kwh.value == 1454834.59


def test_year_energy_and_current_power_not_exposed():
    pd = map_sma_row(_rows()[0])
    assert pd.energy_year_kwh.data_source_status == "not_exposed"
    assert pd.current_power_kw.data_source_status == "not_exposed"
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"


def test_defensive_against_empty():
    pd = map_sma_row({})
    assert pd.source_platform == "sma"
    assert pd.peak_power_kwp.value is None
    assert pd.energy_today_kwh.value is None
