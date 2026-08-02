# Growatt String Analysis — C2 threshold calibration

**Purpose:** every threshold constant Phase C2's `strings/analyze.py` uses is set
from a number in this document. Each was measured on 2026-08-02 against the live
`data/app.db` as it actually stands — **18 days, 2026-07-10 → 2026-07-27**, 18
channels, 90,810 `channel_samples`, 5,045 `inverter_samples` — not carried over
from `2026-07-26-growatt-string-baseline.md`, whose figures were computed on 16
days. The C1 whole-branch review explicitly required this recomputation, and it
was right to: **PV4's share envelope widened from 0.076pp to 0.106pp** once the
two extra days landed, exactly as the smoke test predicted.

Scripts: `calib.py` / `calib2.py` / `calib3.py` (read-only, `mode=ro`). The
method that matters is in pass 2 — pass 1 measured *static full-window*
envelopes, which is **not what the rule does in production**. The rule compares
each day against the median of the days *before* it, so every threshold below is
set from a **trailing-window simulation** that replays exactly that, and reports
how many `(channel, day)` pairs would have fired on demonstrably healthy data.

## The calibrated constants

Every threshold below produces **zero false positives** on the 17 usable days.
Rules 6 and 7 carry their **22-day** re-measurement — the two figures that moved
when five more days arrived, see "Thresholds erode as history grows" below. The
rest are unchanged between the two runs.

| Rule | Metric | Worst healthy value | Threshold | Headroom |
|---|---|---|---|---|
| 4 — share collapse | `share_of_total` vs own trailing median, relative | **−0.96%** | **−5%** | 5.2× |
| 5 — persistent underperformance | same, on ≥3 of last 5 days | −0.96% | **−2%**, 3/5 | 2.1× + persistence |
| 6 — string-pair imbalance | windowed imbalance vs the **pair's own** trailing median | **1.45pp** | **4.0pp** | 2.8× |
| 7 — degrading | recent-7 ÷ prior-7 mean share | **−0.46%** | **−3%** | 6.5× |
| 8 — intraday shape | hourly share vs own trailing median, relative | **−4.96%** | **−15%** | 3.0× |
| 9 — temperature | daily max per column vs own trailing median | **+3.10°C** | **+8°C** | 2.6× |
| 9 — `pv_iso_kohm` | — | — | **rule dropped** | see below |

Supporting constants: `MIN_HISTORY_DAYS = 7` (rules 4/5/6/8), `DEGRADE_MIN_HISTORY
= 14` (rule 7), `PAIR_POWER_FRAC = 0.50`, `SHORT_DAY_MINUTES = 600`,
`INTRADAY_MIN_HOURS = 2`, `DEAD_DAYS = 2`.

**Rule 5 was tightened from an initial −2.5% during validation.** At −2.5% on
3-of-5 days it was close to a duplicate of rule 4 rather than a more sensitive
companion to it — a −3% sustained loss failed to fire because one of the three
days landed 0.0003 above the cut line. The binding fact is that **no healthy day
anywhere in the series comes within half of even a −2% line** (worst is −0.96%),
and three of five days must breach together, so −2% is comfortably safe while
actually catching the sustained mild losses rule 4 is too blunt for.

## Validation against the live database

The finished analyzer was run over `data/app.db` itself, two ways
(`validate.py`):

1. **Every one of the 18 real days**, re-deriving each day's baseline from only
   the days before it. Result: **zero findings on all 17 usable days**, plus the
   expected incomplete-day notice on 2026-07-10. The calibration holds against
   the code that implements it, not just against the spreadsheet it came from.
2. **Eight perturbed copies of that same real data**, one per rule — each rule
   fired, and only that rule:

| Perturbation of the real 07-27 data | Fired |
|---|---|
| PV3 share ×0.88 | `underperforming` PV3 — 12.2% below its own median |
| PV5 rows deleted, last 2 days | `dead` PV5 — stopped reporting for 2 days |
| PV2 energy → 0, last 2 days | `dead` PV2 — zero output for 2 days |
| `str_break='1'` on 3 samples | `fault` — inverter reported string break |
| String 9 current ×0.85 | `imbalance` strings 9+10 — 34.1%, +16.0pp off its median |
| PV1 share ×0.97, last 3 days | `underperforming` PV1 — below on 3/5 days |
| PV4 power ×0.5, 08:00–11:00 only | `watch` PV4 — 46% down across 3h, shading/soiling |
| `temp3_c` +12 °C | `watch` — 72.5 °C, +11.7 °C above its own median |

