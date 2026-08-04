"""Dev entrypoint for the backend API: `python app.py`.

Thin wrapper over `python -m solaranalysis.web`. It re-launches itself under
the project's `.venv` interpreter when started with some other Python, so a
bare `python app.py` works from any shell without activating the venv first.

All arguments are passed through, e.g. `python app.py --port 8100`.
"""
from __future__ import annotations

import os
import sys

import _venv

ROOT = _venv.ROOT
VENV_PY = _venv.VENV_PY


def _relaunch_in_venv() -> int | None:
    return _venv.relaunch(__file__)


def main() -> int:
    rc = _relaunch_in_venv()
    if rc is not None:
        return rc

    if not os.path.isfile(VENV_PY):
        print(f"warning: no virtualenv at {os.path.join(ROOT, '.venv')} — "
              f"running with {sys.executable}", file=sys.stderr)

    sys.path.insert(0, ROOT)
    try:
        from solaranalysis.web.__main__ import main as web_main
    except ImportError as exc:
        print(f"error: {exc}\n"
              f"Install the backend deps first:\n"
              f"  {VENV_PY} -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    argv = list(sys.argv[1:])
    if not any(a == "--data-dir" or a.startswith("--data-dir=") for a in argv):
        argv += ["--data-dir", os.path.join(ROOT, "data")]

    try:
        return web_main(argv)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
