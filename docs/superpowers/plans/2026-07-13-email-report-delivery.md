# Email Report Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email each web-app run's HTML report to a configured recipient list via Microsoft Graph app-only `sendMail`.

**Architecture:** A dependency-light `solaranalysis/web/mailer.py` (plain `requests`, OAuth2 client-credentials flow) exposes `is_configured()`, `recipients()`, `get_token()`, and `send_report()`. `runner.py::run_analysis_job` calls it once, after the report is written and the run status is known, reusing the in-memory `html`. Send is non-fatal (a failure emits a `note` and the run still finalizes). All config lives in `.env`.

**Tech Stack:** Python 3.10, `requests` (already a dependency — no `msal`), pytest, Microsoft Graph v1.0.

## Global Constraints

- **Python interpreter:** `python` (not `python3`) — `python3` is a broken Windows Store alias on this machine.
- **No new dependencies:** use `requests` (already in `requirements.txt`); do NOT add `msal`.
- **Commits:** no AI attribution / no `Co-Authored-By` line (repo policy).
- **Trigger scope:** only the web/runner path emails (manual + scheduled). The CLI (`cli.py`) stays email-free — do not touch it.
- **Outcomes:** email only on `success`/`partial` (a report exists). `failed` runs (the `except` branch of `run_analysis_job`) send nothing.
- **Format:** inline HTML body only. No attachment.
- **Failure isolation:** an email send failure must never change the run's terminal status; it emits a redacted `note` event and the run finalizes normally (mirrors the existing `persist()` pattern in the same function).
- **Config keys (all in `.env`):** `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, `REPORT_RECIPIENTS`. Feature is dark (skips send, emits a note) unless all four `GRAPH_*` keys are set and `REPORT_RECIPIENTS` is non-empty.
- **Event vocabulary:** emit `report_emailed` (with `to`: recipient list) on a successful send; emit `note` (with `reason`) when skipping or on failure. Events go through `events.emit_event`.

---

### Task 1: Graph mailer module

**Files:**
- Create: `solaranalysis/web/mailer.py`
- Test: `tests/web/test_mailer.py`
- Modify: `.env.example` (append config keys)

**Interfaces:**
- Consumes: nothing from other tasks. Reads env vars via `os.getenv`. Uses `requests.post` (injectable as `http_post` for tests).
- Produces (used by Task 2):
  - `is_configured() -> bool`
  - `recipients() -> list[str]`
  - `get_token(tenant: str, client_id: str, client_secret: str, http_post=requests.post) -> str`
  - `send_report(subject: str, html_body: str, http_post=requests.post) -> None` — reads sender/tenant/client/secret/recipients from env internally.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_mailer.py`:

