"""Shared helpers for the deep-fetch (history/devices/alerts) adapter paths.

Every *new* portal endpoint call goes through ``safe_step`` so that a missing
chart, a renamed endpoint or a portal hiccup degrades that one datum to a
``data_quality_flags`` note — it must never fail the plant (the pipeline
already isolates per-plant failures; this keeps isolation per-endpoint too).

The date-window helpers are pure so the time_range -> portal-call mapping is
unit-testable without a browser.
"""
from __future__ import annotations
from datetime import date

from ..core.schema import EnergyPoint, PlantData, TimeRange


def safe_step(pd: PlantData, label: str, fn):
    """Run ``fn()``; on any failure flag it on the plant and return None."""
    try:
        return fn()
    except Exception as e:
        pd.data_quality_flags.append(f"{label}: unavailable ({e})")
        return None


def month_keys_for(time_range: TimeRange, today: date) -> list[str]:
    """'YYYY-MM' keys whose daily charts cover the range, oldest first.

    LAST_30D needs the previous and current month (a 30-day window spans at
    most two calendar months). LAST_12MO returns 13 keys so the window covers
    12 full months plus the current partial one. Other ranges use the
    year/total charts instead.
    """
    if time_range == TimeRange.LAST_30D:
        back = 1
    elif time_range == TimeRange.LAST_12MO:
        back = 12
    else:
        return []
    y, m = today.year, today.month
    keys = []
    for _ in range(back + 1):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(keys))


def year_keys_for(time_range: TimeRange, today: date,
                  install_date: str | None) -> list[str]:
    """'YYYY' keys whose monthly charts cover the range, oldest first."""
    if time_range == TimeRange.LAST_12MO:
        return [str(today.year - 1), str(today.year)]
    if time_range == TimeRange.ALL:
        try:
            first = int(str(install_date)[:4])
        except (TypeError, ValueError):
            first = today.year
        first = max(min(first, today.year), today.year - 30)  # sanity clamp
        return [str(y) for y in range(first, today.year + 1)]
    return []


def clip_series(points: list[EnergyPoint], start_iso: str,
                end_iso: str | None = None) -> list[EnergyPoint]:
    """Keep points in [start_iso, end_iso] (ISO prefixes compare lexically).

    The end bound matters because portal charts pad the rest of the current
    month/year with 0.0 for days that have not happened yet — indistinguishable
    from a real zero-production day without a date cut-off.
    """
    return [p for p in points
            if p.timestamp_local >= start_iso
            and (end_iso is None or p.timestamp_local <= end_iso)]
