from __future__ import annotations
from .schema import PlantData

_ENERGY_FIELDS = [
    "energy_today_kwh", "energy_month_kwh", "energy_year_kwh", "energy_lifetime_kwh",
]

def validate_plant(pd: PlantData) -> PlantData:
    flags = pd.data_quality_flags
    for name in _ENERGY_FIELDS:
        m = getattr(pd, name)
        if m.value is not None and m.value < 0:
            flags.append(f"{name}: negative energy value ({m.value})")
    if pd.current_power_kw.value is not None and pd.current_power_kw.value < 0:
        flags.append(f"current_power_kw: negative power value ({pd.current_power_kw.value})")
    pr = pd.performance_ratio.value
    if pr is not None and not (0.0 <= pr <= 1.0):
        flags.append(f"performance_ratio: out of range [0,1] ({pr})")
    yr = pd.energy_year_kwh.value
    life = pd.energy_lifetime_kwh.value
    if yr is not None and life is not None and life < yr:
        flags.append(f"energy_lifetime_kwh ({life}) < energy_year_kwh ({yr}): non-monotonic")
    for ep in pd.energy_timeseries:
        if ep.energy_kwh is not None and ep.energy_kwh < 0:
            flags.append(f"energy_timeseries@{ep.timestamp_local}: negative energy")
            break
    return pd
