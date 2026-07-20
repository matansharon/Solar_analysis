# System Status Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open every solar report with a plain-language, per-system verdict — a Hebrew fleet headline plus a ✅/⚠️/❌ traffic-light list telling the operator at a glance whether each system is working correctly.

**Architecture:** A new `status_overview(report_md)` LLM call (Opus 4.8, xhigh) parallel to the existing `summarize_executive`, driven by a new `prompts/status.txt`. Rendered above the executive summary in `report.html` via `prepend_status()`, and above the summary in the emailed dashboard via a new optional `status_md` parameter on `compose_dashboard`. Wired into both call sites (`web/runner.py`, `cli.py`) as a non-fatal step.

**Tech Stack:** Python 3.10, `anthropic` SDK 0.116.0, `markdown` (with `md_in_html`), pytest. Windows dev machine — run Python as `python` (not `python3`).

**Spec:** `specs/2026-07-20-system-status-overview-design.md`

## Global Constraints

- **Python invocation:** `python` (the `python3` alias is broken on this machine). Full path if needed: `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`.
- **LLM request knobs (Opus 4.8):** `model="claude-opus-4-8"`, `max_tokens=16000`, `thinking={"type": "adaptive"}`, `output_config={"effort": "xhigh"}`. NEVER send `temperature`/`top_p`/`top_k` or `thinking.budget_tokens` — they return HTTP 400 on Opus 4.8.
- **No new dependencies, no new secrets.** Reuse the existing `anthropic` SDK / `ANTHROPIC_API_KEY`.
- **Language:** the status block is always Hebrew (like the executive summary); not gated on `cfg.output_language`.
- **Grounding untouched:** the status distills only what the report already states. `verify_numbers` still runs on the clean `res["report_md"]` only — do not wire the status into it.
- **Non-fatal:** every new step is wrapped in try/except at the call site; a failure logs a note and the run/email still completes.
- **Commits:** clean commit messages, NO AI-attribution / `Co-Authored-By` trailer (matches this repo's history and the `clean-commits` skill).
- **Tests never hit the network:** LLM clients are always injected/monkeypatched in tests.

---

## Task 1: `status_overview` function + `status.txt` prompt

**Files:**
- Create: `solaranalysis/prompts/status.txt`
- Modify: `solaranalysis/core/analyze.py` (add path constant + `_status_prompt()` + `status_overview()` next to the exec-summary equivalents, ~lines 13-14, 127-128, 167-189)
- Test: `tests/test_analyze.py`

**Interfaces:**
- Consumes: nothing new (reuses the module's existing `_ensure_list_breaks`).
- Produces: `status_overview(report_md: str, client=None) -> str` — returns Hebrew markdown (fleet headline line + ✅/⚠️/❌ bullet list). Later tasks import it into `report.py` call sites, `cli.py`, and `web/runner.py`.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_analyze.py` (the `_HebrewClient`, `_TextClient`, and `status_overview` import will be reused/added):

```python
from solaranalysis.core.analyze import status_overview


def test_status_overview_uses_injected_client():
    out = status_overview("## Health & Faults\nPlant A: inverter fault.",
                          client=_HebrewClient())
    assert out == "**סיכום:** התחנה המובילה תקינה."


def test_status_overview_reuses_list_break_normalization():
    # A headline line immediately followed by bullets must be separated by a
    # blank line, exactly like the executive summary (shared _ensure_list_breaks).
    raw = "סטטוס כללי: 2 מערכות\n- ✅ א' — תקין\n- ❌ ב' — תקלה"
    out = status_overview("report", client=_TextClient(raw))
    assert "סטטוס כללי: 2 מערכות\n\n- ✅ א' — תקין" in out


def test_status_overview_request_shape():
    report_md = "## Health & Faults\nPlant A inverter offline."
    client = _HebrewClient()
    status_overview(report_md, client=client)
    kw = client.kwargs
    assert kw["model"] == "claude-opus-4-8"
    assert kw["output_config"] == {"effort": "xhigh"}
    assert kw["thinking"] == {"type": "adaptive"}
    # The report being judged is handed to the model.
    assert report_md in kw["messages"][0]["content"]
    # Sampling params 400 on Opus 4.8 — they must not be sent.
    assert "temperature" not in kw and "top_p" not in kw and "top_k" not in kw
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_analyze.py -k status_overview -v`
Expected: FAIL with `ImportError: cannot import name 'status_overview'`.

- [ ] **Step 3: Create the prompt file `solaranalysis/prompts/status.txt`**

```
You write a brief SYSTEM-STATUS OVERVIEW ("סטטוס מערכות") of a solar-PV fleet comparison report, for an O&M operator. Write it in HEBREW. It answers one question at a glance: for each system, is it working correctly or not?

You are given the full report below (it may end with an "Unavailable Plants" section). Judge each system's status from the FACTS in the report only — device statuses (online/offline/fault/standby), alert severities and counts, production figures, data-quality flags, and the unavailable section. Do not invent a fault, a number, or a system.

Assign each system exactly one state:
- ✅ תקין — no faults, no error/critical alerts, producing normally.
- ⚠️ דורשת תשומת לב — degraded: low output vs. peers or expected, warning-level alerts, standby devices, or only partial data.
- ❌ תקלה — an offline or faulted inverter, an error/critical alert, zero output when output is expected, OR data that could not be fetched (any system under "Unavailable Plants").
When the evidence is ambiguous between two states, choose the more cautious (worse) one.

OUTPUT (markdown, Hebrew):
1. A single headline line FIRST: "סטטוס כללי: N מערכות — X תקינות, Y דורשות תשומת לב, Z בתקלה". Include only the states that actually occur (omit a count that is zero).
2. Then ONE blank line, then a bullet list — one bullet per system, in this exact shape: "- <emoji> **<system name>** — <short reason>". The emoji is ✅ / ⚠️ / ❌. The reason is a few words, not a sentence.

COVERAGE: include EVERY system named in the report, including every system under "Unavailable Plants" (mark those ❌ with the reason "לא ניתן לאחזר נתונים").

FORMAT rules:
- Do NOT emit a top-level H1/H2 heading — this block is inserted under an existing "סטטוס מערכות" heading.
- Keep the headline line and the bullets separated by a blank line, and keep every line short and scannable.
- Bidirectional text: wrap EVERY embedded Latin or numeric run — plant names in English, values with units, IDs, dates — between a pair of U+200E LEFT-TO-RIGHT MARK characters so numbers and punctuation do not scramble in the RTL text.
- Refer to each system by its display name only. No LaTeX.
```

- [ ] **Step 4: Add the prompt-path constant + loader in `solaranalysis/core/analyze.py`**

After the existing `_EXEC_SUMMARY_PROMPT_PATH` block (currently lines 13-14):

```python
_STATUS_PROMPT_PATH = (Path(__file__).resolve().parent.parent
                       / "prompts" / "status.txt")
```

After the existing `_exec_summary_prompt()` (currently lines 127-128):

```python
def _status_prompt() -> str:
    return _STATUS_PROMPT_PATH.read_text(encoding="utf-8")
```

- [ ] **Step 5: Add `status_overview` at the end of `solaranalysis/core/analyze.py`**

```python
def status_overview(report_md: str, client=None) -> str:
    """Produce a brief Hebrew system-status overview ("סטטוס מערכות") of an
    already-generated report, as markdown: a one-line fleet headline followed by
    a traffic-light list (✅/⚠️/❌) — one line per system, with a short reason.
    Claude judges each system's state from the facts in the report only.

    Uses Claude Opus 4.8 at "xhigh" reasoning — effort="xhigh" plus adaptive
    thinking (the fixed thinking.budget_tokens knob is rejected there). `client`
    is injectable for tests, mirroring summarize_executive."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user = report_md + "\n\nכתוב סקירת סטטוס מערכות תמציתית בעברית של הדוח שלמעלה."
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _status_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    return _ensure_list_breaks(text)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_analyze.py -k status_overview -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/prompts/status.txt solaranalysis/core/analyze.py tests/test_analyze.py
git commit -m "feat: status_overview — grounded Hebrew per-system status verdict (Opus 4.8 xhigh)"
```

---

## Task 2: `prepend_status` renderer (report.py)

**Files:**
- Modify: `solaranalysis/core/report.py` (add `prepend_status` after `prepend_summary`, ~line 125)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: nothing (pure string function).
- Produces: `prepend_status(report_md: str, status_md: str) -> str` — wraps `status_md` in a `<div dir="rtl" markdown="1">` block under a `## סטטוס מערכות` heading, prepended above `report_md` with a `---` rule. Later tasks (`cli.py`, `web/runner.py`) call it on the summary-prepended document so the final order is status → summary → report.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_report.py`:

```python
from solaranalysis.core.report import prepend_status


def test_prepend_status_places_status_first_rtl():
    out = prepend_status("## סיכום מנהלים\n\nהכל תקין.\n\n---\n\nMain body.",
                         "סטטוס כללי: מערכת אחת — תקינה\n\n- ✅ **א'** — תקין")
    assert 'dir="rtl"' in out
    assert "סטטוס מערכות" in out
    # Status precedes both the executive summary and the detailed report body.
    assert out.index("סטטוס מערכות") < out.index("סיכום מנהלים")
    assert out.index("סטטוס כללי") < out.index("Main body")


def test_render_html_renders_status_summary_report_in_order():
    doc = prepend_status(
        prepend_summary("## Production & Performance\n\nMain body.",
                        "**סיכום:** תקין."),
        "- ✅ **א'** — תקין")
    html = render_html(doc, "T", "S")
    # Both RTL headings render (md_in_html active) and order is preserved.
    assert 'dir="rtl"' in html
    assert "<h2" in html and "סטטוס מערכות" in html
    assert "<li>✅ <strong>א'</strong> — תקין</li>" in html
    assert html.index("סטטוס מערכות") < html.index("סיכום מנהלים") < html.index("Main body")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_report.py -k status -v`
Expected: FAIL with `ImportError: cannot import name 'prepend_status'`.

- [ ] **Step 3: Add `prepend_status` to `solaranalysis/core/report.py`**

Append after `prepend_summary` (end of file):

```python
def prepend_status(report_md: str, status_md: str) -> str:
    """Put a Hebrew system-status overview ("סטטוס מערכות") at the very top of
    the report, in a right-to-left block, above the executive summary and the
    detailed analysis. Mirrors prepend_summary; the `markdown="1"` div + blank
    line lets the md_in_html extension render the inner markdown."""
    return ('<div dir="rtl" markdown="1">\n\n'
            '## סטטוס מערכות\n\n'
            f'{status_md}\n\n'
            '</div>\n\n---\n\n' + report_md)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_report.py -k status -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/core/report.py tests/test_report.py
git commit -m "feat: prepend_status — RTL system-status block above the exec summary"
```

---

## Task 3: `compose_dashboard` renders the status above the summary

**Files:**
- Modify: `solaranalysis/core/dashboard.py` (add `_status_html`, add `status_md` param, update `summary_html` assembly and the preheader, ~lines 39-113)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `report._inline_email_styles` (already imported via `from . import report`).
- Produces: `compose_dashboard(summary_md, charts_html, client=None, date_str=None, status_md=None)` — when `status_md` is given, its rendered RTL HTML precedes the summary HTML in the `{{SUMMARY}}` slot, and the hidden preheader is built from `status_md` instead of `summary_md`. Backward compatible: `status_md=None` reproduces today's output.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_dashboard.py`:

```python
def test_compose_dashboard_renders_status_above_summary():
    out = compose_dashboard("**סיכום מנהלים**", "<table>BARCHART</table>",
                            client=_ShellClient(_SHELL),
                            status_md="- ✅ **א'** — תקין")
    assert "תקין" in out                       # status content present
    # Status HTML comes before the summary HTML inside the body.
    assert out.index("תקין") < out.index("סיכום מנהלים")


def test_compose_dashboard_without_status_is_unchanged():
    out = compose_dashboard("**סיכום מנהלים**", "<table>BARCHART</table>",
                            client=_ShellClient(_SHELL))
    assert "סיכום מנהלים" in out and "BARCHART" in out
    # No status list marker leaks in when status_md is omitted.
    assert "✅" not in out


def test_compose_dashboard_preheader_prefers_status():
    out = compose_dashboard("**סיכום** ראשון.", "<b>c</b>",
                            client=_ShellClient(_SHELL),
                            status_md="סטטוס כללי: 2 מערכות תקינות\n\n- ✅ **א'** — תקין")
    pre = out[:out.index("<main>")]
    assert "סטטוס כללי: 2 מערכות תקינות" in pre   # status headline is the inbox preview
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py -k status -v`
Expected: FAIL — `test_compose_dashboard_renders_status_above_summary` raises `TypeError: ... unexpected keyword argument 'status_md'`.

- [ ] **Step 3: Add `_status_html` to `solaranalysis/core/dashboard.py`**

After `_summary_html` (currently lines 39-44):

```python
def _status_html(status_md: str) -> str:
    """Render the Hebrew status-overview markdown to an email-safe, RTL,
    inline-styled fragment (reuses report.py's email inliner)."""
    body = report._inline_email_styles(
        md.markdown(status_md, extensions=["tables", "fenced_code", "md_in_html"]))
    return f'<div dir="rtl" style="text-align:right;">{body}</div>'
```

- [ ] **Step 4: Add the `status_md` parameter and wire it into `compose_dashboard`**

Change the signature (currently lines 73-74) to add `status_md`:

```python
def compose_dashboard(summary_md: str, charts_html: str, client=None,
                      date_str: str | None = None,
                      status_md: str | None = None) -> str:
```

Replace the `summary_html = _summary_html(summary_md)` line (currently line 84) with:

```python
    summary_html = ((_status_html(status_md) if status_md else "")
                    + _summary_html(summary_md))
```

Replace the final return (currently line 113) so the preheader prefers the status headline:

```python
    return _inject_preheader(html_doc, _preheader(status_md or summary_md))
```

- [ ] **Step 5: Run the new + existing dashboard tests to verify all pass**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: PASS — the 3 new `status` tests plus all pre-existing dashboard tests (the `status_md=None` default keeps them green).

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/core/dashboard.py tests/test_dashboard.py
git commit -m "feat: dashboard renders system-status above summary; status headline as preheader"
```

---

## Task 4: Wire the status into `web/runner.py`

**Files:**
- Modify: `solaranalysis/web/runner.py` (imports line 13-15; the `if res["plants"]:` summary block ~lines 96-104; the `compose_dashboard` call ~lines 122-124)
- Test: `tests/web/test_runner.py` (update the autouse `_stub_llm_calls` fixture; add three tests)

**Interfaces:**
- Consumes: `status_overview` (Task 1), `prepend_status` (Task 2), `compose_dashboard(..., status_md=...)` (Task 3).
- Produces: no new public interface; the emitted note reason `"System status overview added"` / `"status overview skipped: ..."` and the status block in the on-disk report and emailed dashboard.

- [ ] **Step 1: Update the autouse fixture and write the failing tests**

In `tests/web/test_runner.py`, extend the `_stub_llm_calls` fixture (currently lines 7-17) to also stub `status_overview` (so the real network call is never made):

```python
    monkeypatch.setattr(runner, "status_overview",
                        lambda report_md: "- ✅ **Good** — תקין", raising=False)
```

Then add these tests (they reuse `_seed_run`, `_success_pipeline`, and the mailer-stub pattern already in the file):

```python
def test_run_job_notes_status_overview(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    notes = [e.get("reason", "") for e in events if e["event"] == "note"]
    assert any("status overview" in r for r in notes)
    complete = [e for e in events if e["event"] == "run_complete"][0]
    assert complete["status"] == "success"


def test_run_job_status_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(report_md):
        raise RuntimeError("opus down")
    monkeypatch.setattr(runner, "status_overview", boom)

    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "success"
    assert "status overview skipped" in out


def test_run_job_passes_status_to_dashboard(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    monkeypatch.setattr(runner, "design_charts",
                        lambda data_summary: [{"metric": "energy_today",
                                               "title": "E", "insight": "i"}])
    seen = {}
    monkeypatch.setattr(runner, "compose_dashboard",
                        lambda summary_md, charts_html, **kw:
                        (seen.update(kw), "<html><body>D</body></html>")[1])
    runner.run_analysis_job(paths, run_id=1)
    assert seen.get("status_md") == "- ✅ **Good** — תקין"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/web/test_runner.py -k "status" -v`
Expected: FAIL — `test_run_job_notes_status_overview` finds no "status overview" note; `test_run_job_passes_status_to_dashboard` sees `status_md` absent (runner does not pass it yet).

- [ ] **Step 3: Add the imports in `solaranalysis/web/runner.py`**

Update the two `..core` imports (currently lines 13-15):

```python
from ..core.report import (render_html, render_email_html, write_report,
                           write_dashboard, append_unavailable_section,
                           prepend_summary, prepend_status)
from ..core.analyze import (summarize_executive, status_overview,
                            build_data_block, default_meta)
```

- [ ] **Step 4: Add the status step in the `if res["plants"]:` block**

Replace the current summary block (lines 94-104):

```python
        report_md = append_unavailable_section(res["report_md"], skipped)
        base_md = report_md   # report + "Unavailable Plants" — the status input
        summary_md = None
        status_md = None
        if res["plants"]:
            try:
                summary_md = summarize_executive(res["report_md"])
                report_md = prepend_summary(report_md, summary_md)
                events.emit_event({"event": "note",
                                   "reason": "Hebrew executive summary added"})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"executive summary skipped: {e}")})
            try:
                status_md = status_overview(base_md)
                report_md = prepend_status(report_md, status_md)
                events.emit_event({"event": "note",
                                   "reason": "System status overview added"})
            except Exception as e:
                events.emit_event({"event": "note",
                                   "reason": red.redact(f"status overview skipped: {e}")})
```

- [ ] **Step 5: Pass `status_md` to the dashboard call**

Update the `compose_dashboard` call (currently lines 122-124):

```python
                dashboard_html = compose_dashboard(
                    summary_md, charts_html, status_md=status_md,
                    date_str=datetime.now().strftime("%d.%m.%Y"))
```

- [ ] **Step 6: Run the runner tests to verify all pass**

Run: `python -m pytest tests/web/test_runner.py -v`
Expected: PASS — the 3 new tests plus every pre-existing runner test (the summary non-fatal test still passes because `status_overview` stays stubbed).

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/web/runner.py tests/web/test_runner.py
git commit -m "feat: web runner adds system-status overview to report + dashboard (non-fatal)"
```

---

## Task 5: Wire the status into `cli.py`

**Files:**
- Modify: `solaranalysis/cli.py` (imports lines 8-10; the `if res["plants"]:` summary block ~lines 69-76; the `compose_dashboard` call ~lines 86-88)
- Test: `tests/test_cli.py` (update the autouse `_stub_llm_calls` fixture; add two tests)

**Interfaces:**
- Consumes: `status_overview` (Task 1), `prepend_status` (Task 2), `compose_dashboard(..., status_md=...)` (Task 3).
- Produces: no new public interface; the `[note] System status overview added` / `[warn] status overview skipped` stderr lines and the status block in the report/dashboard.

- [ ] **Step 1: Update the autouse fixture and write the failing tests**

In `tests/test_cli.py`, extend `_stub_llm_calls` (currently lines 8-17) with:

```python
    monkeypatch.setattr(cli, "status_overview",
                        lambda report_md: "- ✅ **Good** — תקין", raising=False)
```

Add these tests:

```python
def test_cli_prepends_system_status(tmp_path, monkeypatch):
    html = _run(tmp_path, monkeypatch, [])
    assert "סטטוס מערכות" in html                  # the status heading
    assert "תקין" in html                           # the stubbed status body
    # Status appears above the executive summary and the report body.
    assert html.index("סטטוס מערכות") < html.index("סיכום מנהלים") < html.index("Report")


def test_cli_status_failure_is_nonfatal(tmp_path, monkeypatch):
    def boom(report_md):
        raise RuntimeError("opus down")
    monkeypatch.setattr(cli, "status_overview", boom)
    html = _run(tmp_path, monkeypatch, [])   # still returns 0 and writes report
    assert "סטטוס מערכות" not in html         # status skipped, not fatal
    assert "סיכום מנהלים" in html             # summary still present
    assert "Report" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -k "status" -v`
Expected: FAIL — `test_cli_prepends_system_status` cannot find "סטטוס מערכות" (CLI does not prepend the status yet).

- [ ] **Step 3: Add the imports in `solaranalysis/cli.py`**

Update the two imports (currently lines 8-10):

```python
from .core.report import (render_html, write_report, write_dashboard,
                          append_unavailable_section, prepend_summary,
                          prepend_status)
from .core.analyze import (summarize_executive, status_overview,
                           build_data_block, default_meta)
```

- [ ] **Step 4: Add the status step in the `if res["plants"]:` block**

Replace the current summary block (lines 69-76):

```python
    base_md = report_md   # after append_unavailable_section (or plain res["report_md"])
    summary_md = None
    status_md = None
    if res["plants"]:
        try:
            summary_md = summarize_executive(res["report_md"])
            report_md = prepend_summary(report_md, summary_md)
            print("[note] Hebrew executive summary added", file=sys.stderr)
        except Exception as e:
            print(f"[warn] executive summary skipped: {e}", file=sys.stderr)
        try:
            status_md = status_overview(base_md)
            report_md = prepend_status(report_md, status_md)
            print("[note] System status overview added", file=sys.stderr)
        except Exception as e:
            print(f"[warn] status overview skipped: {e}", file=sys.stderr)
```

- [ ] **Step 5: Pass `status_md` to the dashboard call**

Update the `compose_dashboard` call (currently lines 86-88):

```python
            dashboard = compose_dashboard(
                summary_md, charts_html, status_md=status_md,
                date_str=datetime.now().strftime("%d.%m.%Y"))
```

- [ ] **Step 6: Run the CLI tests to verify all pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS — the 2 new tests plus every pre-existing CLI test (`test_cli_summary_failure_is_nonfatal` still passes: status is stubbed, so "סיכום מנהלים" is correctly absent while the run stays non-fatal).

- [ ] **Step 7: Commit**

```bash
git add solaranalysis/cli.py tests/test_cli.py
git commit -m "feat: CLI adds system-status overview to report + dashboard (non-fatal)"
```

---

## Task 6: README documentation + full-suite verification

**Files:**
- Modify: `README.md` (the section describing the executive summary / report contents)
- No test file — verified by running the whole suite.

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: nothing code-facing.

- [ ] **Step 1: Locate the executive-summary description in `README.md`**

Run: `grep -n "סיכום מנהלים\|executive summary\|Executive" README.md`
Expected: one or more line numbers where the report contents / Hebrew executive summary are described.

- [ ] **Step 2: Add a short subsection just above/adjacent to the executive-summary text**

Insert (adapt wording to the surrounding heading style):

```markdown
### System status overview ("סטטוס מערכות")

Every report **opens with a system-status overview**: a one-line fleet headline
plus a per-system traffic-light list — ✅ תקין (working correctly),
⚠️ דורשת תשומת לב (needs attention), or ❌ תקלה (problem) — so an operator sees
at a glance which systems are healthy. It is produced by a second Opus 4.8 call
(xhigh reasoning) that judges each system **from the facts already in the
report** (device statuses, alert severities, production, data-quality flags),
and appears above the Hebrew executive summary in both the on-disk
`report.html` and the emailed dashboard. Systems that could not be fetched show
as ❌. The step is non-fatal — if it fails the report is still delivered without
it.
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS — all pre-existing tests (322 per NextTODO) plus the ~10 new tests from Tasks 1-5, zero failures.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the system-status overview at the top of each report"
```

---

## Self-Review notes (for the implementer)

- **Ordering invariant:** the final on-disk document must read **status → executive summary → detailed report → unavailable plants**. `prepend_summary` runs first, then `prepend_status` wraps its output — verify with `test_render_html_renders_status_summary_report_in_order` (Task 2) and `test_cli_prepends_system_status` (Task 5).
- **Status input includes the appendix:** `status_overview` is called on `base_md` (post-`append_unavailable_section`), while `summarize_executive` stays on the clean `res["report_md"]` — do not swap these.
- **Dashboard guard unchanged:** the dashboard still runs only under `if res["plants"] and summary_md:`; `status_md` is an optional extra, `None`-safe in `compose_dashboard`.
- **Grounding:** never add the status block to `verify_numbers`; it runs on the clean report only.
