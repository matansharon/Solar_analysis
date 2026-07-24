# SolarEdge Optimizer Analysis + Anomaly Report (B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On top of B1's stored per-optimizer daily energy, detect underperforming / dead / degrading optimizers per site, and email a grounded daily anomaly report.

**Architecture:** A pure `analyze` module (accumulated series → flagged optimizers, using SolarEdge's peer-normalized `color` plus string-median and multi-day persistence), a `report` module that builds a Python-computed data block, gets a grounded Claude narrative (numbers computed in Python, prose only from the model), assembles a markdown report, and renders it to email-safe HTML via the existing `core.report.render_email_html`; the existing `optimizers` CLI is extended to run analyze → report → email after collecting. Scheduling is an ops step.

**Tech Stack:** Python 3.10, stdlib `statistics`, `anthropic` (grounded narrative, injectable client), reuses `core.report.render_email_html` + `web.mailer`. No new dependencies.

## Global Constraints

- Run tests with the project venv: `.venv/Scripts/python.exe -m pytest` (never bare `python`/`python3`).
- ENVIRONMENT (this session): the Bash tool's PATH does not resolve bare `git`/`bash`. Prefix git commands with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH";`. Run pytest via the full venv path (works without the prefix).
- Commits carry NO AI attribution: no `Co-Authored-By: Claude`/Anthropic, no AI/Claude/Anthropic mention.
- **Grounding guarantee:** every number in the report is computed in Python (`analyze` + `report.build_anomaly_block`); the Claude call writes narrative prose only. The narrative call must be **non-fatal** (skipped with a note on failure), like `summarize_executive` in the fleet runner.
- Analysis is **pure** (no DB/network/clock inside `analyze_site`): it takes loaded rows + an explicit `as_of_day`. Tests use inline synthetic series.
- Reuse, don't reinvent: markdown → email HTML via `core.report.render_email_html(md, title, subtitle)`; send via `web.mailer.send_report(subject, html)`; `web.mailer.is_configured()`/`recipients()` gate sending.
- This is the B2 half of `specs/2026-07-23-optimizer-collector-design.md`; B1 (collect + store, schema v6, the `optimizers`/`optimizer_energy` tables, the CLI) is already merged.

## File Structure

**Create:**
- `solaranalysis/optimizers/analyze.py` — thresholds, `OptimizerAnomaly`, `analyze_site`
- `solaranalysis/optimizers/report.py` — `build_anomaly_block`, `narrate`, `render_report_md`, `subject`, `resolve_recipients`
- `tests/optimizers/test_analyze.py`, `tests/optimizers/test_report.py`

**Modify:**
- `solaranalysis/optimizers/store.py` — add `load_energy_window`
- `solaranalysis/optimizers/cli.py` — run analyze → report → email after collecting; `--no-email` flag
- `tests/optimizers/test_store.py` — add `load_energy_window` test
- `tests/optimizers/test_cli.py` — add `resolve_recipients` / flag tests

**Data shapes (from B1, via `store`):**
- inventory row dict: `{site_id, optimizer_serial, label, name, inverter_serial, inverter_name, string_label, string_name, model, status, ...}`
- energy row dict: `{site_id, optimizer_serial, day, energy_wh, color, temperature_c, updated_at_utc}`

---

### Task 1: `load_energy_window` store loader

**Files:**
- Modify: `solaranalysis/optimizers/store.py`
- Test: `tests/optimizers/test_store.py` (add)

**Interfaces:**
- Consumes: `optimizer_energy` table (B1).
- Produces: `load_energy_window(conn, site_id: int, since_day: str) -> list[dict]` — all rows for the site with `day >= since_day`, ordered by `(optimizer_serial, day)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/optimizers/test_store.py`:

```python
def test_load_energy_window_filters_by_since_day():
    conn = _conn()
    from solaranalysis.optimizers.mappers import OptimizerEnergyRow
    for day in ("2026-07-01", "2026-07-10", "2026-07-20"):
        store.save_energy(conn, 42, day,
                          [OptimizerEnergyRow("INV-1", "OPT-A", 100.0, 0.9, None)], now="n")
    rows = store.load_energy_window(conn, 42, "2026-07-10")
    days = sorted(r["day"] for r in rows)
    assert days == ["2026-07-10", "2026-07-20"]
    assert all(r["optimizer_serial"] == "OPT-A" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_store.py::test_load_energy_window_filters_by_since_day -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'load_energy_window'`.

- [ ] **Step 3: Implement the loader**

Append to `solaranalysis/optimizers/store.py`:

```python
def load_energy_window(conn: sqlite3.Connection, site_id: int,
                       since_day: str) -> list[dict]:
    """All energy rows for a site on/after `since_day`, oldest optimizer/day first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM optimizer_energy WHERE site_id=? AND day>=? "
        "ORDER BY optimizer_serial, day", (site_id, since_day))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/store.py tests/optimizers/test_store.py
git commit -m "feat: load_energy_window loader for optimizer analysis"
```

---

### Task 2: Analyze — dead / underperforming / watch

**Files:**
- Create: `solaranalysis/optimizers/analyze.py`
- Test: `tests/optimizers/test_analyze.py`

**Interfaces:**
- Consumes: inventory + energy row dicts (Task 1 / B1 store).
- Produces:
  - Module constants: `DEAD_COLOR=0.05, DEAD_DAYS=2, UNDER_COLOR=0.60, UNDER_MIN_DAYS=3, UNDER_WINDOW=5, STRING_RATIO=0.70, WATCH_COLOR=0.75`.
  - `@dataclass OptimizerAnomaly(site_id:int, optimizer_serial:str, label:str|None, string_label:str|None, severity:str, reason:str, latest_day:str|None, latest_energy_wh:float|None, latest_color:float|None, string_median_wh:float|None, ratio_to_string:float|None)`.
  - `analyze_site(site_id, inventory, energy_rows, as_of_day) -> list[OptimizerAnomaly]` — one entry per FLAGGED optimizer, severity ∈ {"dead","underperforming","watch"}; sorted dead→underperforming→watch then worst-first. (Degradation added in Task 3.)

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_analyze.py`:

```python
from solaranalysis.optimizers.analyze import analyze_site, OptimizerAnomaly


def _inv(*serials, string="1.0"):
    return [{"optimizer_serial": s, "label": f"1.0.{i+1}", "string_label": string}
            for i, s in enumerate(serials)]


def _rows(serial, days_vals):
    # days_vals: list of (day, energy_wh, color)
    return [{"optimizer_serial": serial, "day": d, "energy_wh": e, "color": c}
            for d, e, c in days_vals]


DAYS = ["2026-07-18", "2026-07-19", "2026-07-20", "2026-07-21", "2026-07-22"]


def test_healthy_optimizers_not_flagged():
    inv = _inv("A", "B", "C")
    rows = []
    for s in ("A", "B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    assert analyze_site(1, inv, rows, "2026-07-22") == []


def test_dead_optimizer_flagged():
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:3]]
                 + [("2026-07-21", 0.0, 0.0), ("2026-07-22", 0.0, 0.0)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "dead"
    assert "B" not in a and "C" not in a


def test_underperformer_by_color_persistence():
    inv = _inv("A", "B", "C")
    # A: color below 0.60 on 3 of last 5 days
    rows = _rows("A", [("2026-07-18", 5000.0, 0.97), ("2026-07-19", 2000.0, 0.50),
                       ("2026-07-20", 2000.0, 0.45), ("2026-07-21", 2000.0, 0.55),
                       ("2026-07-22", 5000.0, 0.97)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "underperforming"
    assert "color" in a["A"].reason


def test_underperformer_by_string_median():
    inv = _inv("A", "B", "C")  # same string 1.0
    # A produces ~50% of peers on 3+ of last 5 days but color stays okay
    rows = _rows("A", [(d, 2500.0, 0.80) for d in DAYS])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "underperforming"
    assert a["A"].ratio_to_string is not None and a["A"].ratio_to_string < 0.7


def test_watch_single_day_dip():
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:4]] + [("2026-07-22", 4000.0, 0.70)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "watch"  # single-day color<0.75, not persistent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_analyze.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.analyze'`.

- [ ] **Step 3: Implement the analyzer**

Create `solaranalysis/optimizers/analyze.py`:

```python
"""Pure per-optimizer anomaly detection over the accumulated daily series.

No IO/clock: takes loaded inventory + energy rows and an explicit as_of_day.
SolarEdge's `color` (0..1) is its own peer-normalized performance value, so it
is the primary signal; string-median energy and multi-day persistence back it
up (a one-day dip is weather, a multi-day low is a fault)."""
from __future__ import annotations
import statistics
from dataclasses import dataclass

DEAD_COLOR = 0.05        # color at/below this ~ not producing
DEAD_DAYS = 2            # consecutive most-recent dead days -> dead
UNDER_COLOR = 0.60       # color below this counts as an underperforming day
UNDER_MIN_DAYS = 3       # this many bad days within the window -> underperforming
UNDER_WINDOW = 5         # "last N days" window
STRING_RATIO = 0.70      # energy below this fraction of string-median = bad day
WATCH_COLOR = 0.75       # latest-day color below this (not persistent) -> watch

_SEVERITY_RANK = {"dead": 0, "underperforming": 1, "degrading": 2, "watch": 3}


@dataclass
class OptimizerAnomaly:
    site_id: int
    optimizer_serial: str
    label: str | None
    string_label: str | None
    severity: str
    reason: str
    latest_day: str | None
    latest_energy_wh: float | None
    latest_color: float | None
    string_median_wh: float | None
    ratio_to_string: float | None


def _series_by_serial(energy_rows):
    out: dict[str, dict[str, dict]] = {}
    for r in energy_rows:
        out.setdefault(r["optimizer_serial"], {})[r["day"]] = r
    return out


def _string_median_by_day(inventory, series):
    """{day: {string_label: median energy_wh over that string's optimizers}}."""
    string_of = {i["optimizer_serial"]: i.get("string_label") for i in inventory}
    per: dict[str, dict[str, list]] = {}
    for serial, byday in series.items():
        sl = string_of.get(serial)
        for day, row in byday.items():
            e = row.get("energy_wh")
            if e is not None:
                per.setdefault(day, {}).setdefault(sl, []).append(e)
    return {day: {sl: statistics.median(v) for sl, v in slmap.items() if v}
            for day, slmap in per.items()}


def analyze_site(site_id, inventory, energy_rows, as_of_day) -> list[OptimizerAnomaly]:
    series = _series_by_serial(energy_rows)
    string_med = _string_median_by_day(inventory, series)
    all_days = sorted({r["day"] for r in energy_rows if r["day"] <= as_of_day})
    recent = all_days[-UNDER_WINDOW:]
    out: list[OptimizerAnomaly] = []

    for inv in inventory:
        serial = inv["optimizer_serial"]
        byday = series.get(serial, {})
        if not byday:
            continue
        sl = inv.get("string_label")
        latest_day = max((d for d in byday if d <= as_of_day), default=None)
        latest = byday.get(latest_day, {}) if latest_day else {}
        latest_e = latest.get("energy_wh")
        latest_c = latest.get("color")
        med = string_med.get(latest_day, {}).get(sl) if latest_day else None
        ratio = (latest_e / med) if (latest_e is not None and med) else None

        def anomaly(sev, reason):
            return OptimizerAnomaly(site_id, serial, inv.get("label"), sl, sev,
                                    reason, latest_day, latest_e, latest_c, med, ratio)

        # Dead: the most-recent DEAD_DAYS days are all ~zero (color or energy).
        last_days = all_days[-DEAD_DAYS:]
        if len(last_days) == DEAD_DAYS and all(
                (byday.get(d, {}).get("color") is not None
                 and byday[d]["color"] <= DEAD_COLOR)
                or (byday.get(d, {}).get("energy_wh") == 0.0)
                for d in last_days):
            out.append(anomaly("dead", f"~zero output for {DEAD_DAYS} days"))
            continue

        # Underperforming: color low on >=UNDER_MIN_DAYS of recent, OR energy
        # below STRING_RATIO x string-median on >=UNDER_MIN_DAYS of recent.
        color_bad = sum(1 for d in recent
                        if (byday.get(d, {}).get("color") is not None
                            and byday[d]["color"] < UNDER_COLOR))
        ratio_bad = 0
        for d in recent:
            e = byday.get(d, {}).get("energy_wh")
            m = string_med.get(d, {}).get(sl)
            if e is not None and m and e < STRING_RATIO * m:
                ratio_bad += 1
        if color_bad >= UNDER_MIN_DAYS:
            out.append(anomaly("underperforming",
                               f"color<{UNDER_COLOR} on {color_bad}/{len(recent)} days"))
            continue
        if ratio_bad >= UNDER_MIN_DAYS:
            out.append(anomaly("underperforming",
                               f"below {int(STRING_RATIO*100)}% of string median on "
                               f"{ratio_bad}/{len(recent)} days"))
            continue

        # Watch: latest-day color dip, not persistent.
        if latest_c is not None and latest_c < WATCH_COLOR:
            out.append(anomaly("watch", f"latest-day color {latest_c:.2f}"))

    out.sort(key=lambda a: (_SEVERITY_RANK.get(a.severity, 9),
                            a.ratio_to_string if a.ratio_to_string is not None else 1.0,
                            a.latest_color if a.latest_color is not None else 1.0))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_analyze.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/analyze.py tests/optimizers/test_analyze.py
git commit -m "feat: per-optimizer anomaly detection (dead/underperforming/watch)"
```

---

### Task 3: Analyze — degradation trend

**Files:**
- Modify: `solaranalysis/optimizers/analyze.py`
- Test: `tests/optimizers/test_analyze.py` (add)

**Interfaces:**
- Consumes: the Task 2 machinery.
- Produces: constants `DEGRADE_MIN_HISTORY=14, DEGRADE_RECENT=7, DEGRADE_DROP=0.15`; `analyze_site` now also flags `severity="degrading"` for an optimizer whose recent-vs-prior 7-day energy ratio is materially (≥ DEGRADE_DROP) below its string's same-window ratio — but only when ≥ DEGRADE_MIN_HISTORY days of history exist, and only if not already flagged dead/underperforming. Degrading ranks below underperforming, above watch.

- [ ] **Step 1: Write the failing test**

Add to `tests/optimizers/test_analyze.py`:

```python
def _n_days(n, end="2026-07-22"):
    from datetime import date, timedelta
    e = date.fromisoformat(end)
    return [(e - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def test_degrading_optimizer_flagged_when_declining_faster_than_string():
    inv = _inv("A", "B", "C")
    days = _n_days(14)
    rows = []
    # B, C: flat ~5000 across all 14 days (string baseline stable)
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.95) for d in days])
    # A: prior 7 days ~5000, recent 7 days ~3800 (24% drop) while peers flat,
    #    color stays >=0.75 so it is not caught as underperforming/watch.
    a_vals = [(d, 5000.0, 0.95) for d in days[:7]] + [(d, 3800.0, 0.95) for d in days[7:]]
    rows += _rows("A", a_vals)
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert "A" in out and out["A"].severity == "degrading"
    assert "B" not in out and "C" not in out


def test_no_degrading_without_enough_history():
    inv = _inv("A", "B")
    days = _n_days(6)  # < DEGRADE_MIN_HISTORY
    rows = _rows("A", [(d, 5000.0, 0.95) for d in days[:3]] + [(d, 3000.0, 0.95) for d in days[3:]])
    rows += _rows("B", [(d, 5000.0, 0.95) for d in days])
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert "A" not in out  # not enough history to judge a trend
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_analyze.py -v -k degrading`
Expected: FAIL — the degrading tests fail (no such severity emitted yet).

- [ ] **Step 3: Implement degradation**

In `solaranalysis/optimizers/analyze.py`, add the constants near the others:

```python
DEGRADE_MIN_HISTORY = 14  # need at least this many days to judge a trend
DEGRADE_RECENT = 7        # compare last 7 days vs the prior 7
DEGRADE_DROP = 0.15       # optimizer's recent/prior ratio this far below its string's
```

Add this helper above `analyze_site`:

```python
def _mean(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _window_ratio(byday, days_recent, days_prior):
    r = _mean([byday.get(d, {}).get("energy_wh") for d in days_recent])
    p = _mean([byday.get(d, {}).get("energy_wh") for d in days_prior])
    return (r / p) if (r is not None and p) else None
```

In `analyze_site`, after the `watch` block (before the loop's end), add a degradation check that runs only when not already flagged. Restructure the tail of the per-optimizer loop so the checks are mutually exclusive; replace the `# Watch:` block with:

```python
        # Degrading: declining faster than its string peers over two 7-day
        # windows (needs enough history). Checked before watch.
        if len(all_days) >= DEGRADE_MIN_HISTORY:
            recent7 = all_days[-DEGRADE_RECENT:]
            prior7 = all_days[-2 * DEGRADE_RECENT:-DEGRADE_RECENT]
            opt_ratio = _window_ratio(byday, recent7, prior7)
            # string baseline: median of peer optimizers' own window-ratios
            peers = [s for s in series
                     if s != serial and _string_of(inventory).get(s) == sl]
            peer_ratios = [pr for pr in
                           (_window_ratio(series[s], recent7, prior7) for s in peers)
                           if pr is not None]
            base = statistics.median(peer_ratios) if peer_ratios else None
            if (opt_ratio is not None and base
                    and opt_ratio < base * (1 - DEGRADE_DROP)):
                out.append(anomaly("degrading",
                    f"7-day output ratio {opt_ratio:.2f} vs string {base:.2f}"))
                continue

        # Watch: latest-day color dip, not persistent.
        if latest_c is not None and latest_c < WATCH_COLOR:
            out.append(anomaly("watch", f"latest-day color {latest_c:.2f}"))
```

Add the small helper `_string_of` near `_string_median_by_day`:

```python
def _string_of(inventory):
    return {i["optimizer_serial"]: i.get("string_label") for i in inventory}
```

(You may refactor `_string_median_by_day` to reuse `_string_of` — optional.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_analyze.py -v`
Expected: PASS (Task 2 tests still green + the 2 degrading tests).

- [ ] **Step 5: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/analyze.py tests/optimizers/test_analyze.py
git commit -m "feat: optimizer degradation-trend detection"
```

---

### Task 4: Report — grounded data block + Claude narrative

**Files:**
- Create: `solaranalysis/optimizers/report.py`
- Test: `tests/optimizers/test_report.py`

**Interfaces:**
- Consumes: `OptimizerAnomaly` (Task 2).
- Produces:
  - `build_anomaly_block(analyses: dict[int, list[OptimizerAnomaly]], site_names: dict[int,str] | None = None) -> str` — an authoritative text block (per-site flagged counts by severity + each flagged optimizer's label/serial/energy/color/ratio/severity/reason). Every figure the narrative may cite lives here.
  - `narrate(block: str, lang: str, client=None) -> str` — grounded Claude call (Opus, effort xhigh, adaptive thinking, injectable `client`) returning a short narrative; mirrors `core.analyze.summarize_executive`.

- [ ] **Step 1: Write the failing test**

Create `tests/optimizers/test_report.py`:

```python
from solaranalysis.optimizers.analyze import OptimizerAnomaly
from solaranalysis.optimizers import report


def _anom(serial, sev, ratio=0.5):
    return OptimizerAnomaly(1, serial, f"1.4.{serial[-1]}", "1.4", sev,
                            "reason", "2026-07-22", 2500.0, 0.55, 5000.0, ratio)


def test_build_anomaly_block_contains_grounded_figures():
    analyses = {1: [_anom("A", "dead"), _anom("B", "underperforming")], 2: []}
    block = report.build_anomaly_block(analyses, site_names={1: "Baram", 2: "Golan"})
    assert "Baram" in block and "Golan" in block
    assert "dead" in block and "underperforming" in block
    assert "136" not in block  # (no fake serials) — our serials are A/B here
    assert "A" in block and "2500" in block  # energy figure present


class FakeClient:
    def __init__(self):
        self.kwargs = None
    class _Block:
        type = "text"
        text = "Two optimizers need attention."
    class _Msg:
        content = [None]
    def messages(self):  # pragma: no cover
        ...
    class messages:  # noqa: shadow to mimic anthropic client.messages.create
        @staticmethod
        def create(**kwargs):
            FakeClient._last_kwargs = kwargs
            m = type("M", (), {})()
            b = type("B", (), {"type": "text", "text": "Two optimizers need attention."})()
            m.content = [b]
            return m


def test_narrate_uses_client_and_returns_text():
    out = report.narrate("=== DATA ===\nsite 1: 2 flagged", lang="English",
                         client=FakeClient())
    assert out.strip() == "Two optimizers need attention."
    assert FakeClient._last_kwargs["model"] == "claude-opus-4-8"


def test_narrate_empty_when_nothing_flagged_block():
    # A block with zero flagged still returns whatever the client says; the
    # caller decides whether to include it. Just verify no crash on empty-ish input.
    out = report.narrate("=== DATA ===\nno anomalies", lang="Hebrew", client=FakeClient())
    assert isinstance(out, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.optimizers.report'`.

- [ ] **Step 3: Implement the block + narrate**

Create `solaranalysis/optimizers/report.py`:

```python
"""Grounded optimizer anomaly report: Python computes every figure; the Claude
call writes narrative prose only (mirrors core.analyze.summarize_executive)."""
from __future__ import annotations
import os

_SEVERITY_ORDER = ("dead", "underperforming", "degrading", "watch")


def _site_label(site_id, site_names):
    name = (site_names or {}).get(site_id)
    return f"{name} (site {site_id})" if name else f"site {site_id}"


def build_anomaly_block(analyses, site_names=None) -> str:
    parts = ["=== OPTIMIZER ANOMALIES (authoritative; do not go beyond it) ==="]
    for site_id in sorted(analyses):
        anoms = analyses[site_id]
        counts = {s: sum(1 for a in anoms if a.severity == s) for s in _SEVERITY_ORDER}
        counts_str = ", ".join(f"{s}={counts[s]}" for s in _SEVERITY_ORDER)
        parts.append(f"\n--- {_site_label(site_id, site_names)} --- "
                     f"flagged={len(anoms)} ({counts_str})")
        for a in anoms:
            parts.append(
                f"  [{a.severity}] optimizer {a.label or a.optimizer_serial} "
                f"(S/N {a.optimizer_serial}, string {a.string_label}): "
                f"energy={a.latest_energy_wh} Wh, color={a.latest_color}, "
                f"string_median={a.string_median_wh} Wh, "
                f"ratio_to_string={a.ratio_to_string}; {a.reason}")
    return "\n".join(parts)


_SYSTEM = (
    "You are a solar-fleet analyst. You are given an authoritative block of "
    "per-optimizer anomaly findings that were computed in Python. Write a short "
    "operator-facing narrative that summarizes the fleet's optimizer health and "
    "calls out the most urgent optimizers to check. Use ONLY the figures in the "
    "block — never invent numbers or serials. Be concise.")


def narrate(block: str, lang: str, client=None) -> str:
    """Grounded narrative for the anomaly block. `client` injectable for tests."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user = block + f"\n\nWrite the narrative in {lang}. Base every figure on the block above."
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/report.py tests/optimizers/test_report.py
git commit -m "feat: grounded optimizer anomaly data block + narrative"
```

---

### Task 5: Report — markdown assembly, subject, recipients

**Files:**
- Modify: `solaranalysis/optimizers/report.py`
- Test: `tests/optimizers/test_report.py` (add)

**Interfaces:**
- Produces:
  - `render_report_md(analyses, narrative, as_of_day, site_names=None) -> str` — markdown: an `## SolarEdge Optimizers — {as_of_day}` heading, the narrative (if any), then per-site markdown tables of flagged optimizers (columns: Severity, Optimizer, S/N, String, Energy Wh, Color, Ratio, Reason). Sites with zero flagged render a one-line "all clear".
  - `subject(as_of_day, total_flagged) -> str` — e.g. `SolarEdge Optimizers · 3 flagged · 2026-07-22`.
  - `resolve_recipients() -> list[str]` — `OPTIMIZER_RECIPIENTS` (comma-separated) if set, else `web.mailer.recipients()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/optimizers/test_report.py`:

```python
def test_render_report_md_has_narrative_and_tables():
    analyses = {1: [_anom("A", "dead")], 2: []}
    md = report.render_report_md(analyses, "NARRATIVE HERE", "2026-07-22",
                                 site_names={1: "Baram", 2: "Golan"})
    assert "SolarEdge Optimizers" in md and "2026-07-22" in md
    assert "NARRATIVE HERE" in md
    assert "| Severity |" in md            # a markdown table header
    assert "dead" in md and "Baram" in md
    assert "Golan" in md and "all clear" in md.lower()  # empty site


def test_render_report_md_without_narrative():
    md = report.render_report_md({1: [_anom("A", "watch")]}, None, "2026-07-22")
    assert "SolarEdge Optimizers" in md
    assert "watch" in md


def test_subject_counts_flagged():
    assert report.subject("2026-07-22", 3) == "SolarEdge Optimizers · 3 flagged · 2026-07-22"


def test_resolve_recipients_prefers_optimizer_env(monkeypatch):
    monkeypatch.setenv("OPTIMIZER_RECIPIENTS", "a@x.com, b@x.com")
    assert report.resolve_recipients() == ["a@x.com", "b@x.com"]


def test_resolve_recipients_falls_back_to_report_recipients(monkeypatch):
    monkeypatch.delenv("OPTIMIZER_RECIPIENTS", raising=False)
    monkeypatch.setenv("REPORT_RECIPIENTS", "c@x.com")
    assert report.resolve_recipients() == ["c@x.com"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_report.py -v -k "render_report or subject or recipients"`
Expected: FAIL — those attributes don't exist yet.

- [ ] **Step 3: Implement assembly/subject/recipients**

Append to `solaranalysis/optimizers/report.py`:

```python
from ..web import mailer


def subject(as_of_day: str, total_flagged: int) -> str:
    return f"SolarEdge Optimizers · {total_flagged} flagged · {as_of_day}"


def resolve_recipients() -> list[str]:
    raw = os.getenv("OPTIMIZER_RECIPIENTS", "").strip()
    if raw:
        out, seen = [], set()
        for part in raw.split(","):
            a = part.strip()
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out
    return mailer.recipients()


def _table(anoms) -> str:
    rows = ["| Severity | Optimizer | S/N | String | Energy Wh | Color | Ratio | Reason |",
            "|---|---|---|---|---|---|---|---|"]
    for a in anoms:
        color = "" if a.latest_color is None else f"{a.latest_color:.2f}"
        ratio = "" if a.ratio_to_string is None else f"{a.ratio_to_string:.2f}"
        energy = "" if a.latest_energy_wh is None else f"{a.latest_energy_wh:.0f}"
        rows.append(f"| {a.severity} | {a.label or ''} | {a.optimizer_serial} | "
                    f"{a.string_label or ''} | {energy} | {color} | {ratio} | {a.reason} |")
    return "\n".join(rows)


def render_report_md(analyses, narrative, as_of_day, site_names=None) -> str:
    parts = [f"## SolarEdge Optimizers — {as_of_day}", ""]
    if narrative:
        parts += [narrative.strip(), ""]
    for site_id in sorted(analyses):
        anoms = analyses[site_id]
        parts.append(f"### {_site_label(site_id, site_names)}")
        parts.append("")
        if anoms:
            parts.append(_table(anoms))
        else:
            parts.append("_all clear — no optimizers flagged._")
        parts.append("")
    return "\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_report.py -v`
Expected: PASS (all report tests).

- [ ] **Step 5: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/report.py tests/optimizers/test_report.py
git commit -m "feat: optimizer report markdown + subject + recipient resolution"
```

---

### Task 6: CLI wiring — analyze → report → email

**Files:**
- Modify: `solaranalysis/optimizers/cli.py`
- Test: `tests/optimizers/test_cli.py` (add)

**Interfaces:**
- Consumes: `store.load_inventory`/`load_energy_window` (B1 + Task 1), `analyze.analyze_site` (Tasks 2-3), `report.*` (Tasks 4-5), `core.report.render_email_html`, `web.mailer`, `web.repo.get_app_settings` (for language).
- Produces: after collecting, `main` loads each site's inventory + a ~30-day energy window, runs `analyze_site` (`as_of_day = days[-1]`), builds the report, gets a grounded narrative (non-fatal), renders email HTML, and emails it (unless `--no-email`, or mailer unconfigured / no recipients). New flag `--no-email`. New constant `ANALYSIS_WINDOW_DAYS = 30`.

- [ ] **Step 1: Write the failing test**

Add to `tests/optimizers/test_cli.py`:

```python
def test_analysis_window_days_constant():
    from solaranalysis.optimizers import cli
    assert cli.ANALYSIS_WINDOW_DAYS >= 14  # enough for the degradation trend


def test_cli_has_no_email_flag():
    import argparse
    from solaranalysis.optimizers import cli
    # The parser must accept --no-email without error.
    parsed = cli._build_parser().parse_args(
        ["--data-dir", "d", "--app-dir", "a", "--no-email"])
    assert parsed.no_email is True
```

(This task introduces `cli._build_parser()` so the parser is unit-addressable; `main` uses it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_cli.py -v -k "window or no_email"`
Expected: FAIL — `ANALYSIS_WINDOW_DAYS` / `_build_parser` don't exist.

- [ ] **Step 3: Implement the wiring**

In `solaranalysis/optimizers/cli.py`:

Add imports at the top (with the existing ones):

```python
from ..core.report import render_email_html
from ..web import mailer
from . import analyze, report
```

Add the constant near the top (after imports):

```python
ANALYSIS_WINDOW_DAYS = 30
```

Extract the parser into `_build_parser()` and add `--no-email`:

```python
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="solaranalysis.optimizers")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--date")
    ap.add_argument("--backfill", type=int, default=1)
    ap.add_argument("--sites")
    ap.add_argument("--no-email", action="store_true")
    return ap
```

In `main`, replace `args = ap.parse_args(...)` construction with `args = _build_parser().parse_args(argv)` (remove the inline `ap = argparse...` block).

After the collection block (`results = collector.collect(...)`) and the existing empty-`site_ids` guard, add the analyze → report → email flow before the per-site print loop:

```python
    # --- analyze the accumulated series + email an anomaly report ---
    from datetime import date as _date
    as_of = days[-1]
    since = collector.day_range(_date.fromisoformat(as_of), ANALYSIS_WINDOW_DAYS)[0]
    site_names = {r["site_id"]: r.get("name") for r in []}  # names not fetched here
    analyses = {}
    for sid in site_ids:
        inv = store.load_inventory(conn, sid)
        rows = store.load_energy_window(conn, sid, since)
        analyses[sid] = analyze.analyze_site(sid, inv, rows, as_of)
    total_flagged = sum(len(v) for v in analyses.values())

    lang = "Hebrew" if repo.get_app_settings(conn).get("output_language") == "he" else "English"
    block = report.build_anomaly_block(analyses)
    narrative = None
    try:
        narrative = report.narrate(block, lang)
    except Exception as e:
        print(f"narrative skipped: {e}")
    md = report.render_report_md(analyses, narrative, as_of)

    if args.no_email:
        print(f"analysis complete: {total_flagged} optimizer(s) flagged (email skipped)")
    elif mailer.is_configured() and report.resolve_recipients():
        try:
            html = render_email_html(md, "SolarEdge Optimizers",
                                     f"{total_flagged} flagged · {as_of}")
            mailer.send_report(report.subject(as_of, total_flagged), html)
            print(f"emailed optimizer report: {total_flagged} flagged")
        except Exception as e:
            print(f"email failed: {e}")
    else:
        print(f"analysis complete: {total_flagged} flagged (email not configured)")
```

Keep the existing per-site collection summary print loop and `conn.close(); return 0` at the end.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/optimizers/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Sanity-check the CLI imports/wire and run the full suite**

Run: `.venv/Scripts/python.exe -m solaranalysis.optimizers --help`
Expected: usage text including `--no-email` (confirms imports resolve).
Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior + new tests). Report the count.

- [ ] **Step 6: Commit**

```bash
export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"
git add solaranalysis/optimizers/cli.py tests/optimizers/test_cli.py
git commit -m "feat: run optimizer analysis + email anomaly report from the CLI"
```

---

### Task 7 (ops, no unit test): daily schedule + live verification

Not a code change — do this on the deployed server with real credentials.

- [ ] Add a daily scheduled task (NSSM service or Windows Task Scheduler) that runs `python -m solaranalysis.optimizers --data-dir <data> --app-dir <app>` after the fleet run's window (e.g. 06:30 Asia/Jerusalem). Document it in `DEPLOYMENT.md`.
- [ ] Confirm the anomaly email arrives, its figures match a spot-check of the portal's Digital-Twin panel for one flagged optimizer, and `--no-email` prints the flagged count without sending.

---

## Self-Review

**Spec coverage** (`specs/2026-07-23-optimizer-collector-design.md`, B2 portion §5–§7):
- §5 analysis: `color` threshold + string-median + persistence (Task 2), degradation trend (Task 3), all constants top-of-module + Python-computed. ✓
- §6 report: grounded data block + Claude narrative (Task 4), markdown + email-safe HTML via `render_email_html`, emailed via `mailer.send_report` with own subject + `OPTIMIZER_RECIPIENTS` override, non-fatal narrative (Tasks 5-6). ✓
- §7 CLI runs analyze+report+email after collect; `--no-email`; own daily schedule (Task 7, ops). ✓
- Grounding guarantee (numbers in Python, prose from model; non-fatal) → Tasks 4 + 6. ✓
- Deferred/backlog (not this plan): module tilt/azimuth enrichment; a web UI for the series.

**Placeholder scan:** No TBD/TODO; every code step has complete code; every test step shows the test + expected fail/pass. Task 7 is explicitly a manual ops step.

**Type consistency:** `OptimizerAnomaly` (Task 2) fields are consumed identically in `report.build_anomaly_block`/`_table` (Tasks 4-5). `analyze_site(site_id, inventory, energy_rows, as_of_day)` signature matches its tests and the CLI call (Task 6). `analyses` is `dict[int, list[OptimizerAnomaly]]` everywhere (Tasks 4-6). `narrate(block, lang, client=None)`, `build_anomaly_block(analyses, site_names=None)`, `render_report_md(analyses, narrative, as_of_day, site_names=None)`, `subject(as_of_day, total_flagged)`, `resolve_recipients()` match their tests and the CLI. `load_energy_window(conn, site_id, since_day)` (Task 1) matches the CLI call. `_build_parser()` (Task 6) matches its test.
