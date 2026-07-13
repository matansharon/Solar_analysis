# Email Report Delivery — Design

Date: 2026-07-13
Status: draft — pending user review

## 1. Purpose

Every web-app run already produces a self-contained styled HTML report
(`runner.py::run_analysis_job` → `render_html` → `write_report`, emitting a
`report_written` event with the on-disk path). Today that report only lives on
disk under `<data-dir>/output/<stamp>/report.html` and is surfaced through the
web UI, which is now treated as a debugging surface.

This project delivers each run's report by **email** using Microsoft Graph
(app-only / client-credentials `sendMail`), so the operator receives the
report in their inbox without opening the web app. Recipients currently default
to a single address (`elcam.ai@elcam.co.il`) but the mechanism supports a list.

### Non-goals

- **CLI email.** `python -m solaranalysis.cli` stays email-free; only runs
  launched through the web/runner path (manual *and* scheduled) email. If CLI
  email is wanted later, the mailer module lifts to `solaranalysis/mailer.py`
  unchanged and is called from `cli.py` too.
- **Attachments.** The report is delivered as the inline HTML email body only.
  No `report.html` attachment (can be added later without redesign).
- **Failure-notice emails.** Only runs that produce a report (status `success`
  or `partial`) email. `failed` runs produce no report and send nothing — a
  missing email is the (accepted) failure signal.
- **In-app configuration.** All Graph config lives in `.env` (matching
  `ANTHROPIC_API_KEY` and the CLI's portal creds). No settings-table column,
  no Settings-page field, no DB-encrypted Graph secret.
- **Per-recipient personalization / retries / queueing.** One email, one
  attempt, all recipients. A send failure is logged and dropped (see §5).

## 2. Configuration surface (`.env`)

Five new keys, read via `os.getenv` after `runner.py` already calls
`load_dotenv(paths.env_file)`:

| Key | Meaning | Default in `.env.example` |
|-----|---------|---------------------------|
| `GRAPH_TENANT_ID` | Azure AD tenant id | *(blank)* |
| `GRAPH_CLIENT_ID` | App registration (client) id | *(blank)* |
| `GRAPH_CLIENT_SECRET` | App client secret | *(blank)* |
| `GRAPH_SENDER` | Mailbox the app sends **as** | `elcam.ai@elcam.co.il` |
| `REPORT_RECIPIENTS` | Comma-separated recipient list | `elcam.ai@elcam.co.il` |

The app registration must be granted the **Mail.Send *application* permission**
(admin-consented) on the `GRAPH_SENDER` mailbox for app-only send to succeed.

If any of `GRAPH_TENANT_ID` / `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` /
`GRAPH_SENDER` is unset, or `REPORT_RECIPIENTS` resolves to an empty list, the
feature is **dark**: the run emits a `note` event ("email not configured;
skipping") and finishes normally. This lets the feature ship before the secrets
are populated.

## 3. New module — `solaranalysis/web/mailer.py`

Dependency-light, modeled on the existing `claude_pioneers/pioneers/emailer.py`:
plain `requests` + the OAuth2 client-credentials flow, **no new `msal`
dependency** (`requests` is already in `requirements.txt`). All functions take
an injectable `http_post` (default `requests.post`) so tests need no network.

```python
GRAPH = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_SECRET_KEYS = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER")

def is_configured() -> bool
    # True only if all four _SECRET_KEYS env vars are non-empty.

def recipients() -> list[str]
    # Parse REPORT_RECIPIENTS: split on ",", strip, drop blanks, dedupe
    # (order-preserving). Empty/unset -> [].

def get_token(tenant, client_id, secret, http_post=requests.post) -> str
    # POST client_credentials to _TOKEN_URL with
    # scope=https://graph.microsoft.com/.default; raise RuntimeError on non-OK.

def send_report(subject, html_body, http_post=requests.post) -> None
    # sender = os.getenv("GRAPH_SENDER")
    # POST {GRAPH}/users/{sender}/sendMail with one message:
    #   body.contentType = "HTML", body.content = html_body,
    #   toRecipients = [{"emailAddress": {"address": r}} for r in recipients()],
    #   saveToSentItems = True
    # Authorization: Bearer <get_token(...)>. Raise RuntimeError on non-2xx
    # (Graph returns 202 Accepted on success).
```

`send_report` reads tenant/client/secret/sender/recipients from env internally
so the caller only supplies subject + body.

## 4. Hook point — `runner.py::run_analysis_job`

The report HTML is already rendered into the local `html` variable and written
to disk. Immediately after `write_report(...)` and the `report_written` event,
and after `status` is computed (`"partial"` if any skipped else `"success"`),
send the email — reusing `html` in scope (no re-read from disk):

```python
subject = (f"Solar Fleet Analysis · {status} · {len(res['plants'])} plants "
           f"· range {run['time_range']} · {stamp} UTC")
try:
    if mailer.is_configured() and mailer.recipients():
        mailer.send_report(subject, html)
        events.emit_event({"event": "report_emailed",
                           "to": mailer.recipients()})
    else:
        events.emit_event({"event": "note",
                           "reason": "email not configured; skipping"})
except Exception as e:
    events.emit_event({"event": "note",
                       "reason": red.redact(f"email send failed: {e}")})
```

Both `success` and `partial` reach this code (a report exists). The `failed`
branch of `run_analysis_job` (the `except` that emits `run_complete`
status=`failed`) is untouched — no report, no email.

## 5. Failure isolation & secret safety

- **Non-fatal send.** An email failure emits a redacted `note` event and the
  run still finalizes as `success`/`partial`, mirroring the existing
  measurement-persistence pattern (`persist()` in the same function). Email is
  a delivery side effect, not a run-success criterion.
- **Redaction.** `runner.py::collect_secrets` gains `GRAPH_CLIENT_SECRET`
  (when set) so it is fed to the `events.Redactor`. Graph error text echoes API
  responses rather than the secret, but this guards against any accidental
  echo into the log/SSE stream.

## 6. Testing

Matching the existing style (`tests/web/`, fake `http_post`, no network):

`tests/web/test_mailer.py`
- `is_configured()` true only when all four keys set; false if any missing
  (monkeypatch env).
- `recipients()` parsing: single, multiple, surrounding spaces, trailing
  comma, blank entries, unset → `[]`, dedupe.
- `get_token()` posts the client-credentials body (grant_type, scope,
  client_id, client_secret) to the tenant token URL; returns `access_token`;
  raises on a non-OK fake response.
- `send_report()` builds the correct `sendMail` payload (HTML contentType,
  body == html, one `toRecipients` entry per recipient, `saveToSentItems`),
  targets `/users/{sender}/sendMail`, sends `Authorization: Bearer`, and raises
  on a non-2xx fake response.

`tests/web/test_runner.py` (additions, using existing runner test harness)
- Emits `report_emailed` on a `success` run when configured (mailer patched to
  record the call).
- Emits `report_emailed` on a `partial` run (one plant skipped).
- Skips send + emits the "email not configured" note when env is unset.
- A mailer exception does **not** change the run's terminal status
  (still `success`/`partial`); a redacted `note` is emitted.

## 7. Docs

- `.env.example`: add the five keys from §2 with the documented defaults and a
  one-line comment on the required Mail.Send application permission.
- `README.md`: a short "Email delivery" subsection under the Web UI docs
  describing the env keys, the app-permission requirement, the
  success/partial-only + inline-body behavior, and that the feature is dark
  until configured.