```python
import pytest
from solaranalysis.web import mailer

_ALL = {
    "GRAPH_TENANT_ID": "tid",
    "GRAPH_CLIENT_ID": "cid",
    "GRAPH_CLIENT_SECRET": "sec",
    "GRAPH_SENDER": "sender@elcam.co.il",
}


class FakeResp:
    def __init__(self, ok=True, payload=None, status=200, text=""):
        self.ok = ok
        self._payload = payload or {}
        self.status_code = status
        self.text = text

    def json(self):
        return self._payload


def _set(monkeypatch, **env):
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "GRAPH_SENDER", "REPORT_RECIPIENTS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_is_configured_true_when_all_set(monkeypatch):
    _set(monkeypatch, **_ALL)
    assert mailer.is_configured() is True


def test_is_configured_false_when_any_missing(monkeypatch):
    _set(monkeypatch, GRAPH_TENANT_ID="tid", GRAPH_CLIENT_ID="cid",
         GRAPH_CLIENT_SECRET="sec")  # no GRAPH_SENDER
    assert mailer.is_configured() is False


def test_recipients_parsing(monkeypatch):
    _set(monkeypatch, REPORT_RECIPIENTS=" a@x.com , b@x.com ,, a@x.com ")
    assert mailer.recipients() == ["a@x.com", "b@x.com"]


def test_recipients_empty_when_unset(monkeypatch):
    _set(monkeypatch)
    assert mailer.recipients() == []


def test_get_token_posts_client_credentials():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResp(ok=True, payload={"access_token": "TOKEN123"})

    token = mailer.get_token("tid", "cid", "sec", http_post=fake_post)
    assert token == "TOKEN123"
    url, kwargs = calls[0]
    assert "tid/oauth2/v2.0/token" in url
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["client_secret"] == "sec"
    assert kwargs["data"]["scope"] == "https://graph.microsoft.com/.default"


def test_get_token_raises_on_failure():
    def fake_post(url, **kwargs):
        return FakeResp(ok=False, status=401, text="bad creds")

    with pytest.raises(RuntimeError, match="token request failed"):
        mailer.get_token("tid", "cid", "sec", http_post=fake_post)


def test_send_report_builds_payload(monkeypatch):
    _set(monkeypatch, **_ALL, REPORT_RECIPIENTS="a@x.com, b@x.com")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/token"):
            return FakeResp(ok=True, payload={"access_token": "TOK"})
        return FakeResp(ok=True, status=202)

    mailer.send_report("Subj", "<p>hi</p>", http_post=fake_post)
    token_url, _ = calls[0]
    assert token_url.endswith("/token")
    send_url, kwargs = calls[1]
    assert send_url == "https://graph.microsoft.com/v1.0/users/sender@elcam.co.il/sendMail"
    assert kwargs["headers"]["Authorization"] == "Bearer TOK"
    msg = kwargs["json"]["message"]
    assert msg["subject"] == "Subj"
    assert msg["body"]["contentType"] == "HTML"
    assert msg["body"]["content"] == "<p>hi</p>"
    addrs = [r["emailAddress"]["address"] for r in msg["toRecipients"]]
    assert addrs == ["a@x.com", "b@x.com"]


def test_send_report_raises_on_failure(monkeypatch):
    _set(monkeypatch, **_ALL, REPORT_RECIPIENTS="a@x.com")

    def fake_post(url, **kwargs):
        if url.endswith("/token"):
            return FakeResp(ok=True, payload={"access_token": "TOK"})
        return FakeResp(ok=False, status=403, text="Forbidden")

    with pytest.raises(RuntimeError, match="sendMail failed"):
        mailer.send_report("s", "<p>x</p>", http_post=fake_post)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_mailer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'solaranalysis.web.mailer'` (collection error).

- [ ] **Step 3: Write the mailer module**

Create `solaranalysis/web/mailer.py`:

