from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone
from .config import load_config
from .core.schema import TimeRange
from .core.session_store import SessionStore
from .core.report import render_html, write_report
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
    if res["skipped_plants"]:
        n = len(res["skipped_plants"])
        detail = "; ".join(f"{s['name']} ({s['reason']})" for s in res["skipped_plants"])
        print(f"[warn] {n} plant(s) unavailable: {detail}", file=sys.stderr)
        lines = "\n".join(f"- **{s['name']}**: {s['reason']}" for s in res["skipped_plants"])
        report_md += "\n\n## Unavailable Plants\n\nThe following plants could not be fetched for this run:\n\n" + lines

    html = render_html(report_md, title, subtitle)
    path = write_report(html, out_dir)
    if res["verify_missing"]:
        print(f"[warn] {len(res['verify_missing'])} report numbers not found in DATA: "
              f"{res['verify_missing'][:8]}", file=sys.stderr)
    print(f"Report written: {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
