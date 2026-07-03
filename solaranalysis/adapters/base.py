from __future__ import annotations
from abc import ABC, abstractmethod
from ..config import AuthConfig
from ..core.session_store import SessionStore
from ..core.schema import PlantData, TimeRange

class AdapterError(Exception):
    pass

# Persisted portal browser sessions are reused within this window; an expired
# or invalid state just falls back to a fresh login.
SESSION_TTL_S = 6 * 3600

class SolarPortalAdapter(ABC):
    platform: str = ""

    def __init__(self, auth: AuthConfig, session_store: SessionStore):
        self.auth = auth
        self.sessions = session_store

    def _load_session(self) -> dict | None:
        return self.sessions.load(self.platform)

    def _save_session(self, bs) -> None:
        """Best-effort persist of the browser session; never fails the fetch."""
        try:
            self.sessions.save(self.platform, bs.storage_state(), SESSION_TTL_S)
        except Exception:
            pass

    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def fetch(self, time_range: TimeRange) -> list[PlantData]: ...

def get_adapter(auth: AuthConfig, session_store: SessionStore) -> SolarPortalAdapter:
    # imported here to avoid circular imports at module load
    from .solaredge import SolarEdgeAdapter
    from .growatt import GrowattAdapter
    from .sma import SMAAdapter
    registry = {"solaredge": SolarEdgeAdapter, "growatt": GrowattAdapter, "sma": SMAAdapter}
    cls = registry.get(auth.platform)
    if cls is None:
        raise AdapterError(f"unknown platform: {auth.platform!r}")
    return cls(auth, session_store)