The rule-8 case is the one worth noting: halving PV4's power for three morning
hours moves its *daily* total too little for rules 4 or 5 to see, and the
intraday rule catches it with the correct window named.

3. **Live end-to-end, same day.** A live collection filled the 07-28 → 08-01 gap
   (5 days, `4/4 pages` each, exit 0), the analyzer reported all-clear on
   08-01, and the report emailed successfully. The Claude narrative path was
   then exercised against a perturbed copy: it produced correctly grounded
   prose, ranked the pair imbalance above the share drop, and restated the
   own-history caveat the grounded block instructs it to respect.

## Thresholds erode as history grows — measured, not predicted

Five more days of real data arrived within hours of the first calibration, and
**two thresholds lost meaningful headroom immediately**:

| Rule | 17 usable days | 22 usable days |
|---|---|---|
| 6 — pair imbalance | worst 1.02pp (2.9× at 3.0pp) | worst **1.45pp** (2.1× at 3.0pp) |
| 7 — degrading | worst −0.29% (10.2×) | worst **−0.46%** (6.5×) |
| 4/5, 8, 9 | unchanged | unchanged |

Rule 6 was therefore **widened from 3.0pp to 4.0pp**, restoring 2.8× headroom.
The sensitivity cost is nil — a real fault moves this metric by roughly 20pp,
so 4.0pp still catches something five times smaller than a single-string loss.

This is the concrete case for `solaranalysis.tools.string_calibration`: the max
of a sample keeps growing with sample size, so **any threshold set from "the
worst thing a healthy plant has done so far" is a moving target**. Re-run the
tool periodically; it flags any rule whose headroom has fallen below 2×.

## Rule 6 — the flagship rule, and why absolute thresholds are dead

The C1 review flagged that spec §6 rule 6 had **no calibration data at all**.
It does now, and the result is unambiguous.

Pairs are recovered by grouping the 12 strings on `channel_samples.voltage_v`
(rounded to 0.1 V) at each sampled instant — the immutable history, never
`inverter_channels.group_voltage`, which is a last-write-wins snapshot. That
grouping is **perfectly stable**: across all 18 days it yields exactly one
partition, `(1,2) (3,4) (5,6) (7,8) (9,10) (11,12)` — one pair per MPPT input.
`parent_channel_no` is NULL throughout and is not needed.

**Absolute thresholds fire on a healthy plant** (102 pair-days):

| Absolute threshold | Healthy pair-days flagged |
|---|---|
| > 10% | **102 / 102 (100%)** |
| > 15% | 85 / 102 (83%) |
| > 20% | 17 / 102 (17%) |
| > 25% | 0 / 102 (0%) |
| > 30% | 0 / 102 (0%) |

The only absolute threshold with no false positives is >25% — and pair (3,4)
sits permanently at **24.09–24.54%**, so a 25% rule has **0.5pp of margin** and
would start firing daily on the first mild drift. There is no usable absolute
threshold. This is the same conclusion the review reached from 16 days, now
confirmed on 18 with the false-positive counts attached.

**Against each pair's own trailing median it works cleanly.** Per-pair medians
differ enormously (14.19% for pair (5,6) up to 24.32% for pair (3,4)) but each
pair is *stable about its own value*, spread 0.45–1.93pp over 17 days:

| Pair | n | median | min | max | max−min |
|---|---|---|---|---|---|
| (1,2) | 17 | 16.54 | 15.87 | 16.90 | 1.02 |
| (3,4) | 17 | 24.32 | 24.09 | 24.54 | 0.45 |
| (5,6) | 17 | 14.19 | 14.01 | 14.57 | 0.56 |
| (7,8) | 17 | 17.82 | 17.14 | 18.18 | 1.04 |
| (9,10) | 17 | 18.18 | 17.65 | 19.20 | 1.56 |
| (11,12) | 17 | 18.75 | 17.54 | 19.47 | **1.93 ← widest** |

Trailing-median simulation, largest absolute deviation: **1.02pp** at
`MIN_HIST=5` and `=7`, falling to 0.86pp at `=10`. A **3.0pp** threshold gives
~3× headroom and zero false positives. For scale, a real fault is far larger:
one string of a parallel pair losing 20% of its current moves the pair's
imbalance by roughly **20pp**, so 3.0pp is about seven times more sensitive than
the smallest fault worth an email.

