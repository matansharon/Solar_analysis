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
