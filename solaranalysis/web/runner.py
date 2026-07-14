from __future__ import annotations
import argparse
import os
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

from ..config import AppConfig, PlantConfig
from ..core import measurements
from ..core.schema import TimeRange
from ..core.session_store import SessionStore
from ..core.report import (render_html, render_email_html, write_report,
                           append_unavailable_section, prepend_summary)
from ..core.analyze import summarize_executive
from ..adapters.base import get_adapter
from ..pipeline import run_pipeline
from . import db, repo, crypto, events, mailer
from .paths import Paths


def build_app_config(conn, key):
    settings = repo.get_app_settings(conn)
    plants, names = [], {}
    for p in repo.list_plants(conn):
        if not p["enabled"]:
            continue
        auth = repo.load_plant_auth(conn, key, p["id"])
        plants.append(PlantConfig(name=p["name"], auth=auth,
                                  tariff_per_kwh=p["tariff_per_kwh"],
                                  currency=p["currency"], config_id=p["id"]))
        names[p["id"]] = p["name"]
    cfg = AppConfig(plants=plants, model=settings["model"],
                    max_input_tokens=settings["max_input_tokens"],
                    output_language=settings["output_language"])
    return cfg, names


def collect_secrets(cfg: AppConfig) -> list[str]:
    out = []
    for pc in cfg.plants:
        if pc.auth.password:
            out.append(pc.auth.password)
        if pc.auth.token:
            out.append(pc.auth.token)
    graph_secret = os.getenv("GRAPH_CLIENT_SECRET")
    if graph_secret:
        out.append(graph_secret)
    return out


def run_analysis_job(paths: Paths, run_id: int) -> int:
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    # A scheduled subprocess may outlive an app upgrade; make sure the
    # measurement tables exist even if the web app hasn't restarted.
    db.init_db(conn)
    red = events.Redactor([])
    try:
        key = crypto.load_or_create_key(paths.key_path)
        run = repo.get_run(conn, run_id)
        time_range = TimeRange(run["time_range"])
        cfg, _ = build_app_config(conn, key)
        red = events.Redactor(collect_secrets(cfg))
        ss = SessionStore(paths.session_cache_dir)

        def progress(ev):
            # Redact free-text fields before they leave the process.
            if "reason" in ev and ev["reason"]:
                ev = {**ev, "reason": red.redact(str(ev["reason"]))}
            events.emit_event(ev)

        def persist(plants):
            # Persistence failure must never fail the run — note and move on.
            try:
                measurements.save_measurements(conn, plants, time_range, run_id)
                conn.commit()
                events.emit_event({"event": "measurements_saved",
                                   "plants": len(plants)})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"measurement persistence failed: {e}")})

        events.emit_event({"event": "run_start",
                           "plants": [p.name for p in cfg.plants],
                           "time_range": run["time_range"]})
        res = run_pipeline(cfg, time_range, ss, progress=progress,
                           on_fetched=persist)

        skipped = [{"name": s["name"], "reason": red.redact(str(s["reason"]))}
                   for s in res["skipped_plants"]]
        report_md = append_unavailable_section(res["report_md"], skipped)
        if res["plants"]:
            try:
                summary_md = summarize_executive(res["report_md"])
                report_md = prepend_summary(report_md, summary_md)
                events.emit_event({"event": "note",
                                   "reason": "Hebrew executive summary added"})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"executive summary skipped: {e}")})
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        subtitle = f"{len(res['plants'])} plants · range {run['time_range']} · {stamp} UTC"
        html = render_html(report_md, "Solar Fleet Analysis", subtitle)
        out_dir = f"{paths.output_dir}/{stamp}"
        write_report(html, out_dir)
        rel = f"output/{stamp}/report.html"
        events.emit_event({"event": "report_written", "path": rel})

        status = "partial" if skipped else "success"
        subject = (f"Solar Fleet Analysis · {status} · {len(res['plants'])} plants "
                   f"· range {run['time_range']} · {stamp} UTC")
        try:
            if mailer.is_configured() and mailer.recipients():
                mailer.send_report(
                    subject,
                    render_email_html(report_md, "Solar Fleet Analysis", subtitle))
                events.emit_event({"event": "report_emailed", "to": mailer.recipients()})
            else:
                events.emit_event({"event": "note",
                                   "reason": "email not configured; skipping"})
        except Exception as e:
            events.emit_event({"event": "note",
                               "reason": red.redact(f"email send failed: {e}")})
        summary = [{"name": p.plant_name, "ok": True} for p in res["plants"]]
        summary += [{"name": s["name"], "ok": False, "reason": s["reason"]} for s in skipped]
        events.emit_event({"event": "run_complete", "status": status,
                           "report_path": rel, "skipped": skipped,
                           "plants_summary": summary,
                           "notes": {"verify_missing_count": len(res["verify_missing"]),
                                     "series_missing": not any(
                                         p.energy_timeseries for p in res["plants"])}})
        return 0
    except Exception as e:
        events.emit_event({"event": "run_complete", "status": "failed",
                           "error": red.redact(f"{e}\n{traceback.format_exc()}")})
        return 1
    finally:
        conn.close()


def run_test_job(paths: Paths, plant_id: int) -> int:
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    red = events.Redactor([auth.password, auth.token] if auth else [])
    ss = SessionStore(paths.session_cache_dir)
    ok, error = True, None
    try:
        adapter = get_adapter(auth, ss)
        adapter.verify_login()
    except Exception as e:
        ok, error = False, red.redact(str(e))
    events.emit_event({"event": "test_result", "ok": ok, "error": error})
    conn.close()
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solaranalysis.web.runner")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--run-id", type=int)
    ap.add_argument("--plant-id", type=int)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    args = ap.parse_args(argv)
    paths = Paths.create(args.data_dir, args.app_dir)
    if args.test:
        return run_test_job(paths, args.plant_id)
    return run_analysis_job(paths, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
