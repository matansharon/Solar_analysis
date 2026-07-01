from solaranalysis.config import AppConfig, PlantConfig, AuthConfig
from solaranalysis.core.schema import PlantData, Metric, TimeRange
from solaranalysis.core.session_store import SessionStore
from solaranalysis.pipeline import run_pipeline

class FakeAdapter:
    def __init__(self, pd): self._pd = pd
    def login(self): pass
    def fetch(self, tr): return [self._pd]

def _pd(name):
    return PlantData(plant_id=name, source_platform="growatt", source_plant_id="1",
                     plant_name=name, peak_power_kwp=Metric(100.0, "kWp"),
                     energy_lifetime_kwh=Metric(5000.0, "kWh"))

def test_run_pipeline_normalizes_and_analyzes(tmp_path):
    cfg = AppConfig(plants=[PlantConfig("A", AuthConfig("growatt", username="u", password="p"))])
    ss = SessionStore(str(tmp_path))
    def factory(auth, store): return FakeAdapter(_pd("A"))
    def analyzer(plants, tr, c, client=None): return "## Production & Performance\nok 50.0"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    assert "Production" in res["report_md"]
    # specific yield computed in Python: 5000 / 100 = 50.0
    assert res["plants"][0].specific_yield_kwh_per_kwp.value == 50.0

def test_pipeline_survives_one_plant_failure(tmp_path):
    class Boom:
        def login(self): raise RuntimeError("auth failed")
        def fetch(self, tr): raise RuntimeError("nope")
    cfg = AppConfig(plants=[
        PlantConfig("Bad", AuthConfig("growatt", username="bad", password="p")),
        PlantConfig("Good", AuthConfig("growatt", username="good", password="p")),
    ])
    ss = SessionStore(str(tmp_path))
    seq = [Boom(), FakeAdapter(_pd("Good"))]   # dispatched in plant order
    def factory(auth, store): return seq.pop(0)
    def analyzer(plants, tr, c, client=None): return "## Production & Performance\nok"
    res = run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory, analyzer=analyzer)
    names = [p.plant_name for p in res["plants"]]
    assert "Good" in names and "Bad" not in names
