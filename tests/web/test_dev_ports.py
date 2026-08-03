"""The Vite dev proxy must point at the port the backend actually listens on.

When these drifted apart, every /api call from the dev frontend was answered by
an unrelated project's server on the same port — the UI loaded but showed no
data, with nothing in either log to explain it.
"""
import pathlib
import re

from solaranalysis.web.__main__ import DEFAULT_PORT

VITE_CONFIG = (pathlib.Path(__file__).resolve().parents[2]
               / "frontend" / "vite.config.ts")


def _proxy_port() -> int:
    text = VITE_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'"/api":\s*"http://localhost:(\d+)"', text)
    assert m, f"no /api proxy target found in {VITE_CONFIG}"
    return int(m.group(1))


def test_vite_proxy_targets_backend_default_port():
    assert _proxy_port() == DEFAULT_PORT


def test_dev_ports_avoid_common_defaults():
    # 8000 and 5173 are the stock choices and are taken by other apps here.
    text = VITE_CONFIG.read_text(encoding="utf-8")
    assert DEFAULT_PORT != 8000
    assert re.search(r"port:\s*(\d+)", text).group(1) != "5173"


def test_vite_uses_strict_port():
    # Without this Vite silently moves to the next free port.
    assert "strictPort: true" in VITE_CONFIG.read_text(encoding="utf-8")
