"""Pure per-string anomaly detection over the accumulated daily series.

No IO, no clock: takes loaded channels + rows + an explicit `as_of_day`,
matching `optimizers/analyze.py`.

The governing insight, and the reason this analyzer looks nothing like the
optimizer one: **these channels are not peers.** PV1-3 carry ~0.195 of plant DC
energy each against PV4-6's ~0.135, because PV4-6 are physically smaller arrays,
and the twelve individual strings sit at permanently different pair imbalances
(14.2% on strings 5+6 against 24.3% on strings 3+4). Every peer-comparison rule
therefore fires on a perfectly healthy plant. Instead **every rule here compares
a channel against its own trailing history**, which cancels weather exactly and
needs no assumption that any two channels should match.

Every threshold below was measured, not guessed -- see
`docs/superpowers/plans/2026-08-02-growatt-string-c2-calibration.md`, which
replays each rule against the live 18-day series and reports how many false
positives it would have produced on demonstrably healthy data. All of them
produce zero, with the headroom noted per constant.
"""
from __future__ import annotations
import statistics
from collections import defaultdict
from dataclasses import dataclass

# A day the collector only partly captured, or a genuinely short day, must not
# reach the multi-day rules. Measured separation is unambiguous: the partial
# commissioning day reads 385-430 producing minutes, every full day 760-835.
# Paging is newest-first, so a run that loses pages 1-3 keeps only the day's
# last 80 samples and necessarily lands far below this line -- which is what
# lets C2 ship without the deferred `pages_ok` column.
SHORT_DAY_MINUTES = 600

MIN_HISTORY_DAYS = 7      # rules 4/5/6/8: days of own history before judging

DEAD_DAYS = 2             # rule 2: consecutive silent/zero days -> dead

SHARE_DROP_REL = 0.05     # rule 4: relative drop below own trailing median
                          # (worst healthy -0.96%, 5.2x headroom)
SHARE_WATCH_REL = 0.02    # rule 5: milder drop that must persist
SHARE_WATCH_MIN_DAYS = 3  #         on this many of...
SHARE_WATCH_WINDOW = 5    #         ...the last this many days
                          # 2.1x headroom on a single day, but no healthy day
                          # comes within half of it, and three of five must
                          # breach together -- so this is safely tighter than
                          # rule 4 rather than a near-duplicate of it.

PAIR_POWER_FRAC = 0.50    # rule 6: only samples at >=50% of the day's peak AC
PAIR_DEV_PP = 4.0         # rule 6: percentage-point move off own trailing
                          # median (worst healthy 1.45pp over 22 days, 2.8x).
                          # Widened from 3.0 after five more days pushed the
                          # worst healthy deviation 1.02 -> 1.45pp; the max of
                          # a sample keeps growing, and a real fault moves this
                          # metric ~20pp, so headroom costs nothing here.
                          # NOT an absolute threshold: >10% absolute flags
                          # 100% of healthy pair-days, >20% flags 17%.

DEGRADE_MIN_HISTORY = 14  # rule 7
DEGRADE_RECENT = 7
DEGRADE_DROP = 0.03       # recent-7 vs prior-7 mean share
                          # (worst healthy -0.46% over 22 days, 6.5x; the
                          # 18-day figure was -0.29%/10.2x). Weakest-calibrated
                          # rule of the eight: it needs 14 days, so its number
                          # still comes from a handful of overlapping windows.
                          # Rule 7 is also SHADOWED by rules 4/5, which return
                          # before it: rule 5 (2% on 3 of 5 days) is strictly
                          # more sensitive than 3% between two 7-day means, so
                          # a decline still present today always trips 4 or 5
                          # first. What reaches rule 7 is the one shape they
                          # cannot see -- a dip in the older half of the
                          # recent-7 window that has since recovered. Both
                          # behaviours are pinned in test_analyze.py.

INTRADAY_DROP_REL = 0.15  # rule 8: hourly share below own trailing median
                          # (worst healthy -4.96%, 3.0x headroom)
INTRADAY_HOURS = tuple(range(7, 17))
INTRADAY_MIN_HOURS = 2    # contiguous hours needed -- one hour is a cloud

TEMP_RISE_C = 8.0         # rule 9: daily max above own trailing median
                          # (worst healthy +3.10 C, 2.6x headroom)
