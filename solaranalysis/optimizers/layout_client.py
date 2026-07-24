"""Thin authenticated GET/POST wrappers for the SolarEdge /services/layout/*
API. All calls run inside an already-authenticated BrowserSession (cookies
shared via context.request). No parsing here — see mappers.py."""
from __future__ import annotations

BASE = "https://monitoring.solaredge.com"


def get_site_list(bs) -> dict:
    return bs.post_json(f"{BASE}/services/sitelist/searchSites") or {}


def get_logical_tree(bs, sid) -> dict:
    return bs.get_json(
        f"{BASE}/services/layout/logical/generic/v2/site/{sid}"
        "?include-optimizers=true") or {}


def get_by_inverter_energy(bs, sid, day: str, inverter_serials: list[str]) -> dict:
    serials = ",".join(inverter_serials)
    return bs.get_json(
        f"{BASE}/services/layout/energy/site/{sid}/by-inverter"
        f"?start-date={day}&end-date={day}"
        f"&inverter-serials={serials}&include-color=true") or {}
