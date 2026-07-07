import json
from pathlib import Path
from solaranalysis.adapters.sma import (
    map_sma_row, map_sma_list_row, map_sma_month_csv, map_sma_year_csv,
    map_sma_total_csv, map_sma_log_rows, map_sma_device_rows,
)
from solaranalysis.core.schema import AlertSeverity, DeviceStatus

FXDIR = Path(__file__).parent / "fixtures"
def _rows(): return json.loads((FXDIR / "sma_rows.json").read_text(encoding="utf-8"))
def _fx(name): return json.loads((FXDIR / name).read_text(encoding="utf-8"))


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


def test_row_extras_and_derived_year_energy():
    pd = map_sma_row(_rows()[0])
    # Previously-discarded columns now ride in extras...
    assert pd.extras["yield_yesterday_kwh"] == 333.23
    assert pd.extras["yield_lastmonth_kwh"] == 9818.38
    assert pd.extras["specific_yield_month_kwh_per_kwp"] == 12.35
    assert pd.extras["specific_yield_year_kwh_per_kwp"] == 848.49
    # ...and year energy is derived from specific yield × kWp.
    assert pd.energy_year_kwh.value == round(848.49 * 55.0, 1)
    assert pd.energy_year_kwh.is_derived is True
    assert any("derived from specific yield" in f for f in pd.data_quality_flags)
    assert pd.current_power_kw.data_source_status == "not_exposed"
    assert pd.co2_avoided_kg.data_source_status == "not_exposed"


def test_defensive_against_empty():
    pd = map_sma_row({})
    assert pd.source_platform == "sma"
    assert pd.peak_power_kwp.value is None
    assert pd.energy_today_kwh.value is None
    assert pd.energy_year_kwh.data_source_status == "not_exposed"


# ---------------------------------------------------------------------------
# GetPlantList JSON mapper (preferred plant-list source)
# ---------------------------------------------------------------------------

def test_list_row_maps_numeric_values():
    pd = map_sma_list_row(_fx("sma_plantlist.json")["aaData"][0])
    assert pd.source_plant_id == "cf2dbf6b-ed79-4695-a372-3fad78ff0357"
    assert pd.plant_name == "GTO Dalton דלתון"
    assert pd.peak_power_kwp.value == 55.0
    assert pd.energy_today_kwh.value == 261.587
    assert pd.energy_month_kwh.value == 2255.472
    assert pd.energy_lifetime_kwh.value == 1434666.283
    assert pd.extras["yield_yesterday_kwh"] == 322.626
    assert pd.extras["yield_lastmonth_kwh"] == 9818.38
    assert pd.energy_year_kwh.value == round(877.147 * 55.0, 1)
    assert pd.energy_year_kwh.is_derived is True


def test_list_row_defensive():
    pd = map_sma_list_row({})
    assert pd.source_platform == "sma"
    assert pd.energy_today_kwh.value is None


# ---------------------------------------------------------------------------
# CSV mappers (EnergyAndPower download; row order defines the period)
# ---------------------------------------------------------------------------

_MONTH_CSV = ("﻿ ;Plant / Total yield / Meter Change  [kWh]0\n"
              "6/1/26;310.631\n6/2/26;326.789\n6/3/26;\n6/4/26;327.576\n")
_YEAR_CSV = ("﻿ ;Plant / Total yield / Meter Change  [kWh]0\n"
             "Jan 26;4824.970\nFeb 26;5725.621\nMar 26;6803.863\nApr 26;\n")
_TOTAL_CSV = ("﻿ ;Plant / Total yield / Meter Change  [kWh]0\n"
              "2010;2600.430\n2011;89772.750\n2025;\n")


def test_month_csv_indexes_days_by_row_order():
    pts = map_sma_month_csv(_MONTH_CSV, "2026-06")
    assert [(p.timestamp_local, p.energy_kwh) for p in pts] == [
        ("2026-06-01", 310.631), ("2026-06-02", 326.789), ("2026-06-04", 327.576)]
    assert all(p.granularity == "day" for p in pts)


def test_year_csv_indexes_months_by_row_order():
    pts = map_sma_year_csv(_YEAR_CSV, "2026")
    assert [(p.timestamp_local, p.energy_kwh) for p in pts] == [
        ("2026-01", 4824.97), ("2026-02", 5725.621), ("2026-03", 6803.863)]
    assert all(p.granularity == "month" for p in pts)


def test_total_csv_uses_year_labels():
    pts = map_sma_total_csv(_TOTAL_CSV)
    assert [(p.timestamp_local, p.energy_kwh) for p in pts] == [
        ("2010", 2600.43), ("2011", 89772.75)]
    assert all(p.granularity == "year" for p in pts)


def test_csv_mappers_defensive():
    assert map_sma_month_csv("", "2026-06") == []
    assert map_sma_month_csv(None, "2026-06") == []
    assert map_sma_year_csv("junk without separator", "2026") == []
    assert map_sma_total_csv("header only\n") == []


# ---------------------------------------------------------------------------
# Logbook / device grid mappers
# ---------------------------------------------------------------------------

def test_log_rows_map_severity_and_message():
    alerts = map_sma_log_rows([
        {"id": "530050", "type": "Info", "time": "6/5/2026 10:00:36 AM",
         "device": "SMC 7000TL 184", "description": "Wait / ------"},
        {"id": "530051", "type": "Error", "time": "6/5/2026 11:00:00 AM",
         "device": "SMC 7000TL 184", "description": "Grid failure"},
        {"description": "", "device": ""},  # blank row skipped
        "junk",
    ])
    assert len(alerts) == 2
    assert alerts[0].severity == AlertSeverity.INFO
    assert alerts[0].alert_id == "530050"
    assert alerts[0].message == "SMC 7000TL 184: Wait / ------"
    assert alerts[1].severity == AlertSeverity.ERROR


def test_device_rows_split_loggers_from_inverters():
    devs = map_sma_device_rows([
        {"name": "150036151", "serial": "150036151", "product": "Sunny WebBox"},
        {"name": "SMC 7000TL 184", "serial": "2100381184", "product": "SMC 7000TL"},
        {"name": "x", "serial": "", "product": "y"},  # no serial -> skipped
    ])
    assert len(devs) == 2
    assert devs[0].device_type == "logger"
    assert devs[1].device_type == "inverter"
    assert devs[1].manufacturer == "SMA"
    assert all(d.status == DeviceStatus.UNKNOWN for d in devs)
