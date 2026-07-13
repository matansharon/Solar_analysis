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