**Use the windowed metric, not the single peak sample.** Measuring imbalance at
one peak-power instant is materially noisier than taking the median across every
sample at ≥50% of the day's peak AC power — worst per-pair spread **5.54pp →
1.93pp**, per-pair stdev 0.63–1.20 → 0.11–0.48. The cutoff itself is not
delicate: ≥30% gives 2.29pp, ≥50% gives 1.93pp, ≥70% gives 1.86pp. **50% is the
knee** — 70% buys 0.07pp for a materially smaller sample count.

## Rule 9 — `pv_iso_kohm` is not threshold-able, and the reason is not noise

The C1 review suggested time-of-day bucketing or a percentile rule might rescue
this column. Measurement says no, for a reason neither would fix: **the portal
reports one insulation value per day.** Among `status='1'` samples, every single
day has exactly **one distinct `pv_iso_kohm` value**, repeated across all ~175 of
that day's rows; the whole 18-day, 5,045-sample series holds just **66 distinct
values**. That is why the hourly percentile table came out byte-identical for
every hour of the day — there is no intraday variation to bucket.

That single daily value swings without trend across nearly two orders of
magnitude on consecutive healthy days:

```
07-11  148    07-12  151    07-13  144    07-14  189    07-15 5673
07-16  324    07-17  131    07-18 1100    07-19 7831    07-20 6354
07-21 4242    07-22  139    07-23 1342    07-24  161    07-25  656
```

A downward-trend rule is meaningless on that series, and a floor alarm is
impossible too: healthy days read as low as **131 kΩ**, so any floor low enough
to avoid false positives sits below what this inverter reports while perfectly
fine. **C2 ships no `pv_iso_kohm` rule.** The inverter's own `StrBreak` /
`StrUnblance` / `StrUnmatch` flags (rule 3) already cover the insulation/string
fault ground authoritatively.

One useful by-product: **`status='1'` excludes the 65,530 sentinel perfectly** —
0 of 3,032 `status='1'` samples carry it, against 445 of 2,013 `status='0'`
samples. `status` is the correct daylight filter, cleaner than both `pac > 0`
and the `< 60000` value hack.

## Rule 9 — temperature, reshaped

The spec's "temperature outlier **across** `temp_c..temp5_c`" is wrong by
construction: these are five different sensors with five different operating
ranges (daily maxima cluster at 50.0 / 56.3 / 60.8 / — / 42.7 °C), so comparing
them to each other flags the coolest sensor forever. Each column is instead
compared to **its own** trailing median. Measured trailing deviations of the
daily max:

| Column | Daily-max range | Trailing deviation |
|---|---|---|
| `temp_c` | 50.0 – 50.1 °C | +0.00 … **+0.10** °C (clamped — effectively constant) |
| `temp2_c` | 55.7 – 59.3 °C | −0.60 … **+3.10** °C ← widest |
| `temp3_c` | 60.5 – 62.1 °C | −0.30 … +1.30 °C |
| `temp5_c` | 42.2 – 43.6 °C | −0.35 … +1.00 °C |

`temp4_c` is **excluded**: constant 0.0 across all 5,045 samples, 1 distinct
value — the sensor is absent on this hardware. Including it in any cross-column
rule would flag it as a permanent cold outlier, exactly as the review warned.

## Rules 4, 5, 7, 8

