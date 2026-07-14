from __future__ import annotations
import io
import csv
import json
import re
from pathlib import Path
from .schema import PlantData, TimeRange
from .rollup import plan_rollup
from . import units
from ..config import AppConfig

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"
_EXEC_SUMMARY_PROMPT_PATH = (Path(__file__).resolve().parent.parent
                             / "prompts" / "exec_summary.txt")
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")

def pick_model(cfg: AppConfig, time_range: TimeRange, plants=None) -> str:
    if cfg.model:
        return cfg.model
    # Long ranges only justify the bigger model when there is actually
    # historical series data to analyze (adapters may expose counters only).
    if time_range in (TimeRange.LAST_12MO, TimeRange.ALL) and plants \
            and any(p.energy_timeseries for p in plants):
        return "claude-opus-4-8"
    return "claude-sonnet-5"

def default_meta(plants: list[PlantData]) -> dict:
    return {"currency": plants[0].currency if plants else None}

_MAX_ALERT_DETAIL = 10

def _summary(pd: PlantData) -> dict:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    today = pd.energy_today_kwh.value
    month = pd.energy_month_kwh.value
    return {
        "plant_id": pd.plant_id,
        "plant_name": pd.plant_name,
        "vendor": pd.source_platform,
        "kwp": kwp,
        "install_date": pd.install_date,
        "energy_today_kwh": today,
        "energy_month_kwh": month,
        "energy_year_kwh": pd.energy_year_kwh.value,
        "energy_lifetime_kwh": life,
        "current_power_kw": pd.current_power_kw.value,
        # Age-fair current-performance metrics (energy over the period / kWp).
        "specific_yield_today_kwh_per_kwp": units.round_opt(units.specific_yield(today, kwp)),
        "specific_yield_month_kwh_per_kwp": units.round_opt(units.specific_yield(month, kwp)),
        # Lifetime yield scales with plant age (see install_date) — not age-fair.
        "specific_yield_lifetime_kwh_per_kwp": units.round_opt(units.specific_yield(life, kwp)),
        "device_count": len(pd.devices),
        "devices_online": sum(1 for d in pd.devices if d.status.value == "online"),
        "alert_count": len(pd.alerts),
        # Bounded alert detail (severity/code/message/time) so the report can
        # name actual faults instead of just counting them.
        "alerts": [{"severity": a.severity.value, "code": a.code,
                    "message": a.message, "timestamp": a.timestamp_local}
                   for a in pd.alerts[:_MAX_ALERT_DETAIL]],
        "extras": pd.extras,
        "revenue": pd.revenue.value,
        "savings": pd.savings.value,
        "currency": pd.currency,
        "co2_avoided_kg": pd.co2_avoided_kg.value,
        # Vendors are fetched sequentially and lag differently; expose when the
        # data was pulled/reported so same-day comparisons can be qualified.
        "fetched_at_utc": pd.fetched_at_utc,
        "reporting_timestamp_utc": pd.reporting_timestamp_utc,
        "data_quality_flags": pd.data_quality_flags,
    }

def _csv_table(rollup: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["period", "energy_kwh"])
    for p in rollup["series"]:
        w.writerow([p.timestamp_local, p.energy_kwh])
    return buf.getvalue()

def build_data_block(plants: list[PlantData], time_range: TimeRange, meta: dict,
                     include_series: bool = True) -> str:
    parts = ["=== DATA (authoritative; do not go beyond it) ==="]
    parts.append("META: " + json.dumps({**meta, "range": time_range.value}, sort_keys=True))
    if time_range != TimeRange.SNAPSHOT and not any(p.energy_timeseries for p in plants):
        parts.append(
            "NOTE: no historical time series is available from any source for this "
            "range; only current counters (today/month/year/lifetime totals) are "
            "provided below. Do not fabricate per-period analysis.")
    for pd in plants:
        parts.append(f"\n--- PLANT {pd.plant_id} ---")
        parts.append("SUMMARY: " + json.dumps(_summary(pd), sort_keys=True))
        if not include_series:
            continue
        roll = plan_rollup(pd, time_range)
        if roll["series"]:
            parts.append(f"SERIES ({roll['granularity']}):")
            parts.append(_csv_table(roll))
        if roll["worst"]:
            worst = ", ".join(f"{p.timestamp_local}={p.energy_kwh}" for p in roll["worst"])
            parts.append(f"WORST_PERIODS: {worst}")
    return "\n".join(parts)

def verify_numbers(report_md: str, data_block: str) -> list[str]:
    def _norm(tok):
        t = tok.replace(",", "")
        if "." in t:  # canonicalize 189.00 / 189.0 / 189. -> 189
            t = t.rstrip("0").rstrip(".")
        return t
    present = {_norm(m) for m in _NUM_RE.findall(data_block)}
    present.discard("")
    present.discard("-")
    missing = []
    for m in _NUM_RE.finditer(report_md):
        norm = _norm(m.group())
        if norm in ("", "-"):
            continue
        if report_md[m.end():m.end() + 1] == "%":
            continue  # percentages are derived, not expected verbatim in DATA
        if norm not in present:
            missing.append(norm)
    return missing

def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")

def _exec_summary_prompt() -> str:
    return _EXEC_SUMMARY_PROMPT_PATH.read_text(encoding="utf-8")

def _estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1  # ~4 chars/token heuristic

def run_analysis(plants, time_range, cfg: AppConfig, client=None) -> str:
    meta = default_meta(plants)
    data_block = build_data_block(plants, time_range, meta)
    if _estimate_tokens(data_block) > cfg.max_input_tokens:
        data_block = build_data_block(plants, time_range, meta, include_series=False)
        data_block += "\nNOTE: energy series omitted to fit max_input_tokens."
        if _estimate_tokens(data_block) > cfg.max_input_tokens:
            raise ValueError(
                f"data block (~{_estimate_tokens(data_block)} tokens) exceeds "
                f"max_input_tokens={cfg.max_input_tokens}; raise it in config.yaml")
    model = pick_model(cfg, time_range, plants)
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    lang = "Hebrew" if cfg.output_language == "he" else "English"
    user = (data_block + f"\n\nProduce the report in {lang} for time range: "
            f"{time_range.value}. Base every number on the DATA above.")
    msg = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": _system_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

def summarize_executive(report_md: str, client=None) -> str:
    """Distill an already-generated report into a concise Hebrew executive
    summary ("סיכום מנהלים"), returned as markdown.

    Uses Claude Opus 4.8 at "xhigh" reasoning — on Opus 4.8 that is
    output_config effort="xhigh" plus adaptive thinking (the fixed
    thinking.budget_tokens knob is rejected there). `client` is injectable for
    tests, mirroring run_analysis."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user = report_md + "\n\nכתוב סיכום מנהלים תמציתי בעברית של הדוח שלמעלה."
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=[{"type": "text", "text": _exec_summary_prompt(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
