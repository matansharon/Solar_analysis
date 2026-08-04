"""Scheduled entry point for the whole pipeline: `python daily_pipeline.py`.

Thin wrapper over `python -m solaranalysis.orchestrator`, mirroring `app.py`: it
re-launches itself under the project's `.venv` when started with some other
Python, and defaults `--data-dir`/`--app-dir` to this repo. All other arguments
pass straight through, e.g. `python daily_pipeline.py --only strings --no-email`.

The registered Scheduled Task calls `-m solaranalysis.orchestrator` directly
(DEPLOYMENT.md §13); this file is the by-hand form.
"""
from __future__ import annotations

import os
import sys

import _venv

ROOT = _venv.ROOT


def _relaunch():
    return _venv.relaunch(__file__)


def _orchestrator_main(argv):
    sys.path.insert(0, ROOT)
    from solaranalysis.orchestrator import main as orchestrator_main
    return orchestrator_main(argv)


def _with_default(argv: list[str], flag: str, value: str) -> list[str]:
    if any(a == flag or a.startswith(flag + "=") for a in argv):
        return argv
    return argv + [flag, value]


def main(argv=None) -> int:
    rc = _relaunch()
    if rc is not None:
        return rc
    if not os.path.isfile(_venv.VENV_PY):
        print(f"warning: no virtualenv at {os.path.join(ROOT, '.venv')} — "
              f"running with {sys.executable}; Claude narratives may fail "
              f"silently", file=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _with_default(argv, "--data-dir", os.path.join(ROOT, "data"))
    argv = _with_default(argv, "--app-dir", ROOT)
    try:
        return _orchestrator_main(argv)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
