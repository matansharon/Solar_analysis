"""Portal endpoint discovery: log in with the real adapter, record every JSON
XHR the dashboard fires, and probe candidate internal endpoints directly.

Usage:
    python -m solaranalysis.tools.discover --platform growatt [--out .discovery]
        [--idle 120]

Writes <out>/<platform>/NNN.json bodies plus an index.jsonl describing each
capture (url, method, post data, status). Run with SOLAR_HEADLESS=0 and
--idle to click around manually; the auto-tour below already visits the pages
that matter for history/devices/alerts. Recorded payloads (sanitized) become
the unit-test fixtures for new mappers.
"""
from __future__ import annotations
import argparse
import json
import re
from datetime import date
from pathlib import Path

from ..config import load_config
from ..core.session_store import SessionStore
from ..adapters import _browser
from ..adapters.base import get_adapter

_SKIP = re.compile(r"\.(js|css|png|jpg|jpeg|gif|svg|woff2?|ttf|ico|map)(\?|$)", re.I)


class Recorder:
    def __init__(self, out_dir: Path):
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.index = (self.dir / "index.jsonl").open("w", encoding="utf-8")

    def attach(self, page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, resp) -> None:
        url = resp.url
        if _SKIP.search(url):
            return
        ct = (resp.headers or {}).get("content-type", "")
        if "json" not in ct and "text/plain" not in ct:
            return
        try:
            body = resp.json()
        except Exception:
            return
        post = None
        try:
            post = resp.request.post_data
        except Exception:
            pass
        self.save(url, body, method=resp.request.method, post=post,
                  status=resp.status, kind="xhr")

    def save(self, url: str, body, *, method="GET", post=None, status=None,
             kind="probe") -> None:
        self.n += 1
        name = f"{self.n:03d}.json"
        try:
            (self.dir / name).write_text(
                json.dumps(body, indent=1, ensure_ascii=False)[:2_000_000],
                encoding="utf-8")
        except Exception:
            name = None
        self.index.write(json.dumps({
            "kind": kind, "method": method, "url": url, "post": post,
            "status": status, "file": name}, ensure_ascii=False) + "\n")
        self.index.flush()

    def save_html(self, url: str, html: str) -> None:
        self.n += 1
        name = f"{self.n:03d}.html"
        (self.dir / name).write_text(html[:2_000_000], encoding="utf-8")
        self.index.write(json.dumps({"kind": "page", "url": url,
                                     "file": name}, ensure_ascii=False) + "\n")
        self.index.flush()


def _probe(rec: Recorder, bs, method: str, url: str) -> None:
    try:
        body = bs.post_json(url) if method == "POST" else bs.get_json(url)
    except Exception as e:
        body = {"__probe_error__": str(e)}
    rec.save(url, body, method=method, kind="probe")


def tour_growatt(rec, bs, adapter):
    host = "https://server.growatt.com"
    plants = bs.post_json(f"{host}/index/getPlantListTitle") or []
    rec.save(f"{host}/index/getPlantListTitle", plants, method="POST")
    if not plants:
        return
    pid = plants[0].get("id")
    today = date.today()
    d, m, y = today.isoformat(), today.strftime("%Y-%m"), today.strftime("%Y")
    candidates = []
    for prefix in ("panel/max/getMAX", "panel/getPlant"):
        for chart, param in (("EnergyDayChart", f"date={d}"),
                             ("EnergyMonthChart", f"date={m}"),
                             ("EnergyYearChart", f"year={y}"),
                             ("EnergyTotalChart", f"year={y}"),
                             ("DayChart", f"date={d}"),
                             ("MonthChart", f"date={m}"),
                             ("YearChart", f"year={y}"),
                             ("TotalChart", f"year={y}")):
            candidates.append(f"{host}/{prefix}{chart}?plantId={pid}&{param}")
    candidates += [
        f"{host}/log/getNewPlantFaultLog?plantId={pid}&date={y}&toPageNum=1&type=1&deviceSn=&beginDate={y}-01-01",
        f"{host}/panel/getDevicesByPlantList?plantId={pid}&currPage=1",
    ]
    for url in candidates:
        _probe(rec, bs, "POST", url)
    # Let the index dashboard fire its own chart XHRs too (ground truth).
    bs.page.wait_for_timeout(8000)


def tour_solaredge(rec, bs, adapter):
    base = "https://monitoring.solaredge.com"
    sites = bs.post_json(f"{base}/services/sitelist/searchSites") or {}
    rec.save(f"{base}/services/sitelist/searchSites", sites, method="POST")
    page_list = sites.get("page") if isinstance(sites, dict) else None
    sid = (page_list or [{}])[0].get("solarFieldId")
    if sid is None:
        return
    for route in (f"/one#/site/{sid}", f"/one#/site/{sid}/alerts",
                  f"/one#/site/{sid}/layout"):
        try:
            bs.page.goto(base + route, wait_until="commit")
            bs.page.wait_for_timeout(12000)
        except Exception as e:
            rec.save(base + route, {"__nav_error__": str(e)}, kind="nav")


def tour_sma(rec, bs, adapter):
    host = "https://www.sunnyportal.com"
    bs.page.goto(f"{host}/Plants", wait_until="domcontentloaded")
    bs.page.wait_for_timeout(5000)
    rec.save_html(bs.page.url, bs.page.content())
    href = None
    links = bs.page.locator("a[href*='RedirectToPlant']")
    if links.count():
        href = links.first.get_attribute("href")
    if not href:
        return
    bs.page.goto(host + href if href.startswith("/") else href,
                 wait_until="domcontentloaded")
    bs.page.wait_for_timeout(6000)
    rec.save_html(bs.page.url, bs.page.content())
    for path in ("/FixedPages/EnergyAndPower.aspx",
                 "/FixedPages/InverterSelection.aspx",
                 "/FixedPages/Logbook.aspx",
                 "/FixedPages/PlantProfile.aspx"):
        try:
            bs.page.goto(host + path, wait_until="domcontentloaded")
            bs.page.wait_for_timeout(6000)
            rec.save_html(bs.page.url, bs.page.content())
        except Exception as e:
            rec.save(host + path, {"__nav_error__": str(e)}, kind="nav")


_TOURS = {"growatt": tour_growatt, "solaredge": tour_solaredge, "sma": tour_sma}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solaranalysis.tools.discover")
    ap.add_argument("--platform", required=True, choices=sorted(_TOURS))
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default=".discovery")
    ap.add_argument("--cache-dir", default=".session_cache")
    ap.add_argument("--idle", type=int, default=0,
                    help="seconds to keep the browser open for manual clicking")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    pc = next((p for p in cfg.plants if p.auth.platform == args.platform), None)
    if pc is None:
        print(f"no {args.platform} plant in {args.config}")
        return 2
    ss = SessionStore(args.cache_dir)
    adapter = get_adapter(pc.auth, ss)
    adapter.login()
    state = adapter._load_session()
    rec = Recorder(Path(args.out) / args.platform)
    with _browser.BrowserSession(storage_state=state) as bs:
        rec.attach(bs.page)
        adapter._authenticate(bs, had_state=bool(state))
        adapter._save_session(bs)
        bs.page.wait_for_timeout(3000)
        _TOURS[args.platform](rec, bs, adapter)
        if args.idle:
            print(f"idling {args.idle}s — click around, XHRs are being recorded")
            bs.page.wait_for_timeout(args.idle * 1000)
    print(f"captured {rec.n} artifacts under {rec.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
