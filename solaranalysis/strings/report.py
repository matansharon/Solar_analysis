"""Grounded Growatt string anomaly report: Python computes every figure; the
Claude call writes narrative prose only (mirrors optimizers/report.py)."""
from __future__ import annotations
import os
from ..web import mailer

_SEVERITY_ORDER = ("dead", "fault", "underperforming", "imbalance",
                   "degrading", "watch")


def _device_label(device_sn, device_names=None):
    name = (device_names or {}).get(device_sn)
    return f"{name} ({device_sn})" if name else device_sn


def build_anomaly_block(analyses, as_of_day, device_names=None) -> str:
    """An authoritative text block of per-inverter flagged counts by severity
    plus each finding's measured figures. Every number comes straight from a
    `StringAnomaly` field -- no figure is invented here or by the narrative
    that reads it."""
    parts = [f"=== GROWATT STRING ANOMALIES {as_of_day} "
             "(authoritative; do not go beyond it) ==="]
    for sn in sorted(analyses):
        anoms = analyses[sn]
        counts = {s: sum(1 for a in anoms if a.severity == s)
                  for s in _SEVERITY_ORDER}
        counts_str = ", ".join(f"{s}={counts[s]}" for s in _SEVERITY_ORDER)
        parts.append(f"\n--- inverter {_device_label(sn, device_names)} --- "
                     f"flagged={len(anoms)} ({counts_str})")
        for a in anoms:
            bits = [f"  [{a.severity}] {a.label} ({a.scope})"]
            if a.energy_kwh is not None:
                bits.append(f"energy={a.energy_kwh} kWh")
            if a.share_of_total is not None:
                bits.append(f"share_of_total={a.share_of_total}")
            if a.metric is not None:
                bits.append(f"measured={a.metric:.5g}")
            if a.baseline is not None:
                bits.append(f"own_baseline={a.baseline:.5g}")
            parts.append(", ".join(bits) + f"; {a.reason}")
    parts.append(
        "\nContext for interpretation: every rule compares a channel against "
        "its OWN trailing history, never against the other channels -- the "
        "MPPT inputs carry deliberately unequal shares and each string pair "
        "sits at its own steady imbalance, so peer comparison is meaningless "
        "here. 'imbalance' findings are per string-pair; 'dead' means the "
        "channel stopped reporting or produced nothing.")
    return "\n".join(parts)


_SYSTEM = (
    "You are a solar-plant analyst. You are given an authoritative block of "
    "per-string and per-MPPT anomaly findings from one Growatt inverter, "
    "computed in Python. Write a short operator-facing narrative that "
    "summarizes the plant's string health and calls out what to physically "
    "check first. Use ONLY the figures in the block — never invent numbers, "
    "channel names or serials. Be concise.")


def narrate(block: str, lang: str, client=None) -> str:
    """Grounded narrative for the anomaly block. `client` injectable for tests."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user = block + f"\n\nWrite the narrative in {lang}. Base every figure on the block above."
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def subject(as_of_day: str, total_flagged: int) -> str:
    return f"Growatt Strings · {total_flagged} flagged · {as_of_day}"


def resolve_recipients() -> list[str]:
    """STRING_RECIPIENTS overrides the app's configured list, same pattern as
    OPTIMIZER_RECIPIENTS."""
    raw = os.getenv("STRING_RECIPIENTS", "").strip()
    if raw:
        out, seen = [], set()
        for part in raw.split(","):
            a = part.strip()
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out
    return mailer.recipients()


def _fmt(v, spec=".5g"):
    return "" if v is None else format(v, spec)


def _table(anoms) -> str:
    rows = ["| Severity | Channel | Energy kWh | Share | Measured | Own baseline | Finding |",
            "|---|---|---|---|---|---|---|"]
    for a in anoms:
        rows.append(
            f"| {a.severity} | {a.label} | {_fmt(a.energy_kwh, '.1f')} | "
            f"{_fmt(a.share_of_total, '.5f')} | {_fmt(a.metric)} | "
            f"{_fmt(a.baseline)} | {a.reason} |")
    return "\n".join(rows)


def render_report_md(analyses, narrative, as_of_day, device_names=None) -> str:
    parts = [f"## Growatt Strings — {as_of_day}", ""]
    if narrative:
        parts += [narrative.strip(), ""]
    for sn in sorted(analyses):
        anoms = analyses[sn]
        parts.append(f"### Inverter {_device_label(sn, device_names)}")
        parts.append("")
        if anoms:
            parts.append(_table(anoms))
        else:
            parts.append("_all clear — no strings or inputs flagged._")
        parts.append("")
    return "\n".join(parts)
