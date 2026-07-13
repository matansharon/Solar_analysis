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
