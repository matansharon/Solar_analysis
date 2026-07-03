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

def to_float(x, *, strip_commas: bool = False, none_tokens: tuple[str, ...] = ()) -> float | None:
    """Shared lenient numeric coercion for vendor payloads.

    Empty strings and any of ``none_tokens`` (case-insensitive) map to None.
    ``strip_commas`` removes thousands separators — only enable it for sources
    verified to use '.' as the decimal separator.
    """
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() in none_tokens:
        return None
    if strip_commas:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None
