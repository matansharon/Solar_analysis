from solaranalysis.core.dashboard import compose_dashboard


class _Msg:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]


class _ShellClient:
    def __init__(self, text):
        self._text = text
        client = self
        class messages:
            @staticmethod
            def create(**kw):
                client.kwargs = kw
                return _Msg(client._text)
        self.messages = messages


class _BoomClient:
    def __init__(self):
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("opus down")
        self.messages = messages


_SHELL = "<html><body><main>{{SUMMARY}}</main><section>{{CHARTS}}</section></body></html>"


def test_compose_dashboard_substitutes_tokens_with_grounded_content():
    out = compose_dashboard("**סיכום מנהלים בדיקה**", "<table>BARCHART</table>",
                            client=_ShellClient(_SHELL))
    assert "BARCHART" in out                       # charts embedded verbatim
    assert "<strong>" in out                        # summary markdown rendered
    assert "{{SUMMARY}}" not in out and "{{CHARTS}}" not in out
    assert 'dir="rtl"' in out                        # Hebrew summary is RTL


def test_compose_dashboard_falls_back_when_tokens_missing():
    out = compose_dashboard("**S**", "<table>BARCHART</table>",
                            client=_ShellClient("<html>no placeholders here</html>"))
    assert "BARCHART" in out                          # fallback still carries charts
    assert "<strong>S</strong>" in out                # and the summary


def test_compose_dashboard_falls_back_when_llm_raises():
    out = compose_dashboard("**S**", "<table>BARCHART</table>", client=_BoomClient())
    assert "BARCHART" in out
    assert "<strong>S</strong>" in out


def test_compose_dashboard_substitutes_date_token():
    shell = "<html><body>{{DATE}}<main>{{SUMMARY}}</main>{{CHARTS}}</body></html>"
    out = compose_dashboard("**S**", "<b>c</b>", client=_ShellClient(shell),
                            date_str="16.07.2026")
    assert "16.07.2026" in out
    assert "{{DATE}}" not in out


def test_compose_dashboard_strips_unfilled_date_token():
    shell = "<html><body>{{DATE}}<main>{{SUMMARY}}</main>{{CHARTS}}</body></html>"
    out = compose_dashboard("**S**", "<b>c</b>", client=_ShellClient(shell))
    assert "{{DATE}}" not in out


def test_compose_dashboard_injects_hidden_preheader():
    # The summary's first line becomes the inbox preview snippet — hidden
    # right after <body> so Gmail/Outlook show it in the message list.
    out = compose_dashboard("**המתקן המוביל** הפיק 104 kWh/kWp.\n\nעוד טקסט.",
                            "<b>c</b>", client=_ShellClient(_SHELL))
    assert "display:none" in out
    pre = out[:out.index("<main>")]
    assert "המתקן המוביל הפיק" in pre


def test_compose_dashboard_fallback_has_date_and_preheader():
    out = compose_dashboard("**ראשון** ואז שני.", "<b>c</b>",
                            client=_BoomClient(), date_str="16.07.2026")
    assert "16.07.2026" in out
    assert "display:none" in out


def test_compose_dashboard_request_shape():
    c = _ShellClient(_SHELL)
    compose_dashboard("**S**", "<i>c</i>", client=c)
    assert c.kwargs["model"] == "claude-opus-4-8"
    assert c.kwargs["output_config"] == {"effort": "xhigh"}
    assert c.kwargs["thinking"] == {"type": "adaptive"}
    assert "temperature" not in c.kwargs


def test_compose_dashboard_fallback_is_email_safe():
    out = compose_dashboard("**S**", "<table>C</table>", client=_BoomClient())
    assert "<script" not in out and "<svg" not in out
    assert "var(" not in out
    assert "<!doctype html" in out.lower()            # self-contained document