**Rule 4 / 5 — share of total.** Full-window envelopes on 18 days (the
directly-comparable update to the baseline doc's 16-day table):

| Input | median | min | max | max−min (pp) | max abs dev from median (pp) |
|---|---|---|---|---|---|
| PV1 | 0.19476 | 0.19381 | 0.19717 | 0.336 | 0.242 |
| PV2 | 0.20680 | 0.20617 | 0.20734 | 0.117 | 0.063 |
| PV3 | 0.18973 | 0.18924 | 0.19030 | 0.107 | 0.057 |
| PV4 | 0.12753 | 0.12716 | **0.12821** | **0.106** (was 0.076) | 0.068 |
| PV5 | 0.14046 | 0.13937 | 0.14117 | 0.180 | 0.109 |
| PV6 | 0.14085 | 0.13822 | 0.14186 | **0.364 ← widest** | 0.263 |

But the trailing-window simulation is what sets the threshold, and it is far
tighter than these static figures suggest: across 60 comparisons at
`MIN_HIST=7`, the **worst drop below a channel's own trailing median is −0.96%
relative** (PV6, 2026-07-24). A **−5% relative** threshold has 5.2× headroom.
Relative, not percentage-point, because the channels are not peers — PV4's
median share is 0.1275 against PV2's 0.2068, so one absolute pp band means very
different things per channel. For scale: one string of a parallel pair going
open-circuit drops that MPPT's share by roughly **45%**, so −5% is deliberately
far more sensitive than a dropout, sized to catch partial soiling instead.

**Rule 7 — degrading.** Rolling 7-vs-7 over every available window (n=24): ratio
spans **0.9971 – 1.0071**, worst decline **−0.29%** at 17 days, **−0.46%** at 22.
A **−3%** threshold has 6.5× headroom on the later figure. Requires ≥14 usable
days, which is only just satisfied (17), so this rule is the one most likely to
need revisiting as history accumulates.

**Rule 7 is shadowed by rule 5, and this was measured after the fact rather than
designed in.** Rules 4 and 5 return before rule 7 is reached, and rule 5 (−2% on
3 of the last 5 days) is strictly more sensitive than rule 7 (−3% between two
7-day means). Sweeping sustained declines from −2% to −10% over the recent seven
days produces `underperforming` at every level that fires at all, and
`degrading` at none of them: **rule 7 adds no detection capability over rule 5
for a decline that is still present today.** What it does catch, uniquely, is a
dip in the older half of the recent-7 window that has since recovered — today's
share reads normal, so rules 4/5 stay silent, while the 7-day mean does not.
That is a narrow but real signal (an intermittent connection, a partial shading
event that cleared), so the rule is kept rather than dropped. Both behaviours
are pinned in `tests/strings/test_analyze.py` so the limitation cannot quietly
turn into an assumption that rule 7 is catching gradual degradation.

**Rule 8 — intraday shape.** Per channel, per hour, share of plant DC power
within that hour, compared to the same channel-hour's own trailing median.
Across 600 comparisons (`MIN_HIST=7`, hours 07–17), the worst drop is **−4.96%**
(PV6, 12h, 2026-07-26). A **−15% relative** threshold has 3× headroom. Widening
the window from 09–16 to 07–17 does not change the worst case, so the wider
window is used — it costs nothing and covers more of the day where shading
actually appears.

## The short-day filter, and what it incidentally solves

`producing_minutes` separates cleanly with no ambiguity: 2026-07-10 (the partial
commissioning day, 2/4 pages, 149 timestamps) reads **385–430 minutes**, while
all 17 other days read **760–835** across 285–290 timestamps. `SHORT_DAY_MINUTES
= 600` sits in an empty gap wider than either cluster.

This matters more than it looks. The deferred C1 finding — *a day truncated by a
pages-1–3 failure is stored with no marker* — is a real gap, but paging is
newest-first, so a truncated day keeps only its **last** 80 samples and its
`producing_minutes` necessarily collapses to well under 600. **The short-day
filter therefore excludes truncated days from every multi-day rule
automatically**, which means C2 does not have to wait on the `pages_ok` schema
column. The column is still worth adding — it distinguishes *why* a day was
short, and a genuinely short winter day would eventually trip this filter — but
it is **no longer a blocker for C2**.

The cost is deliberate and small: a truncated day's `energy_kwh` and
`share_of_total` are still correct (page 0 carries the counters), so excluding it
wholesale discards good daily data to protect the sample-derived rules. One day
of history is a cheap price for not having rule 8 read a morning hole as a
collapse.

## AC vs DC — still never cross-usable

Re-confirmed on all 18 days: `inverter_samples.e_ac_today_kwh` **exceeds**
`sum(epvNToday)` every single day, by 0.90–2.00 kWh (**+0.36% to +0.68%**).
Physically impossible for a real inverter, but it is what the portal reports.
Any efficiency check or AC-denominated share would fire daily. `share_of_total`
correctly uses DC and must keep doing so.

## Still no positive fault example

Unchanged from the C1 baseline and now confirmed over 18 days and 5,045 samples:
`SELECT DISTINCT str_break, str_unbalance, str_unmatch, warn_code, fault_code1,
fault_code2, fault_type, derating_mode` returns exactly one row,
`('0','0','0','0','0','0','0','0')`. Every threshold above is calibrated purely
against "everything nominal" — it bounds the false-positive rate and says
nothing about the true-positive rate. That asymmetry is why every threshold was
chosen with ≥2.5× headroom over the worst healthy observation rather than tuned
to the edge, and why rule 3 (the inverter's own flags) stays high-severity: it is
the only rule here that does not depend on a threshold at all.
