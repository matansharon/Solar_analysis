"""Pure per-optimizer anomaly detection over the accumulated daily series.

No IO/clock: takes loaded inventory + energy rows and an explicit as_of_day.
SolarEdge's `color` (0..1) is its own peer-normalized performance value, so it
is the primary signal; string-median energy and multi-day persistence back it
up (a one-day dip is weather, a multi-day low is a fault)."""
from __future__ import annotations
import statistics
from dataclasses import dataclass

DEAD_COLOR = 0.05        # color at/below this ~ not producing
DEAD_DAYS = 2            # consecutive most-recent dead days -> dead
UNDER_COLOR = 0.60       # color below this counts as an underperforming day
UNDER_MIN_DAYS = 3       # this many bad days within the window -> underperforming
UNDER_WINDOW = 5         # "last N days" window
STRING_RATIO = 0.70      # energy below this fraction of string-median = bad day
WATCH_COLOR = 0.75       # latest-day color below this (not persistent) -> watch
DEGRADE_MIN_HISTORY = 14  # need at least this many days to judge a trend
DEGRADE_RECENT = 7        # compare last 7 days vs the prior 7
DEGRADE_DROP = 0.15       # optimizer's recent/prior ratio this far below its string's

_SEVERITY_RANK = {"dead": 0, "underperforming": 1, "degrading": 2, "watch": 3}


@dataclass
class OptimizerAnomaly:
    site_id: int
    optimizer_serial: str
    label: str | None
    string_label: str | None
    severity: str
    reason: str
    latest_day: str | None
    latest_energy_wh: float | None
    latest_color: float | None
    string_median_wh: float | None
    ratio_to_string: float | None


def _series_by_serial(energy_rows):
    out: dict[str, dict[str, dict]] = {}
    for r in energy_rows:
        out.setdefault(r["optimizer_serial"], {})[r["day"]] = r
    return out


def _string_of(inventory):
    return {i["optimizer_serial"]: i.get("string_label") for i in inventory}


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _window_ratio(byday, days_recent, days_prior):
    r = _mean([byday.get(d, {}).get("energy_wh") for d in days_recent])
    p = _mean([byday.get(d, {}).get("energy_wh") for d in days_prior])
    return (r / p) if (r is not None and p) else None


def _string_median_by_day(inventory, series):
    """{day: {string_label: median energy_wh over that string's optimizers}}."""
    string_of = _string_of(inventory)
    per: dict[str, dict[str, list]] = {}
    for serial, byday in series.items():
        sl = string_of.get(serial)
        for day, row in byday.items():
            e = row.get("energy_wh")
            if e is not None:
                per.setdefault(day, {}).setdefault(sl, []).append(e)
    return {day: {sl: statistics.median(v) for sl, v in slmap.items() if v}
            for day, slmap in per.items()}


def analyze_site(site_id, inventory, energy_rows, as_of_day) -> list[OptimizerAnomaly]:
    series = _series_by_serial(energy_rows)
    string_med = _string_median_by_day(inventory, series)
    all_days = sorted({r["day"] for r in energy_rows if r["day"] <= as_of_day})
    recent = all_days[-UNDER_WINDOW:]
    out: list[OptimizerAnomaly] = []

    for inv in inventory:
        serial = inv["optimizer_serial"]
        byday = series.get(serial, {})
        if not byday:
            continue
        sl = inv.get("string_label")
        latest_day = max((d for d in byday if d <= as_of_day), default=None)
        latest = byday.get(latest_day, {}) if latest_day else {}
        latest_e = latest.get("energy_wh")
        latest_c = latest.get("color")
        med = string_med.get(latest_day, {}).get(sl) if latest_day else None
        ratio = (latest_e / med) if (latest_e is not None and med) else None

        def anomaly(sev, reason):
            return OptimizerAnomaly(site_id, serial, inv.get("label"), sl, sev,
                                    reason, latest_day, latest_e, latest_c, med, ratio)

        # Dead: the most-recent DEAD_DAYS days are all ~zero (color or energy).
        last_days = all_days[-DEAD_DAYS:]
        if len(last_days) == DEAD_DAYS and all(
                (byday.get(d, {}).get("color") is not None
                 and byday[d]["color"] <= DEAD_COLOR)
                or (byday.get(d, {}).get("energy_wh") == 0.0)
                for d in last_days):
            out.append(anomaly("dead", f"~zero output for {DEAD_DAYS} days"))
            continue

        # Underperforming: color low on >=UNDER_MIN_DAYS of recent, OR energy
        # below STRING_RATIO x string-median on >=UNDER_MIN_DAYS of recent.
        color_bad = sum(1 for d in recent
                        if (byday.get(d, {}).get("color") is not None
                            and byday[d]["color"] < UNDER_COLOR))
        ratio_bad = 0
        for d in recent:
            e = byday.get(d, {}).get("energy_wh")
            m = string_med.get(d, {}).get(sl)
            if e is not None and m and e < STRING_RATIO * m:
                ratio_bad += 1
        if color_bad >= UNDER_MIN_DAYS:
            out.append(anomaly("underperforming",
                               f"color<{UNDER_COLOR} on {color_bad}/{len(recent)} days"))
            continue
        if ratio_bad >= UNDER_MIN_DAYS:
            out.append(anomaly("underperforming",
                               f"below {int(STRING_RATIO*100)}% of string median on "
                               f"{ratio_bad}/{len(recent)} days"))
            continue

        # Degrading: declining faster than its string peers over two 7-day
        # windows (needs enough history). Checked before watch.
        if len(all_days) >= DEGRADE_MIN_HISTORY:
            recent7 = all_days[-DEGRADE_RECENT:]
            prior7 = all_days[-2 * DEGRADE_RECENT:-DEGRADE_RECENT]
            opt_ratio = _window_ratio(byday, recent7, prior7)
            # string baseline: median of peer optimizers' own window-ratios
            peers = [s for s in series
                     if s != serial and _string_of(inventory).get(s) == sl]
            peer_ratios = [pr for pr in
                           (_window_ratio(series[s], recent7, prior7) for s in peers)
                           if pr is not None]
            base = statistics.median(peer_ratios) if peer_ratios else None
            if (opt_ratio is not None and base
                    and opt_ratio < base * (1 - DEGRADE_DROP)):
                out.append(anomaly("degrading",
                    f"7-day output ratio {opt_ratio:.2f} vs string {base:.2f}"))
                continue

        # Watch: latest-day color dip, not persistent.
        if latest_c is not None and latest_c < WATCH_COLOR:
            out.append(anomaly("watch", f"latest-day color {latest_c:.2f}"))

    out.sort(key=lambda a: (_SEVERITY_RANK.get(a.severity, 9),
                            a.ratio_to_string if a.ratio_to_string is not None else 1.0,
                            a.latest_color if a.latest_color is not None else 1.0))
    return out
