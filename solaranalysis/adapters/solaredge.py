from .base import SolarPortalAdapter
from ..core.schema import PlantData, TimeRange
class SolarEdgeAdapter(SolarPortalAdapter):
    platform = "solaredge"
    def login(self) -> None: ...
    def fetch(self, time_range: TimeRange) -> list[PlantData]: return []
