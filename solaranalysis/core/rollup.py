from __future__ import annotations
from collections import defaultdict
from .schema import EnergyPoint, PlantData, TimeRange

def rollup_energy(points: list[EnergyPoint], target_granularity: str) -> list[EnergyPoint]:
    key_len = 7 if target_granularity == "month" else 4  # YYYY-MM or YYYY
    buckets: dict[str, float] = defaultdict(float)
    seen_null: dict[str, bool] = defaultdict(bool)
    for p in points:
        key = p.timestamp_local[:key_len]
        if p.energy_kwh is None:
            seen_null[key] = True
            continue
        buckets[key] += p.energy_kwh
    out = []
    for key in sorted(buckets):
        out.append(EnergyPoint(key, round(buckets[key], 3), target_granularity))
    return out

def worst_periods(points: list[EnergyPoint], n: int) -> list[EnergyPoint]:
    valid = [p for p in points if p.energy_kwh is not None]
    return sorted(valid, key=lambda p: p.energy_kwh)[:n]

def plan_rollup(pd: PlantData, time_range: TimeRange) -> dict:
    pts = pd.energy_timeseries
    if time_range == TimeRange.SNAPSHOT:
        return {"granularity": "none", "series": [], "worst": []}
    if time_range == TimeRange.LAST_30D:
        return {"granularity": "day", "series": pts, "worst": worst_periods(pts, 5)}
    # 12mo and all -> monthly + worst 5
    monthly = rollup_energy(pts, "month")
    return {"granularity": "month", "series": monthly, "worst": worst_periods(pts, 5)}
