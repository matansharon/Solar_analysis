"""Orchestrate per-day Growatt string collection: walk the day's pages, map,
store. Pure of login concerns — the CLI supplies an authenticated
BrowserSession."""
from __future__ import annotations
from datetime import date, timedelta

from . import history_client as hc
from . import mappers, store

PAGES_PER_DAY = 4          # 288 samples/day at 80 rows/page


def day_range(target: date, count: int) -> list[str]:
    """`count` consecutive ISO days ending at `target` (oldest first)."""
    count = max(1, count)
    return [(target - timedelta(days=n)).isoformat()
            for n in range(count - 1, -1, -1)]


def fetch_day_rows(bs, sn: str, day: str,
                   datalog_sn: str) -> tuple[list[dict], int, bool]:
    """Every 5-minute sample for `day`: (rows, pages_fetched, complete).

    Page 0 holds the day's last sample, and therefore its per-channel energy
    counters, so a page-0 failure propagates and fails the whole day. Pages 1-3
    are intraday enrichment: if one fails we stop and keep what we have.

    `complete` reflects WHY the walk stopped, not how far it got — a short day
    whose last page says `haveNext: false` is complete. It is false only when a
    later page raised, or when the page cap was hit with `haveNext` still true
    (i.e. the portal had more than 288 samples and we truncated).
    """
    rows: list[dict] = []
    pages_ok, complete = 0, False
    for page in range(PAGES_PER_DAY):
        try:
            obj = (hc.get_history_page(bs, sn, day, page, datalog_sn)
                   or {}).get("obj") or {}
        except Exception:
            if page == 0:
                raise
            break                     # complete stays False
        datas = [r for r in (obj.get("datas") or []) if isinstance(r, dict)]
        rows += datas
        pages_ok += 1
        if not datas or not obj.get("haveNext"):
            complete = True
            break
    return rows, pages_ok, complete


def collect_day(bs, conn, inv: mappers.InverterInfo, day: str, now: str) -> dict:
    """Fetch, map and persist one plant-local day for one inverter."""
    rows, pages_ok, complete = fetch_day_rows(bs, inv.serial, day,
                                              inv.datalog_sn or "")
    if not rows:
        # Pre-install days (and days beyond the portal's ~90-day rejection
        # window) answer with an empty list. That is normal, not a failure.
        return {"day": day, "empty": True, "pages_ok": pages_ok}

    channels = mappers.channel_inventory(rows)
    plant_uid = f"growatt-{inv.plant_id}" if inv.plant_id else None
    store.save_channels(conn, inv.serial, plant_uid, channels, now)

    energy = mappers.map_day_energy(rows)
    store.save_day_energy(conn, inv.serial, day, energy, now)

    samples = mappers.map_channel_samples(rows, channels)
    store.save_channel_samples(conn, inv.serial, samples)
    inv_samples = mappers.map_inverter_samples(rows)
    store.save_inverter_samples(conn, inv.serial, inv_samples)

    return {"day": day, "empty": False, "pages_ok": pages_ok,
            "partial": not complete,
            "channels": len(channels), "energy_rows": len(energy),
            "samples": len(samples), "inverter_samples": len(inv_samples)}


def collect(conn, inv: mappers.InverterInfo, days: list[str], now: str,
            bs=None, bs_for: dict | None = None) -> list[dict]:
    """Collect every day. Provide either one `bs` (production) or a
    `bs_for` {day: bs} mapping (tests). Per-day failures are isolated:
    logged into the result list and skipped, never aborting the run."""
    results = []
    for day in days:
        session = bs_for[day] if bs_for is not None else bs
        try:
            res = collect_day(session, conn, inv, day, now)
            conn.commit()
        except Exception as e:            # isolate per-day failure
            conn.rollback()
            res = {"day": day, "error": str(e)}
        results.append(res)
    return results