# temp4_c is deliberately absent: constant 0.0 across every stored sample, the
# sensor is not fitted, and any outlier rule would call it cold forever.
TEMP_COLUMNS = ("temp_c", "temp2_c", "temp3_c", "temp5_c")

_FLAG_COLUMNS = (
    ("n_str_break", "string break"),
    ("n_str_unbalance", "string imbalance"),
    ("n_str_unmatch", "string mismatch"),
    ("n_fault_code1", "fault code 1"),
    ("n_fault_code2", "fault code 2"),
    ("n_fault_type", "fault type"),
    ("n_warn_code", "warning code"),
    ("n_derating_mode", "derating"),
)

_SEVERITY_RANK = {"dead": 0, "fault": 1, "underperforming": 2,
                  "imbalance": 3, "degrading": 4, "watch": 5}


@dataclass
class StringAnomaly:
    device_sn: str
    scope: str                    # "mppt" | "pair" | "inverter"
    label: str                    # "PV3" | "strings 9+10" | "inverter"
    severity: str
    reason: str
    as_of_day: str
    metric: float | None = None       # the value the rule fired on
    baseline: float | None = None     # its own-history baseline
    energy_kwh: float | None = None
    share_of_total: float | None = None


# --- pure transforms over the loaded sample aggregates -------------------

def pair_imbalance_by_day(string_samples) -> dict[str, dict[tuple[int, int], float]]:
    """{day: {(string_a, string_b): median imbalance %}}.

    Parallel strings on one MPPT input are electrically tied, so they
    necessarily read the same voltage -- that is what recovers the pairs, and
    it is read from `channel_samples.voltage_v` (immutable history) rather than
    `inverter_channels.group_voltage` (a last-write-wins snapshot). Across the
    18-day series this yields one stable partition on every single sample.

    A voltage group that does not hold exactly two strings is skipped for that
    instant rather than guessed at: two MPPTs momentarily rounding to the same
    0.1 V would otherwise be mis-paired. Taking the median across the day's
    high-power samples absorbs the occasional skipped instant."""
    per_instant: dict[tuple[str, str], dict[float, list]] = defaultdict(
        lambda: defaultdict(list))
    for r in string_samples:
        v, i = r.get("voltage_v"), r.get("current_a")
        if v is None or i is None:
            continue
        per_instant[(r["day"], r["sampled_at"])][round(v, 1)].append(
            (r["channel_no"], i))

    acc: dict[str, dict[tuple[int, int], list]] = defaultdict(
        lambda: defaultdict(list))
    for (day, _ts), by_voltage in per_instant.items():
        for members in by_voltage.values():
            if len(members) != 2:
                continue
            members.sort()
            (n1, i1), (n2, i2) = members
            mean = (i1 + i2) / 2
            if mean > 0:
                acc[day][(n1, n2)].append(abs(i1 - i2) / mean * 100.0)
    return {day: {p: statistics.median(v) for p, v in pairs.items() if v}
            for day, pairs in acc.items()}


def hourly_share_by_day(hourly_rows) -> dict[int, dict[int, dict[str, float]]]:
    """{channel_no: {hour: {day: share of that hour's plant DC power}}}.

    Share within the hour, not raw power, so cloud cover cancels the same way
    it does for the daily rule."""
    total: dict[tuple[str, int], float] = defaultdict(float)
    for r in hourly_rows:
        total[(r["day"], r["hour"])] += r.get("power_w") or 0.0
    out: dict[int, dict[int, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict))
    for r in hourly_rows:
        t = total[(r["day"], r["hour"])]
        if t > 0:
            out[r["channel_no"]][r["hour"]][r["day"]] = (r.get("power_w") or 0.0) / t
    return out


def usable_days(energy_rows, as_of_day: str) -> list[str]:
    """Days on/before `as_of_day` that were captured completely enough to
    compare. See SHORT_DAY_MINUTES."""
    longest: dict[str, int] = {}
    for r in energy_rows:
        d = r["day"]
        if d <= as_of_day:
            pm = r.get("producing_minutes") or 0
            longest[d] = max(longest.get(d, 0), pm)
    return sorted(d for d, pm in longest.items() if pm >= SHORT_DAY_MINUTES)


