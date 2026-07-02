from __future__ import annotations
import requests
from .base import AdapterError


class GrowattV1Error(AdapterError):
    def __init__(self, error_code, error_msg):
        self.error_code = error_code
        self.error_msg = error_msg
        super().__init__(f"growatt v1 error {error_code}: {error_msg}")


class GrowattV1Client:
    """Plain-requests client for the Growatt OpenAPI v1 (token header). Python-3.10 safe.

    Classic Growatt mobile login (newTwoLoginAPI.do) is 403-blocked as of this
    writing, and the maintained growattServer 2.x library requires Python 3.11/3.12.
    This client talks to the OpenAPI v1 REST endpoints directly with `requests`,
    authenticating via the `token` HTTP header (a ShinePhone app API token).
    """

    def __init__(self, token: str, server_url: str = "https://openapi.growatt.com/", session=None):
        self.base = server_url.rstrip("/") + "/v1/"
        self.session = session or requests.Session()
        self.session.headers.update({"token": token})

    def _get(self, path: str, params: dict | None = None):
        r = self.session.get(self.base + path, params=params or {}, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("error_code", 0) != 0:
            raise GrowattV1Error(body.get("error_code"), body.get("error_msg", ""))
        return body.get("data")

    def plant_list(self):
        return self._get("plant/list")

    def plant_details(self, plant_id):
        return self._get("plant/details", {"plant_id": plant_id})

    def plant_energy_overview(self, plant_id):
        return self._get("plant/data", {"plant_id": plant_id})

    def plant_energy_history(self, plant_id, start_date, end_date, time_unit="day", page=None, perpage=None):
        return self._get("plant/energy", {"plant_id": plant_id, "start_date": start_date,
                                          "end_date": end_date, "time_unit": time_unit,
                                          "page": page, "perpage": perpage})

    def device_list(self, plant_id):
        return self._get("device/list", {"plant_id": plant_id, "page": "", "perpage": ""})
