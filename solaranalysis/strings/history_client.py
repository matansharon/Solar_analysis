"""Thin authenticated POST wrappers for the Growatt device-history API.

All calls run inside an already-authenticated BrowserSession (cookies shared
via context.request). No parsing here — see mappers.py.

This portal requires FORM-encoded bodies; query strings return null (the same
constraint documented in adapters/growatt.py). It also intermittently stalls
the request itself (Chromium raises net::ERR_TIMED_OUT), a partial-outage
pattern that recovers within seconds — hence the bounded retry.
"""
from __future__ import annotations
import time

BASE = "https://server.growatt.com"
PAGE_SIZE = 80          # rows per page of getMAXHistory
RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 2.0


def get_plant_list(bs) -> list:
    """Every plant on the account: [{id, plantName, timezone}, ...]."""
    return bs.post_json(f"{BASE}/index/getPlantListTitle") or []


def get_device_list(bs, plant_id) -> dict:
    return bs.post_json(f"{BASE}/panel/getDevicesByPlantList",
                        form={"plantId": str(plant_id), "currPage": "1"}) or {}


def get_history_page(bs, sn: str, day: str, page: int,
                     datalog_sn: str = "", sleep=time.sleep) -> dict:
    """One page of 5-minute samples for a single plant-local day.

    `page` is a 0-based PAGE index (the portal's `start` param), not a row
    offset. start-date == end-date restricts the query to that one day, which
    makes page 0 row 0 the day's last sample.
    """
    form = {"maxSn": sn, "startDate": day, "endDate": day,
            "start": str(page), "allDatalogSns": datalog_sn}
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return bs.post_json(f"{BASE}/device/getMAXHistory", form=form) or {}
        except Exception as e:
            last = e
            if attempt < RETRY_ATTEMPTS - 1:
                sleep(RETRY_DELAY_S)
    raise last
