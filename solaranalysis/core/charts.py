from __future__ import annotations
import json
import re
from pathlib import Path
from html import escape as _escape
from typing import Callable

from .schema import PlantData
from . import units

_CHARTS_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "charts.txt"

# Whitelist of chartable metrics. Each entry: (label, unit, extractor).
# The extractor pulls the authoritative value straight from PlantData, so every
# charted number is Python-computed — the model only chooses WHICH metric to
# chart, never the values.
CHART_METRICS: dict[str, tuple[str, str, Callable[[PlantData], float | None]]] = {
    "specific_yield_today": ("Specific yield today", "kWh/kWp",
        lambda pd: units.round_opt(units.specific_yield(pd.energy_today_kwh.value,
                                                        pd.peak_power_kwp.value))),
    "specific_yield_month": ("Specific yield this month", "kWh/kWp",
        lambda pd: units.round_opt(units.specific_yield(pd.energy_month_kwh.value,
                                                        pd.peak_power_kwp.value))),
    "energy_today": ("Energy today", "kWh", lambda pd: pd.energy_today_kwh.value),
    "energy_month": ("Energy this month", "kWh", lambda pd: pd.energy_month_kwh.value),
    "current_power": ("Current power", "kW", lambda pd: pd.current_power_kw.value),
    "co2_avoided": ("CO₂ avoided", "kg", lambda pd: pd.co2_avoided_kg.value),
}

_BAR_COLOR = "#f5b301"
_BAR_TRACK = "#eef2f6"


def _charts_prompt() -> str:
    return _CHARTS_PROMPT_PATH.read_text(encoding="utf-8")


def _parse_specs(text: str) -> list[dict]:
    """Extract a JSON array of chart specs from the model text (tolerant of
    ```json fences / surrounding prose) and keep only whitelisted metrics."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    specs = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        if metric not in CHART_METRICS:
            continue
        specs.append({"metric": metric,
                      "title": str(item.get("title") or CHART_METRICS[metric][0]),
                      "insight": str(item.get("insight") or "")})
    return specs[:4]


def design_charts(data_summary: str, client=None) -> list[dict]:
    """Ask Claude Opus 4.8 (xhigh reasoning) which cross-plant comparisons are
    most worth charting. Returns validated {metric, title, insight} specs whose
    `metric` is always a CHART_METRICS key — the model never supplies chart
    values. `client` is injectable for tests (mirrors run_analysis)."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    allowed = ", ".join(CHART_METRICS)
    user = (data_summary +
            f"\n\nChoose 2-4 of these metrics whose cross-plant comparison is most "
            f"worth showing management: {allowed}.\n"
            'Return ONLY a JSON array; each item {"metric", "title", "insight"} '
            "where insight is one short sentence grounded in the data above. "
            "No prose outside the JSON.")
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _charts_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    return _parse_specs(text)


def _fmt(v: float) -> str:
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _one_chart(spec: dict, plants: list[PlantData]) -> str:
    label, unit, extract = CHART_METRICS[spec["metric"]]
    rows = [(pd.plant_name, float(extract(pd)))
            for pd in plants if extract(pd) is not None]
    if not rows:
        return ""
    maxv = max(v for _, v in rows) or 1.0
    bars = []
    for name, v in rows:
        pct = max(2, round(v / maxv * 85))   # cap at 85% so the value label fits
        bars.append(
            '<tr>'
            f'<td style="padding:4px 8px 4px 0;font-size:13px;color:#1a2330;">'
            f'{_escape(name)}</td>'
            '<td style="padding:4px 0;width:99%;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="{pct}%" style="background:{_BAR_COLOR};height:16px;'
            'border-radius:3px;font-size:0;line-height:0;">&nbsp;</td>'
            '<td style="padding-left:8px;font-size:13px;color:#1a2330;'
            f'white-space:nowrap;">{_fmt(v)} {_escape(unit)}</td>'
            '</tr></table></td></tr>')
    title = _escape(spec["title"])
    insight = _escape(spec.get("insight") or "")
    insight_html = (f'<div style="font-size:12px;color:#5b6b7b;margin:2px 0 10px;">'
                    f'{insight}</div>' if insight else "")
    return ('<div style="margin:0 0 22px;">'
            f'<div style="font-size:15px;font-weight:bold;color:#12202e;'
            f'margin-bottom:2px;">{title}</div>{insight_html}'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{_BAR_TRACK};border-radius:8px;">'
            f'{"".join(bars)}</table></div>')


def render_charts(specs: list[dict], plants: list[PlantData]) -> str:
    """Render each spec into an email-safe CSS/HTML bar chart, with every bar
    value read straight from PlantData (grounded). Charts with no data for any
    plant are omitted."""
    parts = [_one_chart(s, plants) for s in specs if s.get("metric") in CHART_METRICS]
    return "".join(p for p in parts if p)