```python
"""Microsoft Graph mailer — app-only (client-credentials) sendMail.

Sends each run's HTML report from a fixed mailbox (GRAPH_SENDER) using an
Azure AD app registration granted the Mail.Send *application* permission.
All config comes from environment variables; if any are missing,
is_configured() is False and callers should skip sending.
"""
from __future__ import annotations
import os
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_REQUIRED = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER")


def is_configured() -> bool:
    """True only when every required Graph env var is set (non-empty)."""
    return all(os.getenv(k) for k in _REQUIRED)


def recipients() -> list[str]:
    """Parse REPORT_RECIPIENTS: comma-separated, stripped, blanks dropped,
    order-preserving dedupe. Unset/empty -> []."""
    out: list[str] = []
    seen: set[str] = set()
    for part in os.getenv("REPORT_RECIPIENTS", "").split(","):
        addr = part.strip()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def get_token(tenant: str, client_id: str, client_secret: str,
              http_post=requests.post) -> str:
    resp = http_post(
        _TOKEN_URL.format(tenant=tenant),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Graph token request failed {resp.status_code}: {resp.text}")
    return resp.json()["access_token"]


def send_report(subject: str, html_body: str, http_post=requests.post) -> None:
    """Send the report as one inline-HTML email to all recipients().
    Reads sender/tenant/client/secret from env. Raises on token error or a
    non-2xx Graph response (Graph returns 202 Accepted on success)."""
    sender = os.getenv("GRAPH_SENDER")
    token = get_token(os.getenv("GRAPH_TENANT_ID"), os.getenv("GRAPH_CLIENT_ID"),
                      os.getenv("GRAPH_CLIENT_SECRET"), http_post=http_post)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients()],
        },
        "saveToSentItems": True,
    }
    resp = http_post(
        f"{GRAPH}/users/{sender}/sendMail",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Graph sendMail failed {resp.status_code}: {resp.text}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_mailer.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Update `.env.example`**

Append to `.env.example` (after the `SOLAR_HEADLESS` block):

```
# Email delivery of each web-app run's report (Microsoft Graph, app-only).
# The app registration needs the Mail.Send APPLICATION permission
# (admin-consented) on GRAPH_SENDER. Leave any blank to disable emailing.
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_SENDER=elcam.ai@elcam.co.il
# Comma-separated. Defaults to the sender.
REPORT_RECIPIENTS=elcam.ai@elcam.co.il
```

- [ ] **Step 6: Commit**

```bash
git add solaranalysis/web/mailer.py tests/web/test_mailer.py .env.example
git commit -m "feat: add Microsoft Graph report mailer (app-only sendMail)"
```

---

### Task 2: Wire the mailer into the runner

**Files:**
- Modify: `solaranalysis/web/runner.py` (imports line 15; `collect_secrets`; hook in `run_analysis_job` after status is computed)
- Test: `tests/web/test_runner.py` (add new test functions; do not edit existing ones)
- Modify: `README.md` (add an "Email delivery" subsection under Web UI)

**Interfaces:**
- Consumes (from Task 1): `mailer.is_configured()`, `mailer.recipients()`, `mailer.send_report(subject, html)`.
- Produces: no new public functions. Adds `report_emailed`/`note` events to the run's event stream and includes `GRAPH_CLIENT_SECRET` in `collect_secrets`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_runner.py` (reuse the existing `_paths`, `_seed`, and `PlantData` import patterns already at the top of that file):

```python
def _seed_run(paths):
    conn, key = _seed(paths)
    repo.create_run(conn, trigger="manual", time_range="30d",
                    log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()


def _success_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
    from solaranalysis.core.schema import PlantData
    return {"report_md": "# R", "plants": [PlantData(
                plant_id="g", source_platform="growatt",
                source_plant_id="1", plant_name="Good")],
            "verify_missing": [], "skipped_plants": []}


def test_run_job_emails_on_success(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append((subject, html)))
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    kinds = [json.loads(l[len("@@EVENT@@ "):])["event"]
             for l in out.splitlines() if l.startswith("@@EVENT@@ ")]
    assert "report_emailed" in kinds
    assert len(sent) == 1
    assert sent[0][0].startswith("Solar Fleet Analysis")


def test_run_job_emails_on_partial(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)

    def partial_pipeline(cfg, tr, ss, progress=None, on_fetched=None):
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "boom"}]}

    monkeypatch.setattr(runner, "run_pipeline", partial_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "partial"
    assert len(sent) == 1 and "partial" in sent[0]


def test_run_job_skips_email_when_unconfigured(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: False)
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(subject))
    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == []
    assert "email not configured" in out


def test_run_job_email_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)

    def boom(subject, html):
        raise RuntimeError("graph down")

    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report", boom)
    rc = runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert rc == 0
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "success"
    assert "email send failed" in out


def test_collect_secrets_includes_graph_secret(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, _ = runner.build_app_config(conn, key)
    conn.close()
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "graphsecret")
    assert "graphsecret" in runner.collect_secrets(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_runner.py -q`
Expected: FAIL — `AttributeError: module 'solaranalysis.web.runner' has no attribute 'mailer'` (and `collect_secrets` test fails: `graphsecret` not in list).

- [ ] **Step 3: Add the `mailer` import and `os`**

In `solaranalysis/web/runner.py`, change the import line (currently line 15):

```python
from . import db, repo, crypto, events
```
to:
```python
import os
from . import db, repo, crypto, events, mailer
```

(Place `import os` with the other stdlib imports near the top; add `mailer` to the `from . import` line.)

- [ ] **Step 4: Extend `collect_secrets`**

