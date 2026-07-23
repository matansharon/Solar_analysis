import sys
import types

import pytest

from solaranalysis.adapters._browser import BrowserSession


# ---------------------------------------------------------------------------
# capture()
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, url, payload, raises=False):
        self.url = url
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


class FakeEventPage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, cb):
        self.handlers[event] = cb


def _session_with_fake_page():
    bs = BrowserSession()
    bs.page = FakeEventPage()
    return bs


def test_capture_records_empty_json_body():
    # A legitimately-empty response ([]) still counts as "arrived" — readiness
    # polls must not spin waiting for data that already came back empty.
    bs = _session_with_fake_page()
    store = bs.capture(["meas"])
    bs.page.handlers["response"](FakeResp("https://x/api/meas", []))
    assert "meas" in store
    assert store["meas"] == []


def test_capture_late_empty_does_not_clobber_good_data():
    bs = _session_with_fake_page()
    store = bs.capture(["meas"])
    bs.page.handlers["response"](FakeResp("https://x/api/meas", [{"v": 1}]))
    bs.page.handlers["response"](FakeResp("https://x/api/meas", []))
    assert store["meas"] == [{"v": 1}]


def test_capture_ignores_unparseable_bodies():
    bs = _session_with_fake_page()
    store = bs.capture(["meas"])
    bs.page.handlers["response"](FakeResp("https://x/api/meas", None, raises=True))
    assert "meas" not in store


# ---------------------------------------------------------------------------
# raw capture
# ---------------------------------------------------------------------------

import types as _types
from solaranalysis.adapters._browser import raw_label


class FakeReq:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status = payload, ok, status
    def get(self, url):
        return self
    def post(self, url, **kw):
        return self
    def json(self):
        return self._p


def test_raw_label_extracts_last_path_segment():
    assert raw_label("https://h/services/sitelist/sitesMeasurements?x=1") == "sitesMeasurements"
    assert raw_label("https://h/a/b/") == "b"


def test_raw_capture_records_json_responses():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/api/meas", [{"v": 1}]))
    recs = bs.raw_records()
    assert len(recs) == 1
    assert recs[0]["url"] == "https://x/api/meas"
    assert recs[0]["body"] == [{"v": 1}]


def test_raw_capture_skips_static_assets():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/app.js", {"x": 1}))
    assert bs.raw_records() == []


def test_raw_capture_ignores_unparseable_bodies():
    bs = _session_with_fake_page()
    bs.start_raw_capture()
    bs.page.handlers["response"](FakeResp("https://x/api/meas", None, raises=True))
    assert bs.raw_records() == []


def test_raw_records_empty_before_start():
    bs = _session_with_fake_page()
    assert bs.raw_records() == []


def test_get_json_records_when_capturing():
    bs = _session_with_fake_page()
    bs.context = _types.SimpleNamespace(request=FakeReq({"ok": 1}))
    bs.start_raw_capture()
    body = bs.get_json("https://x/api/y")
    assert body == {"ok": 1}
    recs = bs.raw_records()
    assert recs[-1]["url"] == "https://x/api/y"
    assert recs[-1]["method"] == "GET"


# ---------------------------------------------------------------------------
# __enter__ / storage_state — driven through a fake playwright module
# ---------------------------------------------------------------------------

class FakeContext:
    def __init__(self):
        self.state = {"cookies": [{"name": "x"}]}

    def set_default_timeout(self, t):
        pass

    def new_page(self):
        return FakeEventPage()

    def storage_state(self):
        return self.state


class FakeBrowser:
    def __init__(self):
        self.context_kwargs = None
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return FakeContext()

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, launch_raises=False):
        self.stopped = False
        self.browser = FakeBrowser()
        self._launch_raises = launch_raises
        self.chromium = types.SimpleNamespace(launch=self._launch)

    def _launch(self, **kwargs):
        if self._launch_raises:
            raise RuntimeError("browsers not installed")
        return self.browser

    def stop(self):
        self.stopped = True


def _install_fake_playwright(monkeypatch, pw):
    fake_mod = types.SimpleNamespace(
        sync_playwright=lambda: types.SimpleNamespace(start=lambda: pw))
    monkeypatch.setitem(sys.modules, "playwright",
                        types.SimpleNamespace(sync_api=fake_mod))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)


def test_enter_stops_playwright_when_launch_fails(monkeypatch):
    pw = FakePlaywright(launch_raises=True)
    _install_fake_playwright(monkeypatch, pw)
    with pytest.raises(RuntimeError):
        with BrowserSession():
            pass
    assert pw.stopped is True  # no orphaned driver process


def test_storage_state_passed_to_context_and_readable(monkeypatch):
    pw = FakePlaywright()
    _install_fake_playwright(monkeypatch, pw)
    state = {"cookies": [{"name": "sess", "value": "v"}]}
    with BrowserSession(storage_state=state) as bs:
        assert pw.browser.context_kwargs["storage_state"] == state
        assert bs.storage_state() == {"cookies": [{"name": "x"}]}


def test_no_storage_state_kwarg_when_absent(monkeypatch):
    pw = FakePlaywright()
    _install_fake_playwright(monkeypatch, pw)
    with BrowserSession():
        assert "storage_state" not in pw.browser.context_kwargs
