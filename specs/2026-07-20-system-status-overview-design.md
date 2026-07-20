# System Status Overview ("סטטוס מערכות") — Design

Date: 2026-07-20
Status: draft — pending user review

## 1. Purpose

Every report today opens with the Hebrew executive summary ("סיכום מנהלים"),
then the four-section detailed analysis. An O&M operator opening the email still
has to read prose to answer the one question they care about first: **for each
system we monitor, is it working correctly or not?**

This project adds a **new block at the very top of every report** — above the
executive summary — that answers exactly that, at a glance:

- A one-line **fleet headline** ("סטטוס כללי: 3 מערכות — 2 תקינות, 1 דורשת
  טיפול").
- A **Hebrew traffic-light list**, one line per system, each marked
  ✅ / ⚠️ / ❌ with a short reason.

```
סטטוס כללי: 3 מערכות — 2 תקינות, 1 דורשת טיפול

✅ מערכת א'  — תקין, כל הממירים פעילים
⚠️ מערכת ב'  — תפוקה נמוכה מהצפוי
❌ מערכת ג'  — ממיר בתקלה, התראה קריטית
```

Three states:

| Marker | Hebrew | Meaning |
|--------|--------|---------|
| ✅ | תקין | Working correctly — no faults, no error/critical alerts, producing normally |
| ⚠️ | דורשת תשומת לב | Degraded — low output vs. peers/expected, warnings, standby, or partial data |
| ❌ | תקלה | Problem — offline/fault inverter, error/critical alert, zero output when expected, **or data could not be fetched** |

The verdict is **Claude's judgment** (per user's choice), but Claude is
instructed to base it strictly on facts already in the report — device
statuses, alert severities, production figures, data-quality flags, and the
"Unavailable Plants" section — not on free recall. It introduces no new numbers.

The block appears in **both** artifacts — the on-disk `report.html` and the
emailed dashboard — for **both** the web app (`web/runner.py`) and the CLI
(`cli.py`), mirroring how the executive summary already works.

### Non-goals

- **Python-computed verdict / config thresholds.** Per user choice, Claude makes
  the OK / attention / problem call. No numeric health-rule engine, no
  `config.yaml` thresholds. (The facts Claude reasons over are still the
  Python-grounded ones already in the report.)
- **New provider / SDK.** Reuses the existing `anthropic` SDK and
  `ANTHROPIC_API_KEY`. No new secrets, no change to `collect_secrets`/redaction.
- **New facts or grounding changes.** The status distills only what the report
  already states. `verify_numbers` still runs on the clean `res["report_md"]`
  only and is untouched.
- **Making the status mandatory.** A status-call failure is non-fatal — the run
  still succeeds and delivers the report/email without the status block
  (mirrors the existing non-fatal exec-summary and dashboard patterns).
- **English variant.** Like the executive summary, the status is always Hebrew,
  to match the block directly below it. `cfg.output_language` does not gate it.

## 2. Overview of the flow

The status is generated as a **separate Claude call**, parallel to the
executive summary, at the same call sites. Both distill the report; neither
feeds the other.

```
run_pipeline → res["report_md"]        (clean grounded narrative)
        │
   base = append_unavailable_section(res["report_md"], skipped)   ← report + "Unavailable Plants"
        │
        ├─ summary_md = summarize_executive(res["report_md"])      ← existing (clean report, no appendix)
        ├─ status_md  = status_overview(base)                      ← NEW (INCLUDES appendix, so skipped → ❌)
        │       (each on failure: log a note, skip that block)
        │
   report_md = prepend_summary(base, summary_md)                   ← summary → (report + appendix)
   report_md = prepend_status(report_md, status_md)                ← status → summary → report + appendix
        │
        ├─ render_html(report_md, …)                    → report.html
        └─ compose_dashboard(summary_md, charts_html, status_md=status_md, …)   → dashboard email
```

