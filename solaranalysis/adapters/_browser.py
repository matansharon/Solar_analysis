"""Shared headless-browser session helper for portal adapters.

All three solar portals (SolarEdge, Growatt, SMA Sunny Portal) authenticate
through a normal web login rather than a documented API. This module wraps a
Playwright Chromium session so each adapter only has to describe its own login
steps and then either (a) read the JSON the dashboard fetches on load, or
(b) call the portal's internal endpoints directly within the authenticated
session via ``context.request`` (cookies are shared automatically).

Playwright is imported lazily inside ``__enter__`` so the package (and its unit
tests, which exercise the pure mappers) import cleanly without a browser.
"""
from __future__ import annotations
import os

# A realistic desktop Chrome UA; headless-shell's default UA is occasionally
# treated differently by bot heuristics, and this matched the live probe.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def headless_default() -> bool:
    """Headless unless SOLAR_HEADLESS is explicitly set to a falsy value."""
    return os.environ.get("SOLAR_HEADLESS", "1").strip().lower() not in ("0", "false", "no")


class BrowserSession:
    """Context manager owning a Chromium browser, context and page.

    Usage::

        with BrowserSession() as bs:
            store = bs.capture(["some/endpoint"])
            bs.page.goto(...)          # do the login
            ...
            data = store["some/endpoint"]           # JSON captured on load
            other = bs.get_json("https://host/api")  # direct authenticated call
    """

    def __init__(self, headless: bool | None = None, timeout_ms: int = 45000,
                 ua: str = DEFAULT_UA, storage_state: dict | None = None):
        self.headless = headless_default() if headless is None else headless
        self.timeout_ms = timeout_ms
        self.ua = ua
        self._initial_state = storage_state  # restored cookies/localStorage
        self._pw = None
        self._browser = None
        self.context = None
        self.page = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            kwargs = dict(user_agent=self.ua,
                          viewport={"width": 1440, "height": 900}, locale="en-US")
            if self._initial_state is not None:
                kwargs["storage_state"] = self._initial_state
            self.context = self._browser.new_context(**kwargs)
            self.context.set_default_timeout(self.timeout_ms)
            self.page = self.context.new_page()
        except Exception:
            # __exit__ never runs when __enter__ raises; clean up here so a
            # failed launch can't leak the Playwright driver process.
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
        return False

    def capture(self, fragments: list[str]) -> dict:
        """Register a response listener; returns a dict populated as matching
        responses arrive. Keys are the fragments; values are the latest parsed
        JSON body whose URL contains that fragment."""
        store: dict = {}

        def on_response(resp):
            url = resp.url
            for frag in fragments:
                if frag in url:
                    try:
                        val = resp.json()
                    except Exception:
                        continue  # non-JSON / body unavailable
                    # Record even empty bodies (an empty [] still means the
                    # endpoint answered), but never clobber good data with a
                    # late empty response for the same endpoint.
                    if frag in store and store[frag] and not val:
                        continue
                    store[frag] = val

        self.page.on("response", on_response)
        return store

    def storage_state(self) -> dict:
        """Cookies/localStorage of the live context (for session caching)."""
        return self.context.storage_state()

    def get_json(self, url: str):
        """Authenticated GET within the browser session (shares cookies)."""
        r = self.context.request.get(url)
        return r.json() if r.ok else None

    def post_json(self, url: str, **kwargs):
        """Authenticated POST within the browser session (shares cookies)."""
        r = self.context.request.post(url, **kwargs)
        return r.json() if r.ok else None
