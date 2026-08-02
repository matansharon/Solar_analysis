"""Re-measure the Phase C2 string-anomaly thresholds against stored data.

Usage:
    python -m solaranalysis.tools.string_calibration [--db data/app.db]
        [--device SN] [--verify]

Read-only. Prints, for each rule, the worst deviation a **healthy** plant
produced and how many findings each candidate threshold would have raised —
then compares that against the constants `strings/analyze.py` actually ships.

**Why this exists as a tool and not a one-off script.** Every C2 threshold is
set from the observed spread of a plant with no known faults, and that spread
widens as history accumulates: between the 16-day and 18-day measurements PV4's
share envelope grew from 0.076pp to 0.106pp. The numbers in
`docs/superpowers/plans/2026-08-02-growatt-string-c2-calibration.md` are a
snapshot, not a constant. Re-run this once a few months of history exist and
widen any threshold whose headroom has eroded.

The method is the part that matters: each day is judged against the median of
the days **before** it, which is what the analyzer does in production. Static
full-window envelopes are a different, much looser number and must not be used
to set a trailing-median threshold.
"""
from __future__ import annotations
import argparse
import sqlite3
import statistics as st
from collections import defaultdict

from ..strings import analyze, store


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _device(conn, explicit=None) -> str:
    if explicit:
        return explicit
    row = conn.execute("SELECT device_sn, COUNT(*) n FROM channel_day_energy "
                       "GROUP BY device_sn ORDER BY n DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("no rows in channel_day_energy — nothing to calibrate")
    return row["device_sn"]


def _worst(devs):
    """(most negative, most positive) of a deviation list, or (0, 0)."""
    return (min(devs, default=0.0), max(devs, default=0.0))


_BREACH = {"drop": lambda d, t: d < -t,      # rules 4/5/7/8: one-sided, down
           "rise": lambda d, t: d > t,       # rule 9: one-sided, up
           "abs": lambda d, t: abs(d) > t}   # rule 6: two-sided


def _curve(devs, thresholds, mode="drop"):
    """How many deviations breach each threshold, counted the same way the rule
    itself counts. The mode must match the analyzer's comparison: rule 9 only
    fires on a rise, so counting its drops would overstate its false positives."""
    breach = _BREACH[mode]
    return {t: sum(1 for d in devs if breach(d, t)) for t in thresholds}


def calibrate(conn, sn: str) -> dict:
    energy = store.load_energy_window(conn, sn, "0000-00-00")
    days = analyze.usable_days(energy, "9999-99-99")
    if len(days) <= analyze.MIN_HISTORY_DAYS:
        raise SystemExit(f"only {len(days)} usable days — need more than "
                         f"{analyze.MIN_HISTORY_DAYS} to calibrate anything")
    since = days[0]
    out = {"days": days, "shipped": {}, "measured": {}}

    # --- rule 4/5: share vs own trailing median (relative)
    share = defaultdict(dict)
    for r in energy:
        if r.get("share_of_total") is not None:
            share[r["channel_no"]][r["day"]] = r["share_of_total"]
    devs = []
    for ch, series in share.items():
        for i, d in enumerate(days):
            base = analyze._trailing_median(series, days, i, analyze.MIN_HISTORY_DAYS)
            if base and d in series:
                devs.append((series[d] - base) / base)
    out["measured"]["share_rel"] = _worst(devs)
    out["measured"]["share_curve"] = _curve(devs, (0.02, 0.03, 0.05, 0.08, 0.10))
    out["shipped"]["share_rel"] = analyze.SHARE_DROP_REL

    # --- rule 7: rolling recent-N vs prior-N mean share
    ratios = []
    for ch, series in share.items():
        for i in range(2 * analyze.DEGRADE_RECENT, len(days) + 1):
            w = days[:i]
            a = analyze._mean([series.get(x) for x in w[-analyze.DEGRADE_RECENT:]])
            b = analyze._mean([series.get(x) for x in
                               w[-2 * analyze.DEGRADE_RECENT:-analyze.DEGRADE_RECENT]])
            if a is not None and b:
                ratios.append(a / b - 1.0)
    out["measured"]["degrade_rel"] = _worst(ratios)
    out["measured"]["degrade_curve"] = _curve(ratios, (0.02, 0.03, 0.05, 0.10))
    out["shipped"]["degrade_rel"] = analyze.DEGRADE_DROP

    # --- rule 6: pair imbalance vs own trailing median (percentage points)
    imb = analyze.pair_imbalance_by_day(store.load_peak_string_samples(
        conn, sn, since, analyze.PAIR_POWER_FRAC))
    pairs = sorted({p for d in days for p in imb.get(d, {})})
    pair_devs, absolute = [], []
    for p in pairs:
        series = {d: imb[d][p] for d in days if p in imb.get(d, {})}
        absolute += list(series.values())
        for i, d in enumerate(days):
            base = analyze._trailing_median(series, days, i, analyze.MIN_HISTORY_DAYS)
            if base and d in series:
                pair_devs.append(series[d] - base)
    out["measured"]["pair_pp"] = _worst(pair_devs)
    out["measured"]["pair_curve"] = _curve(pair_devs, (1.0, 1.5, 2.0, 3.0, 4.0),
                                           mode="abs")
    out["measured"]["pair_absolute"] = (
        (min(absolute), max(absolute)) if absolute else (0, 0))
    out["measured"]["pair_absolute_curve"] = {
        t: sum(1 for v in absolute if v > t) for t in (10, 15, 20, 25, 30)}
    out["measured"]["pair_n"] = len(absolute)
    out["shipped"]["pair_pp"] = analyze.PAIR_DEV_PP

    # --- rule 8: hourly share vs own trailing median (relative)
    hourly = analyze.hourly_share_by_day(
        store.load_hourly_channel_power(conn, sn, since))
    hdevs = []
    for ch, byhour in hourly.items():
        for hour in analyze.INTRADAY_HOURS:
            series = byhour.get(hour, {})
            for i, d in enumerate(days):
                base = analyze._trailing_median(series, days, i,
                                                analyze.MIN_HISTORY_DAYS)
                if base and d in series:
                    hdevs.append((series[d] - base) / base)
    out["measured"]["intraday_rel"] = _worst(hdevs)
    out["measured"]["intraday_curve"] = _curve(hdevs, (0.05, 0.10, 0.15, 0.20))
    out["shipped"]["intraday_rel"] = analyze.INTRADAY_DROP_REL

    # --- rule 9: daily max temperature vs own trailing median (degrees)
    health = {h["day"]: h for h in store.load_inverter_day_health(conn, sn, since)}
    tdevs = []
    for col in analyze.TEMP_COLUMNS:
        series = {d: h[col] for d, h in health.items() if h.get(col) is not None}
        for i, d in enumerate(days):
            base = analyze._trailing_median(series, days, i, analyze.MIN_HISTORY_DAYS)
            if base and d in series:
                tdevs.append(series[d] - base)
    out["measured"]["temp_c"] = _worst(tdevs)
    out["measured"]["temp_curve"] = _curve(tdevs, (4.0, 6.0, 8.0, 12.0),
                                           mode="rise")
    out["shipped"]["temp_c"] = analyze.TEMP_RISE_C
    return out


def _headroom(worst, shipped):
    return f"{abs(shipped / worst):.1f}x" if worst else "inf"


def report(res) -> str:
    days, m, s = res["days"], res["measured"], res["shipped"]
    lines = [f"usable days: {len(days)}  ({days[0]} .. {days[-1]})", ""]
    lines.append(f"{'rule':<24}{'worst healthy':>16}{'shipped':>12}{'headroom':>11}")
    rows = [
        ("4/5 share (rel)", m["share_rel"][0], s["share_rel"], "{:+.2%}"),
        ("7 degrading (rel)", m["degrade_rel"][0], s["degrade_rel"], "{:+.2%}"),
        ("6 pair imbalance (pp)", max(abs(v) for v in m["pair_pp"]),
         s["pair_pp"], "{:.2f}"),
        ("8 intraday (rel)", m["intraday_rel"][0], s["intraday_rel"], "{:+.2%}"),
        ("9 temperature (C)", m["temp_c"][1], s["temp_c"], "{:+.2f}"),
    ]
    for name, worst, shipped, fmt in rows:
        flag = "" if abs(shipped) > abs(worst) * 2 else "   <-- HEADROOM ERODED"
        lines.append(f"{name:<24}{fmt.format(worst):>16}{shipped:>12}"
                     f"{_headroom(worst, shipped):>11}{flag}")

    lines += ["", "false positives each candidate threshold would raise:"]
    for key, label in (("share_curve", "4/5 share drop"),
                       ("degrade_curve", "7 degrade drop"),
                       ("pair_curve", "6 pair |dev| pp"),
                       ("intraday_curve", "8 intraday drop"),
                       ("temp_curve", "9 temp rise C")):
        got = ", ".join(f"{t}:{n}" for t, n in m[key].items())
        lines.append(f"  {label:<20} {got}")

    lo, hi = m["pair_absolute"]
    lines += ["", f"rule 6 sanity check -- ABSOLUTE thresholds on {m['pair_n']} "
                  f"healthy pair-days (range {lo:.2f}-{hi:.2f}%):"]
    for t, n in m["pair_absolute_curve"].items():
        pct = 100 * n / m["pair_n"] if m["pair_n"] else 0
        lines.append(f"  > {t:>2}% absolute -> {n} flagged ({pct:.0f}% of a healthy plant)")
    lines.append("  (this is why rule 6 is a deviation-from-own-median rule, "
                 "not an absolute one)")
    return "\n".join(lines)


def verify(conn, sn: str) -> str:
    """Replay the shipped analyzer over every stored day. A healthy plant must
    produce nothing but incomplete-day notices."""
    energy = store.load_energy_window(conn, sn, "0000-00-00")
    all_days = sorted({r["day"] for r in energy})
    since = all_days[0]
    loaded = dict(
        channels=store.load_channels(conn, sn),
        energy_rows=energy,
        string_samples=store.load_peak_string_samples(
            conn, sn, since, analyze.PAIR_POWER_FRAC),
        hourly_rows=store.load_hourly_channel_power(conn, sn, since),
        day_health=store.load_inverter_day_health(conn, sn, since))
    lines, total = [], 0
    for d in all_days:
        found = analyze.analyze_device(
            sn, loaded["channels"], loaded["energy_rows"], d,
            string_samples=loaded["string_samples"],
            hourly_rows=loaded["hourly_rows"], day_health=loaded["day_health"])
        total += len(found)
        lines.append(f"  {d}: {len(found)} finding(s)")
        for a in found:
            lines.append(f"      [{a.severity}] {a.label}: {a.reason}")
    lines.append(f"\n  {total} finding(s) across {len(all_days)} stored days")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solaranalysis.tools.string_calibration")
    ap.add_argument("--db", default="data/app.db")
    ap.add_argument("--device")
    ap.add_argument("--verify", action="store_true",
                    help="also replay the shipped analyzer over every day")
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    sn = _device(conn, args.device)
    print(f"device {sn}  ({args.db})\n")
    print(report(calibrate(conn, sn)))
    if args.verify:
        print("\nreplay of the shipped analyzer over stored days:")
        print(verify(conn, sn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
