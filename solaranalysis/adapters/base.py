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

    def _session_key(self) -> str:
        import hashlib
        ident = self.auth.username or self.auth.token or ""
        h = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:12]
        return f"{self.platform}:{h}"

    def _load_session(self) -> dict | None:
        return self.sessions.load(self._session_key())

    def _save_session(self, bs) -> None:
        """Best-effort persist of the browser session; never fails the fetch."""
        try:
            self.sessions.save(self._session_key(), bs.storage_state(), SESSION_TTL_S)
        except Exception:
            pass

    # Set True by the pipeline/runner to persist untouched portal payloads.
    record_raw: bool = False

    def _begin_raw(self, bs) -> None:
        if self.record_raw:
            bs.start_raw_capture()

    def _finish_raw(self, bs, results) -> None:
        """Attach the session's recorded raw payloads to the first PlantData."""
        if not self.record_raw or not results:
            return
        from ..core.schema import RawPayload
        from ._browser import raw_label
        results[0].raw_payloads = [
            RawPayload(endpoint_label=raw_label(r.get("url", "")),
                       url=r.get("url", ""), method=r.get("method", "GET"),
                       status=r.get("status"), body=r.get("body"))
            for r in bs.raw_records()]

    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def verify_login(self) -> None:
        """Perform a real portal login; raise AdapterError on failure."""

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
