from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from .config import load_config
from .core.schema import TimeRange
from .core.session_store import SessionStore
from .core.report import (render_html, write_report, write_dashboard,
                          append_unavailable_section, prepend_summary)
from .core.analyze import summarize_executive, build_data_block, default_meta
from .core.charts import design_charts, render_charts
from .core.dashboard import compose_dashboard
from .pipeline import run_pipeline

def main(argv=None):
    p = argparse.ArgumentParser(prog="solar-analysis")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--range", default="30d", choices=[t.value for t in TimeRange])
    p.add_argument("--out", default=None)
    p.add_argument("--cache-dir", default=".session_cache")
    p.add_argument("--db", default="data/app.db",
                   help="SQLite DB measurements accumulate in (shared with the web UI)")
    p.add_argument("--no-persist", action="store_true",
                   help="skip saving fetched measurements to the DB")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    time_range = TimeRange(args.range)
    ss = SessionStore(args.cache_dir)

    on_fetched = None
    if not args.no_persist:
        def on_fetched(plants):
            # Persistence failure must not fail the run.
            try:
                import os
                from .web import db
                from .core import measurements
                os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
                conn = db.connect(args.db)
                try:
                    db.init_db(conn)
                    measurements.save_measurements(conn, plants, time_range,
                                                   run_id=None)
                    conn.commit()
                finally:
                    conn.close()
                print(f"[note] measurements saved to {args.db}", file=sys.stderr)
            except Exception as e:
                print(f"[warn] measurement persistence failed: {e}", file=sys.stderr)

    res = run_pipeline(cfg, time_range, ss, on_fetched=on_fetched)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or f"output/{stamp}"
    title = "Solar Fleet Analysis"
    subtitle = f"{len(res['plants'])} plants · range {args.range} · {stamp} UTC"

    report_md = res["report_md"]
    if time_range != TimeRange.SNAPSHOT and not any(p.energy_timeseries for p in res["plants"]):
        print("[note] no historical series available from the portals; "
              "the report covers current counters only.", file=sys.stderr)
    if res["skipped_plants"]:
        n = len(res["skipped_plants"])
        detail = "; ".join(f"{s['name']} ({s['reason']})" for s in res["skipped_plants"])
        print(f"[warn] {n} plant(s) unavailable: {detail}", file=sys.stderr)
        report_md = append_unavailable_section(report_md, res["skipped_plants"])

    summary_md = None
    if res["plants"]:
        try:
            summary_md = summarize_executive(res["report_md"])
            report_md = prepend_summary(report_md, summary_md)
            print("[note] Hebrew executive summary added", file=sys.stderr)
        except Exception as e:
            print(f"[warn] executive summary skipped: {e}", file=sys.stderr)

    html = render_html(report_md, title, subtitle)
    path = write_report(html, out_dir)

    if res["plants"] and summary_md:
        try:
            specs = design_charts(build_data_block(res["plants"], time_range,
                                                   default_meta(res["plants"])))
            charts_html = render_charts(specs, res["plants"])
            dashboard = compose_dashboard(summary_md, charts_html)
            dpath = write_dashboard(dashboard, out_dir)
            print(f"Dashboard written: {dpath}", file=sys.stderr)
        except Exception as e:
            print(f"[warn] dashboard skipped: {e}", file=sys.stderr)
    if res["verify_missing"]:
        print(f"[note] {len(res['verify_missing'])} report figure(s) not found verbatim in source data "
              f"(may include derived deltas): {res['verify_missing'][:8]}", file=sys.stderr)
    print(f"Report written: {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
