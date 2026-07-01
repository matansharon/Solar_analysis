from __future__ import annotations

def w_to_kw(watts: float | None) -> float | None:
    return None if watts is None else watts / 1000.0

def wh_to_kwh(wh: float | None) -> float | None:
    return None if wh is None else wh / 1000.0

def specific_yield(energy_kwh: float | None, kwp: float | None) -> float | None:
    if energy_kwh is None or not kwp:
        return None
    return energy_kwh / kwp

def capacity_factor(energy_kwh: float | None, kwp: float | None, hours: float | None) -> float | None:
    if energy_kwh is None or not kwp or not hours:
        return None
    return energy_kwh / (kwp * hours)

def money(energy_kwh: float | None, tariff_per_kwh: float | None) -> float | None:
    if energy_kwh is None or tariff_per_kwh is None:
        return None
    return energy_kwh * tariff_per_kwh

def round_opt(x: float | None, ndigits: int = 2) -> float | None:
    return None if x is None else round(x, ndigits)
