"""Standalone Growatt per-string collector entry point.

    python -m solaranalysis.strings --data-dir DIR --app-dir DIR
        [--date YYYY-MM-DD]   # default: yesterday
        [--backfill N]        # N days ending at --date (default 1)

Loads the enabled Growatt plant's credentials from app.db, authenticates via
the Growatt adapter (reusing its session cache), and collects per-MPPT-input
daily energy plus the 5-minute channel/inverter series into app.db.

Analysis, reporting and email are Phase C2 and are not wired here.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta

from dotenv import load_dotenv

from ..adapters._browser import BrowserSession
from ..adapters.growatt import GrowattAdapter
from ..core.session_store import SessionStore
from ..web import db, repo, crypto
from ..web.paths import Paths
from . import collector, history_client, mappers
from ._now import now_utc


def resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]:
    target = date.fromisoformat(date_arg) if date_arg else (today - timedelta(days=1))
    return collector.day_range(target, backfill)


def growatt_plant_id(conn) -> int | None:
    """The web app's `plants.id` for the enabled Growatt plant (credentials)."""
    for p in repo.list_plants(conn):
        if p["platform"] == "growatt" and p["enabled"]:
            return p["id"]
    return None


def first_plant_id(plants) -> str | None:
    """The portal's own plant id (e.g. '10950561') from getPlantListTitle."""
    for p in plants or []:
        if isinstance(p, dict) and p.get("id") is not None:
            return str(p["id"])
    return None


def exit_code(results: list[dict]) -> int:
    """4 if any day in the flat per-day result list carries an `"error"` key,
    else 0. An `"empty"` day (pre-install or out-of-range) is not a failure --
    only an actual error should tell a Scheduled Task the run failed."""
    return 4 if any("error" in r for r in results) else 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="solaranalysis.strings")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=1)
    return ap


def main(argv=None, today=None) -> int:
    args = _build_parser().parse_args(argv)

    paths = Paths.create(args.data_dir, args.app_dir)
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    db.init_db(conn)

    plant_id = growatt_plant_id(conn)
    if plant_id is None:
        print("no enabled Growatt plant configured in app.db")
        conn.close()
        return 2

    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    days = resolve_days(args.date, args.backfill, today or date.today())

    adapter = GrowattAdapter(auth, SessionStore(paths.session_cache_dir))
    adapter.login()
    state = adapter._load_session()
    with BrowserSession(storage_state=state) as bs:
        adapter._authenticate(bs, had_state=bool(state))
        adapter._save_session(bs)

        source_plant_id = first_plant_id(history_client.get_plant_list(bs))
        if source_plant_id is None:
            print("no Growatt plants on the account (plant list empty or unauthorized)")
            conn.close()
            return 3

        devices = mappers.parse_devices(
            history_client.get_device_list(bs, source_plant_id))
        if not devices:
            # An empty device list is almost always a failed/unauthorized
            # fetch, not a genuinely deviceless plant -- don't exit 0 silently.
            print(f"no devices found on plant {source_plant_id} "
                  "(device list empty or unauthorized)")
            conn.close()
            return 3

        now = now_utc()
        all_results = []
        for inv in devices:
            print(f"inverter {inv.serial} ({inv.model}) — {len(days)} day(s)")
            all_results.append(
                (inv, collector.collect(conn, inv, days, now, bs=bs)))

    for inv, results in all_results:
        for r in results:
            if "error" in r:
                print(f"{inv.serial} {r['day']}: ERROR {r['error']}")
            elif r.get("empty"):
                print(f"{inv.serial} {r['day']}: no data (pre-install or out of range)")
            else:
                flag = " PARTIAL" if r.get("partial") else ""
                print(f"{inv.serial} {r['day']}: {r['channels']} channels, "
                      f"{r['energy_rows']} energy rows, {r['samples']} samples, "
                      f"{r['pages_ok']}/{collector.PAGES_PER_DAY} pages{flag}")

    flat_results = [r for _, results in all_results for r in results]
    failed = sum(1 for r in flat_results if "error" in r)
    if failed:
        print(f"{failed} of {len(flat_results)} day(s) failed")
    conn.close()
    return exit_code(flat_results)
