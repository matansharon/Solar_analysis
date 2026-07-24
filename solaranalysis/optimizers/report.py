"""Grounded optimizer anomaly report: Python computes every figure; the Claude
call writes narrative prose only (mirrors core.analyze.summarize_executive)."""
from __future__ import annotations

_SEVERITY_ORDER = ("dead", "underperforming", "degrading", "watch")


def _site_label(site_id, site_names):
    name = (site_names or {}).get(site_id)
    return f"{name} (site {site_id})" if name else f"site {site_id}"


def build_anomaly_block(analyses, site_names=None) -> str:
    """Build an authoritative text block of per-site flagged counts by
    severity plus each flagged optimizer's identifying/energy figures. Every
    number in this block comes straight from `OptimizerAnomaly` fields — no
    figure is invented here or by the narrative that reads it."""
    parts = ["=== OPTIMIZER ANOMALIES (authoritative; do not go beyond it) ==="]
    for site_id in sorted(analyses):
        anoms = analyses[site_id]
        counts = {s: sum(1 for a in anoms if a.severity == s) for s in _SEVERITY_ORDER}
        counts_str = ", ".join(f"{s}={counts[s]}" for s in _SEVERITY_ORDER)
        parts.append(f"\n--- {_site_label(site_id, site_names)} --- "
                     f"flagged={len(anoms)} ({counts_str})")
        for a in anoms:
            parts.append(
                f"  [{a.severity}] optimizer {a.label or a.optimizer_serial} "
                f"(S/N {a.optimizer_serial}, string {a.string_label}): "
                f"energy={a.latest_energy_wh} Wh, color={a.latest_color}, "
                f"string_median={a.string_median_wh} Wh, "
                f"ratio_to_string={a.ratio_to_string}; {a.reason}")
    return "\n".join(parts)


_SYSTEM = (
    "You are a solar-fleet analyst. You are given an authoritative block of "
    "per-optimizer anomaly findings that were computed in Python. Write a short "
    "operator-facing narrative that summarizes the fleet's optimizer health and "
    "calls out the most urgent optimizers to check. Use ONLY the figures in the "
    "block — never invent numbers or serials. Be concise.")


def narrate(block: str, lang: str, client=None) -> str:
    """Grounded narrative for the anomaly block. `client` injectable for tests."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user = block + f"\n\nWrite the narrative in {lang}. Base every figure on the block above."
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
