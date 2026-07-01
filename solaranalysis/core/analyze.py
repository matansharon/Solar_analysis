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
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")

def pick_model(cfg: AppConfig, time_range: TimeRange) -> str:
    if cfg.model:
        return cfg.model
    if time_range in (TimeRange.LAST_12MO, TimeRange.ALL):
        return "claude-opus-4-8"
    return "claude-sonnet-5"

def _summary(pd: PlantData) -> dict:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    return {
        "plant_id": pd.plant_id,
        "plant_name": pd.plant_name,
        "vendor": pd.source_platform,
        "kwp": kwp,
        "energy_today_kwh": pd.energy_today_kwh.value,
        "energy_month_kwh": pd.energy_month_kwh.value,
        "energy_year_kwh": pd.energy_year_kwh.value,
        "energy_lifetime_kwh": life,
        "current_power_kw": pd.current_power_kw.value,
        "specific_yield_lifetime_kwh_per_kwp": units.round_opt(units.specific_yield(life, kwp)),
        "device_count": len(pd.devices),
        "devices_online": sum(1 for d in pd.devices if d.status.value == "online"),
        "alert_count": len(pd.alerts),
        "revenue": pd.revenue.value,
        "currency": pd.currency,
        "co2_avoided_kg": pd.co2_avoided_kg.value,
        "data_quality_flags": pd.data_quality_flags,
    }

def _csv_table(rollup: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["period", "energy_kwh"])
    for p in rollup["series"]:
        w.writerow([p.timestamp_local, p.energy_kwh])
    return buf.getvalue()

def build_data_block(plants: list[PlantData], time_range: TimeRange, meta: dict) -> str:
    parts = ["=== DATA (authoritative; do not go beyond it) ==="]
    parts.append("META: " + json.dumps({**meta, "range": time_range.value}, sort_keys=True))
    for pd in plants:
        parts.append(f"\n--- PLANT {pd.plant_id} ---")
        parts.append("SUMMARY: " + json.dumps(_summary(pd), sort_keys=True))
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
        return tok.replace(",", "").rstrip(".")
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

def run_analysis(plants, time_range, cfg: AppConfig, client=None) -> str:
    meta = {"currency": (plants[0].currency if plants else None)}
    data_block = build_data_block(plants, time_range, meta)
    model = pick_model(cfg, time_range)
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
