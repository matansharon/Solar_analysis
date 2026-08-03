"""Dev entrypoint for the backend API: `python app.py`.

Thin wrapper over `python -m solaranalysis.web`. It re-launches itself under
the project's `.venv` interpreter when started with some other Python, so a
bare `python app.py` works from any shell without activating the venv first.

All arguments are passed through, e.g. `python app.py --port 8100`.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = (os.path.join(ROOT, ".venv", "Scripts", "python.exe") if os.name == "nt"
           else os.path.join(ROOT, ".venv", "bin", "python"))
_GUARD = "SOLAR_APP_IN_VENV"


def _same_interpreter(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _relaunch_in_venv() -> int | None:
    """Re-run this script with the venv's Python. None = already correct."""
    if os.environ.get(_GUARD) == "1":
        return None
    if not os.path.isfile(VENV_PY) or _same_interpreter(VENV_PY, sys.executable):
        return None
    env = dict(os.environ, **{_GUARD: "1"})
    try:
        return subprocess.run([VENV_PY, os.path.abspath(__file__), *sys.argv[1:]],
                              cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        return 0


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
