from __future__ import annotations
from abc import ABC, abstractmethod
from ..config import AuthConfig
from ..core.session_store import SessionStore
from ..core.schema import PlantData, TimeRange

class AdapterError(Exception):
    pass

class SolarPortalAdapter(ABC):
    platform: str = ""

    def __init__(self, auth: AuthConfig, session_store: SessionStore):
        self.auth = auth
        self.sessions = session_store

    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def fetch(self, time_range: TimeRange) -> list[PlantData]: ...

def get_adapter(auth: AuthConfig, session_store: SessionStore) -> SolarPortalAdapter:
    # imported here to avoid circular imports at module load
    from .solaredge import SolarEdgeAdapter
    from .growatt import GrowattAdapter
    registry = {"solaredge": SolarEdgeAdapter, "growatt": GrowattAdapter}
    cls = registry.get(auth.platform)
    if cls is None:
        raise AdapterError(f"unknown platform: {auth.platform!r}")
    return cls(auth, session_store)
