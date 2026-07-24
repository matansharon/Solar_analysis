"""Standalone optimizer collector entry point.

    python -m solaranalysis.optimizers --data-dir DIR --app-dir DIR
        [--date YYYY-MM-DD]   # default: yesterday
        [--backfill N]        # N days ending at --date (default 1)
        [--sites 2387929,...] # default: all sites on the account

Loads the enabled SolarEdge plant's credentials from app.db, authenticates
via the SolarEdge adapter (reusing its session cache), and collects per-
optimizer daily energy + inventory into app.db.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta

from dotenv import load_dotenv

from ..adapters._browser import BrowserSession
from ..adapters.solaredge import SolarEdgeAdapter
from ..core.report import render_email_html
from ..core.session_store import SessionStore
from ..web import db, repo, crypto, mailer
from ..web.paths import Paths
from . import analyze, collector, layout_client, report, store
from ._now import now_utc

ANALYSIS_WINDOW_DAYS = 30


def resolve_days(date_arg: str | None, backfill: int, today: date) -> list[str]:
    target = date.fromisoformat(date_arg) if date_arg else (today - timedelta(days=1))
    return collector.day_range(target, backfill)


def _solaredge_plant_id(conn) -> int | None:
    for p in repo.list_plants(conn):
        if p["platform"] == "solaredge" and p["enabled"]:
            return p["id"]
    return None


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="solaranalysis.optimizers")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=1)
    ap.add_argument("--sites")
    ap.add_argument("--no-email", action="store_true")
    return ap


def main(argv=None, today=None) -> int:
    args = _build_parser().parse_args(argv)

    paths = Paths.create(args.data_dir, args.app_dir)
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    db.init_db(conn)

    plant_id = _solaredge_plant_id(conn)
    if plant_id is None:
        print("no enabled SolarEdge plant configured in app.db")
        return 2

    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    days = resolve_days(args.date, args.backfill, today or date.today())

    adapter = SolarEdgeAdapter(auth, SessionStore(paths.session_cache_dir))
    adapter.login()
    state = adapter._load_session()
    with BrowserSession(storage_state=state) as bs:
        adapter._authenticate(bs, had_state=bool(state))
        adapter._save_session(bs)
        if args.sites:
            site_ids = [int(s) for s in args.sites.split(",") if s.strip()]
        else:
            site_ids = collector.parse_site_ids(layout_client.get_site_list(bs))
        now = now_utc()
        results = collector.collect(conn, site_ids, days, now, bs=bs)

    if not site_ids:
        # An empty site list is almost always a failed/unauthorized sitelist
        # fetch, not a genuinely empty account — don't exit 0 silently.
        print("no sites found (site list empty or unauthorized)")
        conn.close()
        return 3

    # --- analyze the accumulated series + email an anomaly report ---
    as_of = days[-1]
    since = collector.day_range(date.fromisoformat(as_of), ANALYSIS_WINDOW_DAYS)[0]
    analyses = {}
    for sid in site_ids:
        inv = store.load_inventory(conn, sid)
        rows = store.load_energy_window(conn, sid, since)
        analyses[sid] = analyze.analyze_site(sid, inv, rows, as_of)
    total_flagged = sum(len(v) for v in analyses.values())

    lang = "Hebrew" if repo.get_app_settings(conn).get("output_language") == "he" else "English"
    block = report.build_anomaly_block(analyses)
    narrative = None
    try:
        narrative = report.narrate(block, lang)
    except Exception as e:
        print(f"narrative skipped: {e}")
    md = report.render_report_md(analyses, narrative, as_of)

    if args.no_email:
        print(f"analysis complete: {total_flagged} optimizer(s) flagged (email skipped)")
    elif mailer.is_configured() and report.resolve_recipients():
        try:
            html = render_email_html(md, "SolarEdge Optimizers",
                                     f"{total_flagged} flagged · {as_of}")
            mailer.send_report(report.subject(as_of, total_flagged), html,
                               to=report.resolve_recipients())
            print(f"emailed optimizer report: {total_flagged} flagged")
        except Exception as e:
            print(f"email failed: {e}")
    else:
        print(f"analysis complete: {total_flagged} flagged (email not configured)")

    for r in results:
        if "error" in r:
            print(f"site {r['site_id']}: ERROR {r['error']}")
        else:
            print(f"site {r['site_id']}: {r['optimizers']} optimizers, "
                  f"{r['energy_rows']} energy rows over {r['days']} day(s)")
    conn.close()
    return 0
