"""Shared venv relaunch for the root entry points (`app.py`, `daily_pipeline.py`).

Under a non-venv interpreter the installed anthropic SDK is old enough that the
Claude narrative calls fail *silently* — the run exits 0 and still emails, with
no narrative in the report. Stdlib only, so it is importable before any
dependency is installed.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = (os.path.join(ROOT, ".venv", "Scripts", "python.exe") if os.name == "nt"
           else os.path.join(ROOT, ".venv", "bin", "python"))
GUARD = "SOLAR_APP_IN_VENV"


def same_interpreter(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == \
            os.path.normcase(os.path.realpath(b))


def relaunch(script_path: str, guard: str = GUARD) -> int | None:
    """Re-run `script_path` with the venv's Python. None = already correct."""
    if os.environ.get(guard) == "1":
        return None
    if not os.path.isfile(VENV_PY) or same_interpreter(VENV_PY, sys.executable):
        return None
    env = dict(os.environ, **{guard: "1"})
    try:
        return subprocess.run([VENV_PY, os.path.abspath(script_path),
                               *sys.argv[1:]], cwd=ROOT, env=env).returncode
    except KeyboardInterrupt:
        return 0
