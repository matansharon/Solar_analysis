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
        """Route each recorded raw payload to the PlantData it belongs to.

        A payload is attributed to a site when exactly one result's
        source_plant_id appears in the URL as a bounded token (so one site id
        can't partially match inside a longer number). Zero matches means an
        account/fleet-level payload; multiple matches means an ambiguous URL
        (e.g. a shared id prefix) — both fall back to results[0].
        """
        if not self.record_raw or not results:
            return
        import re
        from ..core.schema import RawPayload
        from ._browser import raw_label

        for r in bs.raw_records():
            url = r.get("url", "")
            payload = RawPayload(endpoint_label=raw_label(url), url=url,
                                  method=r.get("method", "GET"),
                                  status=r.get("status"), body=r.get("body"))
            matches = [
                pd for pd in results
                if pd.source_plant_id
                and re.search(r"(?<![A-Za-z0-9])" + re.escape(pd.source_plant_id) + r"(?![A-Za-z0-9])", url)
            ]
            target = matches[0] if len(matches) == 1 else results[0]
            target.raw_payloads.append(payload)

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
