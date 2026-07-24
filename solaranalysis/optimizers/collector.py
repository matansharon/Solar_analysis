"""Orchestrate per-site optimizer collection: tree -> inventory, then
per-day energy/by-inverter -> stored rows. Pure of login concerns — the CLI
supplies an authenticated BrowserSession."""
from __future__ import annotations
from datetime import date, timedelta

from . import layout_client as lc
from . import mappers, store


def parse_site_ids(search_payload: dict) -> list[int]:
    ids = []
    for s in (search_payload or {}).get("page") or []:
        if isinstance(s, dict) and isinstance(s.get("solarFieldId"), int):
            ids.append(s["solarFieldId"])
    return ids


def day_range(target: date, count: int) -> list[str]:
    """`count` consecutive ISO days ending at `target` (oldest first)."""
    count = max(1, count)
    return [(target - timedelta(days=n)).isoformat()
            for n in range(count - 1, -1, -1)]


def collect_site(bs, conn, site_id: int, days: list[str], now: str) -> dict:
    tree = lc.get_logical_tree(bs, site_id)
    infos = mappers.flatten_inventory(tree)
    store.save_inventory(conn, site_id, infos, now)
    inverter_serials = sorted({i.inverter_serial for i in infos if i.inverter_serial})
    energy_rows = 0
    for day in days:
        payload = lc.get_by_inverter_energy(bs, site_id, day, inverter_serials)
        rows = mappers.map_by_inverter_energy(payload)
        store.save_energy(conn, site_id, day, rows, now)
        energy_rows += len(rows)
    return {"site_id": site_id, "optimizers": len(infos),
            "days": len(days), "energy_rows": energy_rows}


def collect(conn, site_ids: list[int], days: list[str], now: str,
            bs=None, bs_for: dict | None = None) -> list[dict]:
    """Collect every site. Provide either one `bs` (production) or a
    `bs_for` {site_id: bs} mapping (tests). Per-site failures are isolated:
    logged into the result list and skipped, never aborting the run."""
    results = []
    for sid in site_ids:
        session = bs_for[sid] if bs_for is not None else bs
        try:
            res = collect_site(session, conn, sid, days, now)
            conn.commit()
        except Exception as e:  # isolate per-site failure
            conn.rollback()
            res = {"site_id": sid, "error": str(e)}
        results.append(res)
    return results
