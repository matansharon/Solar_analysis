# Hebrew Executive Summary ("סיכום מנהלים") — Design

Date: 2026-07-14
Status: draft — pending user review

## 1. Purpose

Every run currently produces a single grounded narrative report: the pipeline
calls `run_analysis` (Claude writes four sections from a Python-computed data
block), the markdown is rendered to HTML, written to disk, and — for web-app
runs — emailed (`render_email_html`).

This project adds a **second LLM call** that takes that finished report and
distills it into a concise Hebrew **executive summary ("סיכום מנהלים")**,
prepended to the top of the report so a manager sees the summary first,
followed by the full detailed analysis. It appears in **both** the on-disk
`report.html` and the emailed body, for **both** the web app and the standalone
CLI.

The summary call uses **Claude Opus 4.8 (`claude-opus-4-8`) at "xhigh"
reasoning** — the user's original request named `gpt-5.6-sol` with "xhigh
reasoning"; per user direction this is implemented with Anthropic Opus 4.8, and
"xhigh reasoning" maps to `output_config={"effort": "xhigh"}` + adaptive
thinking (Claude's analog — see §3).

### Non-goals

- **New provider / SDK.** No OpenAI/GPT dependency. Reuses the existing
  `anthropic` SDK and `ANTHROPIC_API_KEY`.
- **Config knobs for the summary.** Model (`claude-opus-4-8`), effort
  (`xhigh`), and output language (Hebrew, always) are fixed in code per the
  user's request — no `config.yaml` / settings surface for them. (The main
  report still picks its model via `pick_model`, unchanged.)
- **New facts.** The summary distills only what the report already states; it
  introduces no numbers or claims not present in the report. It does not touch
  the grounding/`verify_numbers` guarantee (see §5).
- **Making the summary mandatory.** A summary-call failure is non-fatal — the
  run still succeeds and delivers the detailed report without the summary
  (see §5).

## 2. Overview of the flow

```
run_pipeline → res["report_md"]        (clean grounded narrative, English by default)
        │
        ├─ verify_numbers(res["report_md"], data_block)   ← unchanged; runs on CLEAN report only
        │
   (call site: cli.py / web/runner.py, after append_unavailable_section)
        │
        ├─ summary_md = summarize_executive(res["report_md"], cfg)   ← NEW Opus 4.8 call (Hebrew)
        ├─ report_md  = prepend_summary(report_md, summary_md)       ← NEW, RTL wrapper on top
        │       (on failure: log a note, keep report_md unchanged)
        │
        └─ render_html(report_md, …)   +   (web only) render_email_html(report_md, …)
```

The summary is generated from `res["report_md"]` (the clean main narrative,
without the "Unavailable Plants" appendix) but prepended to the fully composed
`report_md` (which already has the appendix at the bottom). Final document
order: **summary (top) → detailed report → unavailable plants (bottom)**.

## 3. New function — `core/analyze.py::summarize_executive`

```python
def summarize_executive(report_md: str, cfg: AppConfig, client=None) -> str:
    """Produce a concise Hebrew executive summary ("סיכום מנהלים") of an
    already-generated report, as markdown. Uses Claude Opus 4.8 at xhigh
    effort. `client` is injectable for tests (mirrors run_analysis)."""
```

- Prompt: a new file `solaranalysis/prompts/exec_summary.txt` holds the system
  prompt (the grounding contract for the summary — see §4). Loaded like
  `_system_prompt()` loads `system.txt`.
- Client: `if client is None: import anthropic; client = anthropic.Anthropic()`
  — identical to `run_analysis`.
- Request shape (validated against `anthropic` 0.116.0, installed):

  ```python
  msg = client.messages.create(
      model="claude-opus-4-8",
      max_tokens=16000,
      thinking={"type": "adaptive"},
      output_config={"effort": "xhigh"},
      system=[{"type": "text", "text": _exec_summary_prompt(),
               "cache_control": {"type": "ephemeral"}}],
      messages=[{"role": "user", "content":
                 report_md + "\n\nכתוב סיכום מנהלים בעברית של הדוח שלמעלה."}],
  )
  return "".join(b.text for b in msg.content
                 if getattr(b, "type", None) == "text")
  ```

  Notes on the API (from the claude-api skill):
  - **`effort: "xhigh"`** is the exact Opus 4.8 analog of "xhigh reasoning";
    the legacy `thinking.budget_tokens` would return a **400** on Opus 4.8.
  - `temperature`/`top_p`/`top_k` are **omitted** (they 400 on Opus 4.8).
  - `thinking.display` defaults to `"omitted"` — fine; the text extraction
    already ignores non-`text` blocks (thinking blocks carry no text anyway).
  - `max_tokens=16000` non-streaming mirrors `run_analysis` and stays under the
    SDK's long-request timeout guard; a short summary + thinking fits easily.

## 4. Prompt — `solaranalysis/prompts/exec_summary.txt`

Hebrew "סיכום מנהלים" contract. Content brief (final wording written during
implementation):

- Role: you write a concise executive summary, in **Hebrew**, of a solar-fleet
  comparison report, for management.
- Content: lead with the headline (best/worst performers by specific yield),
  then notable faults/alerts, then the top recommended actions. Scannable —
  a short lead paragraph and/or a few bullets.
- Grounding: use **only** facts and figures present in the provided report; do
  not introduce new numbers or invent data. If the report says a metric is not
  reported, do not fabricate it.
- Format: markdown, **no top-level H1** (the wrapper supplies the
  "סיכום מנהלים" heading — see §5); use bold and bullets. Keep it brief.