Final on-disk document order: **status (top) → executive summary → detailed
report → unavailable plants (bottom)**.

**Why `status_overview` gets the appendix but `summarize_executive` does not:**
the executive-summary behavior is unchanged (it reads `res["report_md"]`). The
status must account for systems that could not be fetched — an unfetched system
is precisely a system whose status is *not* "working correctly" — so it reads
`base`, which carries the "Unavailable Plants" section. Claude marks each such
system ❌ with reason "לא ניתן לאחזר נתונים".

## 3. New function — `core/analyze.py::status_overview`

```python
def status_overview(report_md: str, client=None) -> str:
    """Produce a brief Hebrew system-status overview ("סטטוס מערכות") of an
    already-generated report, as markdown: a one-line fleet headline followed by
    a traffic-light list (✅/⚠️/❌) — one line per system, with a short reason.
    Claude judges each system's state from the facts in the report only. Uses
    Claude Opus 4.8 at xhigh effort. `client` is injectable for tests (mirrors
    summarize_executive)."""
```

- Prompt: a new file `solaranalysis/prompts/status.txt` (see §4), loaded like
  `_exec_summary_prompt()` loads `exec_summary.txt`. Add a module-level
  `_STATUS_PROMPT_PATH` and a `_status_prompt()` helper next to the existing
  exec-summary ones.
- Client init: `if client is None: import anthropic; client = anthropic.Anthropic()`
  — identical to `summarize_executive`.
- Request shape (identical knobs to `summarize_executive`, validated against
  `anthropic` 0.116.0):

  ```python
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
  text = "".join(b.text for b in msg.content
                 if getattr(b, "type", None) == "text")
  return _ensure_list_breaks(text)
  ```

  - `output_config={"effort": "xhigh"}` + `thinking={"type": "adaptive"}` — the
    Opus 4.8 analog of "xhigh reasoning"; `thinking.budget_tokens` would 400.
  - `temperature`/`top_p`/`top_k` omitted (they 400 on Opus 4.8).
  - Reuses the existing `_ensure_list_breaks` helper so a headline line
    immediately followed by list items still renders as a real `<ul>` (the same
    fix the exec summary relies on).

Same-file placement keeps `status_overview` beside `summarize_executive`; the
two are near-identical, isolated units differing only in prompt and user nudge.

## 4. Prompt — `solaranalysis/prompts/status.txt`

Hebrew "סטטוס מערכות" contract. Content brief (final wording written during
implementation):

- **Role:** You produce a brief system-status overview, in **Hebrew**, for a
  solar-PV O&M operator, from a fleet comparison report they already have.
- **Output shape:**
  1. A single **headline line** first: `סטטוס כללי: N מערכות — X תקינות, Y
     דורשות תשומת לב, Z בתקלה`. Only name the states that actually occur (drop a
     count that is zero).
  2. Then a markdown **bullet list**, one bullet per system, each starting with
     the state emoji (✅ / ⚠️ / ❌), then the system name in bold, then " — " and
     a **short** reason (a few words, not a sentence).
- **State definitions** (give these verbatim in the prompt so the mapping is
  stable run-to-run):
  - ✅ **תקין** — no faults, no error/critical alerts, producing normally.
  - ⚠️ **דורשת תשומת לב** — degraded: low output vs. peers/expected, warning
    alerts, standby devices, or only partial data.
  - ❌ **תקלה** — offline/fault inverter, error/critical alert, zero output when
    output is expected, **or data could not be fetched** (a system listed under
    "Unavailable Plants").
- **Coverage:** include **every** system named in the report, including any
  under an "Unavailable Plants" / unavailable section — mark those ❌ with reason
  "לא ניתן לאחזר נתונים".
- **Grounding:** judge each system **only** from facts in the report (device
  statuses, alert severities/counts, production figures, data-quality flags, the
  unavailable section). Never invent a fault or a number. When the evidence is
  ambiguous between two states, choose the more cautious (worse) one.
