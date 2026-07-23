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
                           write_dashboard, append_unavailable_section,
                           prepend_summary, prepend_status)
from ..core.analyze import (summarize_executive, status_overview,
                            build_data_block, default_meta)
from ..core.charts import design_charts, render_charts
from ..core.dashboard import compose_dashboard
from ..adapters.base import get_adapter
from ..pipeline import run_pipeline
from . import db, repo, crypto, events, mailer
from .paths import Paths


def build_app_config(conn, key, plant_id=None):
    settings = repo.get_app_settings(conn)
    plants, names = [], {}
    for p in repo.list_plants(conn):
        if not p["enabled"]:
            continue
        if plant_id is not None and p["id"] != plant_id:
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
        plant_id = run.get("plant_id")
        cfg, names = build_app_config(conn, key, plant_id=plant_id)
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
                           on_fetched=persist, record_raw=True)

        skipped = [{"name": s["name"], "reason": red.redact(str(s["reason"]))}
                   for s in res["skipped_plants"]]
        report_md = append_unavailable_section(res["report_md"], skipped)
        base_md = report_md   # report + "Unavailable Plants" — the status input
        summary_md = None
        status_md = None
        if res["plants"]:
            try:
                summary_md = summarize_executive(res["report_md"])
                report_md = prepend_summary(report_md, summary_md)
                events.emit_event({"event": "note",
                                   "reason": "Hebrew executive summary added"})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"executive summary skipped: {e}")})
            try:
                status_md = status_overview(base_md)
                report_md = prepend_status(report_md, status_md)
                events.emit_event({"event": "note",
                                   "reason": "System status overview added"})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"status overview skipped: {e}")})
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if plant_id is not None:
            scope_label = names.get(plant_id, f"system {plant_id}")
        else:
            scope_label = f"{len(res['plants'])} plants"
        subtitle = f"{scope_label} · range {run['time_range']} · {stamp} UTC"
        html = render_html(report_md, "Solar Fleet Analysis", subtitle)
        out_dir = f"{paths.output_dir}/{stamp}"
        write_report(html, out_dir)
        rel = f"output/{stamp}/report.html"
        events.emit_event({"event": "report_written", "path": rel})

        # Executive dashboard: model-designed / Python-rendered grounded charts +
        # the Hebrew summary, composed into one email-safe HTML. It is what gets
        # emailed when available; the detailed report.html stays on disk.
        dashboard_html = None
        if res["plants"] and summary_md:
            try:
                specs = design_charts(build_data_block(
                    res["plants"], time_range, default_meta(res["plants"])))
                charts_html = render_charts(specs, res["plants"])
                dashboard_html = compose_dashboard(
                    summary_md, charts_html, status_md=status_md,
                    date_str=datetime.now().strftime("%d.%m.%Y"))
                write_dashboard(dashboard_html, out_dir)
                events.emit_event({"event": "dashboard_written",
                                   "path": f"output/{stamp}/dashboard.html",
                                   "charts": len(specs)})
            except Exception as e:
                dashboard_html = None
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"dashboard skipped: {e}")})

        status = "partial" if skipped else "success"
        subject = (f"Solar Fleet Analysis · {status} · {scope_label} "
                   f"· range {run['time_range']} · {stamp} UTC")
        try:
            if mailer.is_configured() and mailer.recipients():
                body = dashboard_html or render_email_html(
                    report_md, "Solar Fleet Analysis", subtitle)
                mailer.send_report(subject, body)
                events.emit_event({"event": "report_emailed",
                                   "to": mailer.recipients(),
                                   "body": "dashboard" if dashboard_html else "report"})
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