def _trailing_median(series: dict, days: list[str], upto: int,
                     min_history: int) -> float | None:
    """Median of `series` over `days[:upto]` -- strictly before the day being
    judged -- or None if there is not enough history yet. Rules skip, never
    fire, below their minimum."""
    hist = [series[d] for d in days[:upto] if d in series]
    return statistics.median(hist) if len(hist) >= min_history else None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _longest_run(hours) -> tuple[int, int, int]:
    """(length, first hour, last hour) of the longest consecutive run.

    Reported rather than just counted so the finding names the window that
    actually collapsed: hours 7, 8 and 15 are a 2-hour morning event, not a
    9-hour one."""
    best = (0, 0, 0)
    start = prev = None
    for h in sorted(hours):
        if prev is None or h != prev + 1:
            start = h
        prev = h
        best = max(best, (h - start + 1, start, h))
    return best


# --- the analyzer ---------------------------------------------------------

def analyze_device(device_sn, channels, energy_rows, as_of_day, *,
                   string_samples=(), hourly_rows=(), day_health=()) -> list[StringAnomaly]:
    """Every anomaly for one inverter as of `as_of_day`, worst first."""
    out: list[StringAnomaly] = []
    health = {h["day"]: h for h in day_health}

    # --- rule 3: the inverter's own diagnosis. No history needed, so it runs
    # even on a day too short for everything else -- and it is the only rule
    # here that does not depend on a threshold.
    today_health = health.get(as_of_day)
    if today_health:
        for col, human in _FLAG_COLUMNS:
            n = today_health.get(col) or 0
            if n:
                out.append(StringAnomaly(
                    device_sn, "inverter", "inverter", "fault",
                    f"inverter reported {human} on {n} sample(s)", as_of_day,
                    metric=float(n)))

    days = usable_days(energy_rows, as_of_day)
    if as_of_day not in days:
        # Short or truncated: energy/share stay correct (page 0 carries the
        # counters) but peak/producing_minutes and the intraday series are
        # evening-only. Say so rather than emailing a false all-clear.
        minutes = max((r.get("producing_minutes") or 0)
                      for r in energy_rows if r["day"] == as_of_day) \
            if any(r["day"] == as_of_day for r in energy_rows) else 0
        out.append(StringAnomaly(
            device_sn, "inverter", "inverter", "watch",
            f"day incomplete ({minutes} producing minutes, under "
            f"{SHORT_DAY_MINUTES}) — history-based rules skipped", as_of_day,
            metric=float(minutes)))
        return sorted(out, key=_sort_key)

    idx = days.index(as_of_day)
    recent = days[max(0, idx + 1 - SHARE_WATCH_WINDOW): idx + 1]

    energy = defaultdict(dict)      # channel_no -> day -> row
    for r in energy_rows:
        if r["day"] <= as_of_day:
            energy[r["channel_no"]][r["day"]] = r

    # Rule 1 is an exclusion, not a finding: an input that has never produced
    # (PV7-16 here, including PV7's stray 227 V open circuit) is not dead
    # hardware, it is hardware that was never connected.
    live = [c for c in channels
            if c.get("channel_kind") == "mppt" and (c.get("lifetime_kwh") or 0) > 0]
    shares = hourly_share_by_day(hourly_rows)

    for ch in live:
        no = ch["channel_no"]
        label = f"PV{no}"
        by_day = energy.get(no, {})
        today = by_day.get(as_of_day, {})
        kwh = today.get("energy_kwh")
        share = today.get("share_of_total")

        def anomaly(sev, reason, metric=None, baseline=None):
            return StringAnomaly(device_sn, "mppt", label, sev, reason,
                                 as_of_day, metric, baseline, kwh, share)

        # --- rule 2: stopped reporting. A missing row is not a zero row, so
        # the zero-output check below cannot see it.
        silent = 0
        for d in reversed(days):
            if d in by_day:
                break
            silent += 1
        if silent >= DEAD_DAYS:
            out.append(anomaly("dead", f"stopped reporting for {silent} days"))
            continue
        if not by_day:
            continue

        last = days[-DEAD_DAYS:]
        if len(last) == DEAD_DAYS and all(
                by_day.get(d, {}).get("energy_kwh") == 0.0 for d in last):
            out.append(anomaly("dead", f"zero output for {DEAD_DAYS} days"))
            continue

        share_series = {d: r["share_of_total"] for d, r in by_day.items()
                        if r.get("share_of_total") is not None}
        base = _trailing_median(share_series, days, idx, MIN_HISTORY_DAYS)

        # --- rule 4: share collapse against the channel's own history.
        if base and share is not None:
            drop = (base - share) / base
            if drop >= SHARE_DROP_REL:
                out.append(anomaly(
                    "underperforming",
                    f"share {share:.5f} is {drop*100:.1f}% below its own "
                    f"{MIN_HISTORY_DAYS}+-day median {base:.5f}",
                    share, base))
                continue

            # --- rule 5: milder, but persistent.
            bad = [d for d in recent
                   if d in share_series
                   and share_series[d] < base * (1 - SHARE_WATCH_REL)]
            if len(bad) >= SHARE_WATCH_MIN_DAYS:
                out.append(anomaly(
                    "underperforming",
                    f"share more than {SHARE_WATCH_REL*100:.1f}% below its own "
                    f"median on {len(bad)}/{len(recent)} days", share, base))
                continue

        # --- rule 7: declining against its own recent past.
        if len(days) >= DEGRADE_MIN_HISTORY:
            r7 = days[-DEGRADE_RECENT:]
            p7 = days[-2 * DEGRADE_RECENT:-DEGRADE_RECENT]
            a = _mean([share_series.get(d) for d in r7])
            b = _mean([share_series.get(d) for d in p7])
            if a is not None and b:
                ratio = a / b
                if ratio < 1 - DEGRADE_DROP:
                    out.append(anomaly(
                        "degrading",
                        f"{DEGRADE_RECENT}-day mean share {ratio*100-100:+.1f}% "
                        f"against the prior {DEGRADE_RECENT} days", ratio, 1.0))
                    continue

        # --- rule 8: intraday shape. A contiguous collapse while peers hold is
        # a shading/soiling signature, which the daily total averages away.
        drops = {}
        for hour in INTRADAY_HOURS:
            series = shares.get(no, {}).get(hour, {})
            hbase = _trailing_median(series, days, idx, MIN_HISTORY_DAYS)
            today_share = series.get(as_of_day)
            if not hbase or today_share is None:
                continue
            hdrop = (hbase - today_share) / hbase
            if hdrop >= INTRADAY_DROP_REL:
                drops[hour] = (hdrop, hbase)
        length, first, last = _longest_run(drops)
        if length >= INTRADAY_MIN_HOURS:
            run = range(first, last + 1)
            worst, worst_base = max(drops[h] for h in run)
            out.append(anomaly(
                "watch",
                f"intraday share down to {worst*100:.0f}% below its own median "
                f"across {length}h ({first:02d}:00-{last+1:02d}:00) — "
                f"shading or soiling", worst, worst_base))

    # --- rule 6: string-pair imbalance. The finest signal available, and the
    # one with no SolarEdge equivalent: it catches one bad string in a parallel
    # pair, which per-MPPT energy averages away.
    imbalance = pair_imbalance_by_day(string_samples)
    pairs = sorted({p for d in days for p in imbalance.get(d, {})})
    for pair in pairs:
        series = {d: imbalance[d][pair] for d in days
                  if pair in imbalance.get(d, {})}
        base = _trailing_median(series, days, idx, MIN_HISTORY_DAYS)
        today = series.get(as_of_day)
        if base is None or today is None:
            continue
        dev = today - base
        if abs(dev) > PAIR_DEV_PP:
            out.append(StringAnomaly(
                device_sn, "pair", f"strings {pair[0]}+{pair[1]}", "imbalance",
                f"current imbalance {today:.1f}% is {dev:+.1f}pp off this "
                f"pair's own median {base:.1f}%", as_of_day, today, base))

    # --- rule 9: temperature, each sensor against its own history. Comparing
    # the sensors to each other would be wrong by construction -- they sit at
    # different operating points (daily maxima cluster near 50 / 56 / 61 / 43 C).
    temp_series = {c: {d: h[c] for d, h in health.items()
                       if d <= as_of_day and h.get(c) is not None}
                   for c in TEMP_COLUMNS}
    for col in TEMP_COLUMNS:
        series = temp_series[col]
        base = _trailing_median(series, days, idx, MIN_HISTORY_DAYS)
        today = series.get(as_of_day)
        if base is None or today is None:
            continue
        if today - base > TEMP_RISE_C:
            out.append(StringAnomaly(
                device_sn, "inverter", "inverter", "watch",
                f"{col} peaked at {today:.1f}C, {today-base:+.1f}C above its "
                f"own median {base:.1f}C", as_of_day, today, base))

    return sorted(out, key=_sort_key)


def _sort_key(a: StringAnomaly):
    return (_SEVERITY_RANK.get(a.severity, 9), a.label)