In `solaranalysis/web/runner.py`, replace the body of `collect_secrets`:

```python
def collect_secrets(cfg: AppConfig) -> list[str]:
    out = []
    for pc in cfg.plants:
        if pc.auth.password:
            out.append(pc.auth.password)
        if pc.auth.token:
            out.append(pc.auth.token)
    graph_secret = os.getenv("GRAPH_CLIENT_SECRET")
    if graph_secret:
        out.append(graph_secret)
    return out
```

- [ ] **Step 5: Add the email hook in `run_analysis_job`**

In `solaranalysis/web/runner.py::run_analysis_job`, immediately after this line:

```python
        status = "partial" if skipped else "success"
```

insert:

```python
        subject = (f"Solar Fleet Analysis · {status} · {len(res['plants'])} plants "
                   f"· range {run['time_range']} · {stamp} UTC")
        try:
            if mailer.is_configured() and mailer.recipients():
                mailer.send_report(subject, html)
                events.emit_event({"event": "report_emailed", "to": mailer.recipients()})
            else:
                events.emit_event({"event": "note",
                                   "reason": "email not configured; skipping"})
        except Exception as e:
            events.emit_event({"event": "note",
                               "reason": red.redact(f"email send failed: {e}")})
```

(`status`, `stamp`, `html`, `res`, `run`, and `red` are all already in scope at this point.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_runner.py -q`
Expected: PASS (all existing + 5 new tests pass).

- [ ] **Step 7: Run the full backend suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS — the pre-existing runner tests still pass (email is dark by default, so they emit a harmless "email not configured" note).

- [ ] **Step 8: Update the README**

In `README.md`, add this subsection under the "Web UI" section (e.g. after "Importing an existing `config.yaml`"):

```markdown
### Email delivery

Every web-app run (manual or scheduled) that produces a report — status
`success` or `partial` — emails the report as an inline-HTML message via
Microsoft Graph (app-only `sendMail`). `failed` runs send nothing.

Configure it in `.env`:

- `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` — the Azure AD
  app registration, which must be granted the **Mail.Send application
  permission** (admin-consented) on `GRAPH_SENDER`.
- `GRAPH_SENDER` — the mailbox the app sends *as* (default
  `elcam.ai@elcam.co.il`).
- `REPORT_RECIPIENTS` — comma-separated recipient list (default: the sender).

If any `GRAPH_*` key is blank or `REPORT_RECIPIENTS` is empty, emailing is
disabled: the run logs an "email not configured" note and finishes normally.
A send failure never fails the run — it is logged as a note. The CLI
(`python -m solaranalysis.cli`) does not email.
```

- [ ] **Step 9: Commit**

```bash
git add solaranalysis/web/runner.py tests/web/test_runner.py README.md
git commit -m "feat: email report after each web-app run via Graph mailer"
```

---

## Self-Review

**1. Spec coverage:**
- §1 Purpose (email each web run's report) → Task 2 hook. ✓
- §2 Config surface (5 env keys, dark-when-unconfigured) → Task 1 `is_configured`/`recipients` + `.env.example` (Step 5); dark behavior tested in Task 2 Step 1. ✓
- §3 Mailer module (`is_configured`, `recipients`, `get_token`, `send_report`) → Task 1. ✓
- §4 Hook point (after status computed, reuse `html`, subject with status) → Task 2 Step 5. ✓
- §5 Failure isolation + `GRAPH_CLIENT_SECRET` redaction → Task 2 Steps 4 (collect_secrets) & 5 (try/except note); tested Task 2 Step 1. ✓
- §6 Testing (mailer unit + runner behaviors) → Task 1 Step 1, Task 2 Step 1. ✓
- §7 Docs (.env.example + README) → Task 1 Step 5, Task 2 Step 8. ✓

**2. Placeholder scan:** No TBD/TODO; every code and test step shows complete content. ✓

**3. Type consistency:** `is_configured() -> bool`, `recipients() -> list[str]`, `get_token(tenant, client_id, client_secret, http_post)`, `send_report(subject, html_body, http_post)` — names/signatures identical between the Task 1 module, the Task 1 tests, and the Task 2 runner calls and tests. Event names `report_emailed`/`note` consistent. ✓

---

### Task 3: Email-safe report rendering

**Added after the whole-branch review.** The whole-branch review found that the on-disk report (`core/report.py::render_html`) styles everything through CSS custom properties (`var(--bg)`, etc.) in a `<head><style>` block. Outlook (Word engine) and Gmail don't support CSS variables, so the emailed report renders plain/unstyled. This task adds an email-optimized renderer — **light theme, styles inlined onto each element, no CSS variables, table-wrapper layout** — and sends that variant. The on-disk `render_html` (dark theme) is unchanged.

**Files:**
- Modify: `solaranalysis/core/report.py` (add `import re`; add `render_email_html` + helpers; do NOT change `render_html`/`write_report`)
- Test: `tests/test_report.py` (add email-render tests)
- Modify: `solaranalysis/web/runner.py` (import `render_email_html`; send it instead of the dark `html`)
- Test: `tests/web/test_runner.py` (add one email-safe-body assertion test)
- Modify: `README.md` (one sentence in the Email delivery section)

**Interfaces:**
- Consumes: `md.markdown(...)` (already used by `render_html`).
- Produces (used by runner): `render_email_html(report_md: str, title: str, subtitle: str) -> str`.

- [ ] **Step 1: Write the failing report tests**

Append to `tests/test_report.py`:

```python
from solaranalysis.core.report import render_email_html


def test_render_email_html_has_no_css_variables():
    html = render_email_html("# Title\n\nSome text.", "Solar Fleet Analysis", "3 plants")
    assert "var(" not in html
    assert ":root" not in html


def test_render_email_html_inlines_table_styles():
    md_table = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = render_email_html(md_table, "T", "S")
    assert "<table style=" in html
    assert "<th style=" in html
    assert "<td style=" in html


def test_render_email_html_includes_title_subtitle_body():
    html = render_email_html("**bold** words", "My Title", "my subtitle")
    assert "My Title" in html
    assert "my subtitle" in html
    assert "<strong>bold</strong>" in html


def test_render_email_html_light_theme_and_inlined_paragraph():
    html = render_email_html("plain paragraph", "T", "S")
    assert "#f4f6f8" in html      # light page background
    assert "<p style=" in html    # paragraph styled inline
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_email_html'`.

- [ ] **Step 3: Add the email renderer to `core/report.py`**

In `solaranalysis/core/report.py`, add `import re` to the imports (after `import os`). Then add the following (place it after the existing `render_html` function; leave `render_html`, `_CSS`, `_TEMPLATE`, `write_report`, `append_unavailable_section` unchanged):

```python
# Email-safe rendering: mail clients (Outlook's Word engine, Gmail) do not
# support CSS custom properties and don't reliably honor <head><style>, so the
# email body uses a light theme with styles inlined onto each element.
_EMAIL_BODY_STYLES = {
    "h1": "margin:0;font-size:24px;color:#12202e;",
    "h2": "margin:28px 0 8px;font-size:19px;color:#a86500;",
    "h3": "margin:20px 0 6px;font-size:16px;color:#12202e;",
    "p": "margin:12px 0;color:#1a2330;font-size:15px;line-height:1.6;",
    "table": "width:100%;border-collapse:collapse;margin:16px 0;font-size:14px;",
    "th": "padding:9px 12px;text-align:left;background:#eef2f6;color:#12202e;border:1px solid #d5dde5;",
    "td": "padding:9px 12px;text-align:left;color:#1a2330;border:1px solid #d5dde5;",
    "ul": "margin:12px 0;padding-left:22px;color:#1a2330;font-size:15px;line-height:1.6;",
    "ol": "margin:12px 0;padding-left:22px;color:#1a2330;font-size:15px;line-height:1.6;",
    "li": "margin:4px 0;",
    "code": "background:#eef2f6;padding:2px 5px;border-radius:4px;font-family:Consolas,monospace;font-size:13px;",
    "a": "color:#a86500;",
}
_EMAIL_TAG_RE = re.compile(r"<(h1|h2|h3|p|table|th|td|ul|ol|li|code|a)(\s[^>]*)?>")

def _inline_email_styles(html: str) -> str:
    def repl(m):
        tag, attrs = m.group(1), m.group(2) or ""
        if "style=" in attrs:
            return m.group(0)
        return f'<{tag}{attrs} style="{_EMAIL_BODY_STYLES[tag]}">'
    return _EMAIL_TAG_RE.sub(repl, html)

_EMAIL_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;">
<tr><td style="padding:32px 28px;">
<h1 style="margin:0;font-size:24px;color:#12202e;">{title}</h1>
<div style="color:#5b6b7b;margin-top:6px;font-size:14px;">{subtitle}</div>
<div style="border-bottom:2px solid #f5b301;margin:16px 0 24px;font-size:0;line-height:0;">&nbsp;</div>
{body}
<div style="margin-top:40px;color:#5b6b7b;font-size:12px;border-top:1px solid #d5dde5;padding-top:16px;">Generated by solar-analysis · figures computed in Python, narrative by Claude.</div>
</td></tr></table>
</td></tr></table>
</body></html>"""

def render_email_html(report_md: str, title: str, subtitle: str) -> str:
    """Light-theme, inline-styled HTML body for email clients (no CSS
    variables, no reliance on <head> styles). Mirrors render_html's content
    but survives Outlook/Gmail. The on-disk report still uses render_html."""
    body = _inline_email_styles(md.markdown(report_md, extensions=["tables", "fenced_code"]))
    return _EMAIL_TEMPLATE.format(title=title, subtitle=subtitle, body=body)
```

- [ ] **Step 4: Run to verify report tests pass**

Run: `python -m pytest tests/test_report.py -q`
Expected: PASS (4 new + existing report tests).

- [ ] **Step 5: Send the email-safe body from the runner**

In `solaranalysis/web/runner.py`, add `render_email_html` to the report import (currently line 12):

```python
from ..core.report import render_html, write_report, append_unavailable_section
```
becomes:
```python
from ..core.report import (render_html, render_email_html, write_report,
                           append_unavailable_section)
```

Then in the email hook, change the send call from:
```python
                mailer.send_report(subject, html)
```
to:
```python
                mailer.send_report(
                    subject,
                    render_email_html(report_md, "Solar Fleet Analysis", subtitle))
```

(`report_md` and `subtitle` are already in scope at the hook — see runner lines 86 and 88.)

- [ ] **Step 6: Add a runner test for the email-safe body**

Append to `tests/web/test_runner.py` (reuses the `_seed_run` and `_success_pipeline` helpers added in Task 2):

```python
def test_run_job_emails_email_safe_body(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    _seed_run(paths)
    monkeypatch.setattr(runner, "run_pipeline", _success_pipeline)
    sent = []
    monkeypatch.setattr(runner.mailer, "is_configured", lambda: True)
    monkeypatch.setattr(runner.mailer, "recipients", lambda: ["me@x.com"])
    monkeypatch.setattr(runner.mailer, "send_report",
                        lambda subject, html: sent.append(html))
    runner.run_analysis_job(paths, run_id=1)
    assert len(sent) == 1
    assert "var(" not in sent[0]     # CSS custom properties not used in email body
    assert "style=" in sent[0]       # styles are inlined
```

- [ ] **Step 7: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS — all prior tests plus the new report + runner tests (no regressions).

- [ ] **Step 8: Update the README**

In `README.md`, in the "Email delivery" subsection, add this sentence at the end of the first paragraph (after "…`failed` runs send nothing."):

```markdown
The email body is rendered in an email-optimized light theme with inline
styles (Outlook/Gmail don't support the on-disk report's CSS variables); the
`report.html` saved on disk keeps its full styling.
```

- [ ] **Step 9: Commit**

```bash
git add solaranalysis/core/report.py tests/test_report.py solaranalysis/web/runner.py tests/web/test_runner.py README.md
git commit -m "feat: render email-safe (light, inline-styled) report body for delivery"
```
