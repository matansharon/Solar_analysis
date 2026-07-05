from __future__ import annotations
import json
import sys

EVENT_PREFIX = "@@EVENT@@ "


class Redactor:
    def __init__(self, secrets):
        # Deduplicated, empty/None dropped. Order does not matter: redact()
        # masks the union of all occurrence spans, so overlapping or
        # equal-length secrets can't leave a fragment.
        self._secrets = {s for s in secrets if s}

    def redact(self, text: str) -> str:
        if not text or not self._secrets:
            return text
        spans = []
        for s in self._secrets:
            start = 0
            while True:
                i = text.find(s, start)
                if i < 0:
                    break
                spans.append((i, i + len(s)))
                start = i + 1  # allow overlapping matches of the same secret
        if not spans:
            return text
        spans.sort()
        merged = []
        cur_start, cur_end = spans[0]
        for a, b in spans[1:]:
            if a <= cur_end:          # overlapping or touching -> extend
                cur_end = max(cur_end, b)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = a, b
        merged.append((cur_start, cur_end))
        out, prev = [], 0
        for a, b in merged:
            out.append(text[prev:a])
            out.append("***")
            prev = b
        out.append(text[prev:])
        return "".join(out)


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
