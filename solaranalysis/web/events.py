from __future__ import annotations
import json
import sys

EVENT_PREFIX = "@@EVENT@@ "


class Redactor:
    def __init__(self, secrets):
        # Longest first so a secret that contains another is masked whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for s in self._secrets:
            text = text.replace(s, "***")
        return text


def emit_event(ev: dict) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps(ev) + "\n")
    sys.stdout.flush()


def parse_line(line: str):
    if line.startswith(EVENT_PREFIX):
        try:
            return "event", json.loads(line[len(EVENT_PREFIX):])
        except ValueError:
            return "log", line
    return "log", line
