from datetime import date

from solaranalysis.adapters.solaredge import map_solaredge_fleet


def test_energy_yesterday_becomes_daily_point():
    site = {"solarFieldId": 2387929, "name": "Baram", "status": "ACTIVE"}
    meas = {"energyToday": 100.0, "energyYesterday": 1314.5,
            "energyMonthly": 5000.0, "energyLifeTime": 900000.0}
    pd = map_solaredge_fleet(site, meas, None, None, today=date(2026, 7, 23))
    days = [p for p in pd.energy_timeseries if p.granularity == "day"]
    assert len(days) == 1
    assert days[0].timestamp_local == "2026-07-22"
    assert days[0].energy_kwh == 1314.5


def test_no_daily_point_when_energy_yesterday_absent():
    site = {"solarFieldId": 1, "name": "S", "status": "ACTIVE"}
    pd = map_solaredge_fleet(site, {"energyToday": 10.0}, None, None,
                             today=date(2026, 7, 23))
    assert [p for p in pd.energy_timeseries if p.granularity == "day"] == []
