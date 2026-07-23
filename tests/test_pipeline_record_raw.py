from solaranalysis.config import AppConfig, PlantConfig, AuthConfig
from solaranalysis.core.schema import PlantData
from solaranalysis.pipeline import run_pipeline


def _cfg():
    return AppConfig(plants=[PlantConfig(
        name="p", auth=AuthConfig("stub", username="u", password="x"))])


def test_run_pipeline_propagates_record_raw():
    seen = {}

    class FakeAdapter:
        record_raw = False
        def login(self): ...
        def fetch(self, time_range):
            seen["record_raw"] = self.record_raw
            return [PlantData("uid", "stub", "1", "S")]

    def factory(auth, ss):
        return FakeAdapter()

    from solaranalysis.core.schema import TimeRange
    run_pipeline(_cfg(), TimeRange.SNAPSHOT, session_store=None,
                 adapter_factory=factory, analyzer=lambda *a, **k: "ok",
                 record_raw=True)
    assert seen["record_raw"] is True


def test_run_pipeline_defaults_record_raw_false():
    seen = {}

    class FakeAdapter:
        record_raw = False
        def login(self): ...
        def fetch(self, time_range):
            seen["record_raw"] = self.record_raw
            return [PlantData("uid", "stub", "1", "S")]

    from solaranalysis.core.schema import TimeRange
    run_pipeline(_cfg(), TimeRange.SNAPSHOT, session_store=None,
                 adapter_factory=lambda a, s: FakeAdapter(),
                 analyzer=lambda *a, **k: "ok")
    assert seen["record_raw"] is False
