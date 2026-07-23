from solaranalysis.core.schema import PlantData, RawPayload


def test_raw_payloads_defaults_empty():
    pd = PlantData("uid", "solaredge", "1", "Site")
    assert pd.raw_payloads == []


def test_to_dict_excludes_raw_payloads_but_keeps_other_fields():
    pd = PlantData("uid", "solaredge", "1", "Site")
    pd.raw_payloads = [RawPayload("meas", "http://x/meas", "GET", 200, {"a": 1})]
    d = pd.to_dict()
    assert "raw_payloads" not in d
    assert d["plant_id"] == "uid"
    assert d["energy_today_kwh"]["unit"] == "kWh"  # nested dataclass still converts
