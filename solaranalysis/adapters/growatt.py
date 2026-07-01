from .base import SolarPortalAdapter
from ..core.schema import PlantData, TimeRange
class GrowattAdapter(SolarPortalAdapter):
    platform = "growatt"
    def login(self) -> None: ...
    def fetch(self, time_range: TimeRange) -> list[PlantData]: return []
