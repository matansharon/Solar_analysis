from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from .config import load_config
from .core.schema import TimeRange
from .core.session_store import SessionStore
from .core.report import render_html, write_report, append_unavailable_section
from .pipeline import run_pipeline

def main(argv=None):
    p = argparse.ArgumentParser(prog="solar-analysis")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--range", default="30d", choices=[t.value for t in TimeRange])
    p.add_argument("--out", default=None)
    p.add_argument("--cache-dir", default=".session_cache")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    time_range = TimeRange(args.range)
    ss = SessionStore(args.cache_dir)
    res = run_pipeline(cfg, time_range, ss)

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

    html = render_html(report_md, title, subtitle)
    path = write_report(html, out_dir)
    if res["verify_missing"]:
        print(f"[note] {len(res['verify_missing'])} report figure(s) not found verbatim in source data "
              f"(may include derived deltas): {res['verify_missing'][:8]}", file=sys.stderr)
    print(f"Report written: {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
