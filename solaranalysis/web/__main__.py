from __future__ import annotations
import argparse
import os

from .paths import Paths
from .app import create_app
from .run_manager import RunManager
from .scheduler import ScheduleService

# Matches the production port in DEPLOYMENT.md, and deliberately avoids the
# crowded 8000 — other projects on this machine use that. The Vite dev proxy in
# frontend/vite.config.ts must point at this port; a test asserts they agree.
DEFAULT_PORT = 8010


def build(data_dir: str, app_dir: str):
    paths = Paths.create(data_dir, app_dir)
    rm = RunManager(paths)
    sched = ScheduleService(paths, rm)
    app = create_app(paths, run_manager=rm, schedule_service=sched)
    return app, paths


def main(argv=None) -> int:
    default_app_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(prog="solaranalysis.web")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--app-dir", default=default_app_dir)
    args = ap.parse_args(argv)
    app, _ = build(args.data_dir, args.app_dir)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