- **Format:** markdown; **no top-level heading** (the wrapper / dashboard
  supplies the "סטטוס מערכות" context). Keep every line short and scannable.
  Hebrew throughout.

## 5. Rendering

### 5a. `core/report.py::prepend_status(report_md, status_md) -> str`

Mirrors `prepend_summary`: wraps the status in a right-to-left block with its
own heading and prepends it above whatever is passed in (which, at the call
site, is already `summary → report`), separated by a rule:

```python
def prepend_status(report_md: str, status_md: str) -> str:
    return ('<div dir="rtl" markdown="1">\n\n'
            '## סטטוס מערכות\n\n'
            f'{status_md}\n\n'
            '</div>\n\n---\n\n' + report_md)
```

The blank line after `<div …>` and `markdown="1"` are required by `md_in_html`
(already in both renderers' extension lists) so the inner markdown renders as
HTML inside the RTL container. The existing `[dir=rtl]` CSS in `render_html`'s
`_CSS` already styles this block (heading border side, right alignment); no CSS
change is needed. Emoji markers carry their own color in every client, so no
per-state color styling is added.

### 5b. `core/dashboard.py::compose_dashboard` — new optional `status_md`

Add a keyword-only `status_md: str | None = None` parameter (backward
compatible — existing callers/tests pass none). Inside, render it as its own RTL
block **above** the summary block, in the existing `{{SUMMARY}}` slot — so
**no dashboard-prompt or shell change is needed** and both the model shell and
the deterministic fallback template pick it up for free:

```python
def _status_html(status_md: str) -> str:
    body = report._inline_email_styles(
        md.markdown(status_md, extensions=["tables", "fenced_code", "md_in_html"]))
    return f'<div dir="rtl" style="text-align:right;">{body}</div>'

# in compose_dashboard, replacing the current summary_html line:
summary_html = (_status_html(status_md) if status_md else "") + _summary_html(summary_md)
```

The status block in the email carries no extra heading — its "סטטוס כללי:"
headline line is self-heading, matching how the summary already renders
heading-less in the dashboard.

**Preheader upgrade (small win):** `_preheader` currently takes the summary's
first line as the hidden inbox-preview snippet. The status headline ("סטטוס
כללי: …") is a far better preview. Change `compose_dashboard` to build the
preheader from `status_md` when present, else fall back to `summary_md`:
`_inject_preheader(html_doc, _preheader(status_md or summary_md))`. `_preheader`
itself is unchanged.

## 6. Integration — call sites (both non-fatal)

Both `cli.py` and `web/runner.py` already own the composed report and a "note"
mechanism, and both already have the `if res["plants"]:` summary block. The
status step slots into that same block, right after the summary is prepended,
and reuses the appendix-bearing base for its input and the `status_md` for the
dashboard call.

### `solaranalysis/web/runner.py`

Imports: add `status_overview` to the `..core.analyze` import and `prepend_status`
to the `..core.report` import.

Around the existing lines 94–99, capture the appendix base and add the status
call:

```python
report_md = append_unavailable_section(res["report_md"], skipped)
base_md = report_md   # report + "Unavailable Plants" — the status input
summary_md = None
status_md = None
if res["plants"]:
    try:
        summary_md = summarize_executive(res["report_md"])
        report_md = prepend_summary(report_md, summary_md)
        events.emit_event({"event": "note", "reason": "Hebrew executive summary added"})
    except Exception as e:
        events.emit_event({"event": "note",
                           "reason": red.redact(f"executive summary skipped: {e}")})
    try:
        status_md = status_overview(base_md)
        report_md = prepend_status(report_md, status_md)
        events.emit_event({"event": "note", "reason": "System status overview added"})
    except Exception as e:
        events.emit_event({"event": "note",
                           "reason": red.redact(f"status overview skipped: {e}")})
```

Then pass `status_md` to the dashboard call (line ~122):

```python
dashboard_html = compose_dashboard(
    summary_md, charts_html, status_md=status_md,
    date_str=datetime.now().strftime("%d.%m.%Y"))
```

The dashboard block's guard stays `if res["plants"] and summary_md:` — the
dashboard is gated on the summary as today; `status_md` is an optional extra on
top (and is `None`-safe in `compose_dashboard`).

### `solaranalysis/cli.py`

Same edits, using the CLI's `print(..., file=sys.stderr)` note style instead of
`events.emit_event`:

```python
base_md = report_md   # after append_unavailable_section (or plain res["report_md"])
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

Add `status_overview` to the `.core.analyze` import and `prepend_status` to the
`.core.report` import, and pass `status_md=status_md` to `compose_dashboard`.

Note: in `cli.py` the base for the status is `report_md` after the
`append_unavailable_section` branch (which only runs when there are skipped
plants); capturing `base_md = report_md` just before the `if res["plants"]:`
block covers both the skipped and no-skip cases.

## 7. Failure isolation

- **Non-fatal status.** A status-call exception is caught at each call site; the
  run proceeds and renders/emails without the status block, emitting a note.
  Independent of the summary's try/except, so either block can fail alone.
- **`compose_dashboard` `None`-safe.** With `status_md=None` the dashboard
  renders exactly as today (no status block), so a status failure never breaks
  the email.
- **No new secrets / grounding untouched.** Uses the existing
  `ANTHROPIC_API_KEY`; `verify_numbers` still runs on the clean report only.

## 8. Testing (TDD)

`tests/test_analyze.py` (reusing the existing recording/fake client — no
network):

- `test_status_overview_uses_injected_client` — fake client returns Hebrew
  markdown → `status_overview` returns it.
- `test_status_overview_request_shape` — recorded kwargs use
  `model="claude-opus-4-8"`, `output_config={"effort": "xhigh"}`,
  `thinking={"type": "adaptive"}`; the user message contains the passed
  `report_md`; no `temperature`/`top_p`/`top_k` are sent; system prompt is the
  status prompt.

`tests/test_report.py`:

- `test_prepend_status_places_status_first_rtl` — output starts with the
  `dir="rtl"` wrapper and the "סטטוס מערכות" heading; the passed inner report
  body appears after it.
- `test_render_html_renders_rtl_status` — `render_html` of a
  `prepend_status(prepend_summary(...))` document shows status heading, then
  summary heading, then the report body, in that order (verifies ordering +
  `md_in_html`).

`tests/` dashboard test (wherever `compose_dashboard` is currently tested):

- `test_compose_dashboard_renders_status_above_summary` — with an injected fake
  client returning a shell containing `{{SUMMARY}}`/`{{CHARTS}}`, passing
  `status_md` puts the status HTML before the summary HTML in the output.
- `test_compose_dashboard_without_status_unchanged` — `status_md=None` produces
  output with no status block (guards backward compatibility).
- `test_preheader_prefers_status` — when `status_md` is given, the hidden
  preheader div contains the status headline, not the summary's first line.

Non-fatal path:

- A call-site test (in `tests/web/test_runner.py` and/or `tests/test_cli.py`,
  matching the existing exec-summary non-fatal test) monkeypatches
  `status_overview` to raise and asserts the run still completes and writes the
  report/dashboard, with the summary still present and a note emitted.

## 9. Docs

- `README.md`: extend the executive-summary subsection to note that each report
  now **opens with a system-status overview ("סטטוס מערכות")** — a fleet
  headline plus a per-system traffic-light list (✅ תקין / ⚠️ דורשת תשומת לב /
  ❌ תקלה) judged by a second Opus 4.8 call from the report's facts, appearing
  above the executive summary in both `report.html` and the emailed dashboard,
  and skipped non-fatally on failure. Unavailable systems appear as ❌.