## 5. Rendering — `core/report.py`

### `prepend_summary(report_md, summary_md) -> str`

Wraps the summary in a right-to-left block with its own heading and prepends it
above the detailed report, separated by a rule:

```python
def prepend_summary(report_md: str, summary_md: str) -> str:
    return ('<div dir="rtl" markdown="1">\n\n'
            '## סיכום מנהלים\n\n'
            f'{summary_md}\n\n'
            '</div>\n\n---\n\n' + report_md)
```

The blank line after `<div …>` and the `markdown="1"` attribute are required by
the `md_in_html` extension so the inner markdown (heading, bullets, bold) is
rendered as HTML **inside** the RTL container.

### Markdown extension + RTL styling

- Add `"md_in_html"` to the extensions list in **both** `render_html` and
  `render_email_html`:
  `md.markdown(report_md, extensions=["tables", "fenced_code", "md_in_html"])`.
- On-disk report (`_CSS`): add a small rule so the Hebrew block reads correctly
  and the accent heading border sits on the correct side:

  ```css
  [dir=rtl] { text-align: right; }
  [dir=rtl] h2 { border-left: 0; border-right: 4px solid var(--accent);
                 padding-left: 0; padding-right: 12px; }
  ```

- Email (`render_email_html`): the `<div dir="rtl">` carries direction; the
  existing `_inline_email_styles` regex already inlines styles on the inner
  `h2`/`p`/`ul`/`li` tags (it matches globally), so the summary is styled like
  the rest of the email body. The heading's accent border on the left in email
  is a minor cosmetic detail left as-is (Outlook RTL border-side handling is
  unreliable; not worth special-casing).

`write_report` is unchanged.

## 6. Integration — call sites (both non-fatal)

Both sites already own `cfg` and the composed `report_md`, and both already
have a "note" mechanism. The summary step slots in **after**
`append_unavailable_section` and **before** rendering.

### `solaranalysis/cli.py`

After `report_md = append_unavailable_section(...)` (or the plain
`res["report_md"]` when nothing is skipped), before `render_html`:

```python
from .core.analyze import summarize_executive                  # add import
from .core.report import prepend_summary                       # add to existing report import

if res["plants"]:
    try:
        summary_md = summarize_executive(res["report_md"], cfg)
        report_md = prepend_summary(report_md, summary_md)
        print("[note] Hebrew executive summary added", file=sys.stderr)
    except Exception as e:
        print(f"[warn] executive summary skipped: {e}", file=sys.stderr)
```

### `solaranalysis/web/runner.py`

After `report_md = append_unavailable_section(res["report_md"], skipped)`,
before `render_html`:

```python
from ..core.report import (..., prepend_summary)               # add to existing report import
from ..core.analyze import summarize_executive                 # add import

if res["plants"]:
    try:
        summary_md = summarize_executive(res["report_md"], cfg)
        report_md = prepend_summary(report_md, summary_md)
        events.emit_event({"event": "note",
                           "reason": "Hebrew executive summary added"})
    except Exception as e:
        events.emit_event({"event": "note",
                           "reason": red.redact(f"executive summary skipped: {e}")})
```

Both `render_html` (disk) and `render_email_html` (email) then use the
summary-prepended `report_md`. The `no plants` / "No plant data available."
case is skipped (guarded by `if res["plants"]`).

`verify_numbers` is computed inside `run_pipeline` on the **clean**
`res["report_md"]` and is untouched, so the Hebrew summary's numbers never
inflate the "figures not found verbatim" note.

## 7. Failure isolation

- **Non-fatal summary.** A summary-call exception (API error, timeout, etc.) is
  caught at the call site; the run proceeds and renders/emails the detailed
  report without the summary, emitting a note. This mirrors the existing
  non-fatal email and measurement-persistence patterns in `runner.py`.
- **No new secrets.** The call uses the existing `ANTHROPIC_API_KEY`; nothing
  new is added to `collect_secrets`/redaction.

## 8. Testing (TDD)

`tests/test_analyze.py` (additions, reusing the existing `_RecordingClient`
fake — no network):

- `test_summarize_executive_uses_injected_client` — fake client returns Hebrew
  markdown → `summarize_executive` returns it.
- `test_summarize_executive_request_shape` — recorded kwargs use
  `model="claude-opus-4-8"`, `output_config={"effort": "xhigh"}`,
  `thinking={"type": "adaptive"}`; the user message contains the passed
  `report_md`; and no `temperature`/`top_p`/`top_k` are sent.

`tests/test_report.py` (additions):

- `test_prepend_summary_places_summary_first_rtl` — output starts with the
  `dir="rtl"` wrapper and the "סיכום מנהלים" heading, and the main report body
  appears after the summary.
- `test_render_html_renders_rtl_summary` — `render_html` of a
  `prepend_summary(...)` document contains a rendered `<h2>` with "סיכום מנהלים"
  inside a `dir="rtl"` element (verifies `md_in_html` is active).

Non-fatal path:

- A call-site test (in `tests/test_cli.py` or `tests/web/test_runner.py`,
  whichever fits the existing harness) monkeypatches `summarize_executive` to
  raise and asserts the run still completes and writes the report (web:
  terminal status unchanged + a `note` emitted).

## 9. Docs

- `README.md`: a short subsection (under "How it works" and/or the Web UI docs)
  noting that each report is prefixed with a Hebrew executive summary
  ("סיכום מנהלים") generated by a second Opus 4.8 call at xhigh reasoning,
  applies to both CLI and web, and is skipped non-fatally if the call fails.
