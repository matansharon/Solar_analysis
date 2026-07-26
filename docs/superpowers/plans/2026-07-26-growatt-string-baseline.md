# Growatt String Baseline — observed data from the live 2026-07-26 backfill

**Purpose:** this is the observed-baseline reference Phase C2 calibrates its
anomaly thresholds against. Every number below was read directly from
`data/app.db` after running `python -m solaranalysis.strings --data-dir data
--app-dir . --backfill 90` against the real Growatt portal on 2026-07-26. None
of it is estimated or carried over unchanged from the earlier discovery spike
— where this document's measured numbers disagree with that spike's
expectations, the disagreement is called out explicitly below rather than
smoothed over.

## Plant / inverter identity

- Plant: **Elcam Baram**, Growatt portal id `10950561`.
- One inverter: serial `MZHRF6K002`, model `MAX 70KTL3 LV`, datalogger
  `XGD6CH21BK`.
- **18 live channels**: 6 MPPT inputs (PV1–PV6) + 12 individual strings.
  Confirmed via `SELECT channel_kind, COUNT(*) FROM inverter_channels GROUP BY
  channel_kind` → `mppt: 6, string: 12`.

## Backfill result — and one discrepancy from the pre-run expectation

`--backfill 90` on 2026-07-26 returned exit 0. Every one of the 74 pre-install
days (2026-04-27 through 2026-07-09) printed `no data (pre-install or out of
range)` — **not** `ERROR`. Real data covers **2026-07-10 through 2026-07-25
inclusive — 16 days**, not the ~17 anticipated from the earlier discovery
spike. 16 + 74 = 90, so the arithmetic is internally consistent; the most
likely explanation is that the collector's backfill window ends at yesterday
(2026-07-25, relative to the run date 2026-07-26) rather than including the
still-open current day, one day short of whatever window the spike used. This
is flagged here rather than silently reconciled — it is a real, measured
16-day history, not 17.

2026-07-10 is a partial commissioning day: 2 of 4 pages, 2,682 `channel_samples`
rows (vs. a full day's 5,184 = 288 timestamps × 18 channels). Every day
2026-07-11 onward reports `4/4 pages`, with sample counts fluctuating slightly
around the 5,184 nominal (5,166–5,220, i.e. 287–290 5-minute timestamps/day
depending on how the portal reported that day) — normal portal variation, not
a defect.

Verified counts (`SELECT COUNT(DISTINCT day) FROM channel_day_energy`,
`SELECT COUNT(*) FROM channel_samples`, `SELECT COUNT(*) FROM
inverter_samples`):

| Metric | Value |
|---|---|
| Distinct days with data | **16** (2026-07-10 → 2026-07-25) |
| `channel_samples` rows | **80,496** (exactly the sum of the per-day counts the CLI printed) |
| `inverter_samples` rows | **4,472** |
| Fault-flag combinations seen | **1** — all eight fault/status columns (`str_break`, `str_unbalance`, `str_unmatch`, `warn_code`, `fault_code1`, `fault_code2`, `fault_type`, `derating_mode`) read `0` for every row in the window |

## Per-MPPT-input energy and share_of_total

Query: `SELECT channel_no, day, energy_kwh, share_of_total FROM
channel_day_energy ORDER BY channel_no, day`, grouped by `channel_no` (16 days
each), median/min/max computed over `share_of_total`.

| Input | Days | Energy range (kWh) | share_of_total median | share_of_total min | share_of_total max | share_of_total max−min |
|---|---|---|---|---|---|---|
| PV1 | 16 | 30.7 – 60.9 | 0.19467 | 0.19381 | 0.19717 | 0.00336 |
| PV2 | 16 | 32.1 – 64.8 | 0.20685 | 0.20617 | 0.20734 | 0.00117 |
| PV3 | 16 | 29.6 – 59.5 | 0.18980 | 0.18936 | 0.19030 | 0.00094 |
| PV4 | 16 | 19.9 – 39.8 | 0.12749 | 0.12716 | 0.12792 | 0.00076 |
| PV5 | 16 | 21.7 – 44.0 | 0.14043 | 0.13937 | 0.14117 | 0.00180 |
| PV6 | 16 | 21.7 – 44.0 | 0.14085 | 0.13822 | 0.14186 | **0.00364 ← widest** |

PV1–3 and PV4–6 form two stable groups, as expected: PV1–3 together carry
~59% of plant PV energy (share medians 0.195 + 0.207 + 0.190 = 0.592), PV4–6
the remaining ~41% (0.127 + 0.140 + 0.141 = 0.408). Per-day group ratio
(PV4–6 total ÷ PV1–3 total, `SELECT channel_no, day, energy_kwh FROM
channel_day_energy WHERE channel_no BETWEEN 1 AND 6`, grouped by day):

- Median ratio: **0.6905**
- Min: **0.6851** (2026-07-10 — the partial commissioning day)
- Max: **0.6959** (2026-07-20)

**Note vs. the discovery-spike expectation:** the spike's notes anticipated a
ratio "near 0.66"; the measured ratio across all 16 real days is consistently
**~0.69** (0.685–0.696), not 0.66. The per-day energy magnitudes for the full
days (e.g. PV1–3 topping out at 60.9/64.8/59.5 kWh, PV4–6 at 39.8/44.0/44.0
kWh) do line up closely with the spike's per-day figures, so this looks like
the spike's ratio was rounded or eyeballed rather than computed — the number
above is the one actually measured from stored data and is the one to use.

## Widest single-day swing in any channel's share_of_total (adjacent-day metric)

Per channel, the largest day-over-day absolute change in `share_of_total`
across the 16-day series (adjacent-day deltas only — this is a narrower
metric than the full-window max−min below, see note after the table):

| Input | Widest swing | Between |
|---|---|---|
| PV1 | 0.00261 | 2026-07-10 → 2026-07-11 |
| PV2 | 0.00086 | 2026-07-10 → 2026-07-11 |
| PV3 | 0.00046 | 2026-07-13 → 2026-07-14 |
| PV4 | 0.00065 | 2026-07-10 → 2026-07-11 |
| PV5 | 0.00120 | 2026-07-10 → 2026-07-11 |
| PV6 | **0.00310** | 2026-07-13 → 2026-07-14 |

**Widest overall (adjacent-day): PV6, 0.0031 (0.31 percentage points),
2026-07-13 → 2026-07-14.** Several of the other channels' widest adjacent-day
swings (PV1, PV2, PV4, PV5) span the 2026-07-10 partial-commissioning-day
boundary, which is atypical by construction (short day, only 2/4 pages). PV6's
widest swing and PV3's widest swing both occur between two ordinary full days
(07-13 → 07-14), which is why 0.0031 was previously singled out as "the"
noise figure — but excluding the commissioning-day boundary from the other
four channels' comparisons mechanically narrows this metric; it is not
evidence that the plant's genuine day-to-day noise is lower than 0.31pp, only
that measuring across fewer, more homogeneous day-pairs produces a smaller
observed maximum. See below for the wider, full-window figure and which one
C2 should actually use.

## Full-window max−min per channel — the noise floor for C2

The `Per-MPPT-input energy and share_of_total` table above already gives each
channel's full 16-day min and max `share_of_total`; its max−min column
(repeated here as percentage points for clarity):

| Input | share_of_total max−min (pp) |
|---|---|
| PV1 | 0.336 |
| PV2 | 0.117 |
| PV3 | 0.094 |
| PV4 | 0.076 |
| PV5 | 0.180 |
| PV6 | **0.364 ← widest** |

Two different noise metrics are in play here, and they disagree:

- **Adjacent-day delta (narrower): 0.31pp** (PV6, 07-13 → 07-14, ordinary
  full days only, commissioning day excluded from the comparison).
- **Full-window max−min (wider): 0.36pp** (PV6, comparing the single lowest
  day to the single highest day anywhere in the 16-day window, commissioning
  day included).

**C2 should treat 0.36 percentage points — the full-window max−min — as the
noise floor, not the 0.31pp adjacent-day figure.** A plant with zero string
faults demonstrably moved a channel's share by up to 0.36pp somewhere in this
16-day window, so that is the floor a real anomaly must clear. False
positives are the dominant risk for this baseline — the fault-flag columns
are all-zero for the entire window (see "Fault flags" below), so there is no
positive fault example to calibrate a tighter threshold against — which means
the more conservative, larger figure is the correct default absent a specific
reason to trust the narrower one. The adjacent-day figure is kept above for
reference (it is the cleaner full-day-to-full-day comparison), but it should
not be read as evidence of lower genuine noise, only as a metric computed
over a subset of day-pairs.

**This is the noise floor.** A "share collapse" anomaly rule in Phase C2 must
trigger well outside the **0.36 percentage-point** band measured above (the
per-channel full-window max−min ranges from 0.08pp on PV4 to 0.36pp on PV6,
per the table above), or it will fire on ordinary day-to-day weather
variation rather than a real string fault.

## `pv_iso_kohm` spread

Query: `SELECT MIN/MAX/AVG(pv_iso_kohm), COUNT(*) FROM inverter_samples WHERE
pv_iso_kohm IS NOT NULL` → all 4,472 rows have a non-null value.

- Raw range: **105.0 – 65,530.0 kΩ**, median 1,100.0 kΩ.
- **445 of 4,472 samples (~10%) read exactly 65,530.0 kΩ** — a fixed value near
  but not at the 16-bit unsigned ceiling (65,530 = `0xFFFA`, five below the
  65,535 max), so it reads as a sentinel the isolation-resistance sensor emits
  when it has no valid reading (plausibly when the array is dark, e.g.
  overnight, or the ISO check is otherwise inactive) rather than a genuine
  measured resistance. The exact provenance is unconfirmed; what is measured is
  that the value is discrete, constant, and accounts for ~10% of samples. This
  is flagged explicitly rather than rationalized away: **C2 should exclude or
  specially handle the 65,530 sentinel** before using `pv_iso_kohm` in any
  threshold, or a threshold based on the raw column will be dominated by this
  artifact.
- Excluding the 65,530 sentinel (`pv_iso_kohm < 60000`, 4,027 samples): range
  **105.0 – 7,831.0 kΩ**, median 656.0 kΩ.
- That non-sentinel range is itself a **~75× spread** (7,831 ÷ 105 ≈ 74.6),
  which is a second, independent reason this column needs more work before
  C2 builds a rule on it — separate from the sentinel issue above. Insulation
  resistance is strongly conditions-dependent (humidity, time of day, whether
  the array is electrically connected), so even with the sentinel excluded, a
  plain "trending down from a fixed baseline" rule on the raw kΩ value would
  be extremely noisy. The median of 656.0 kΩ should **not** be read as a
  stable baseline — before any C2 rule uses `pv_iso_kohm`, it needs further
  characterization, e.g. time-of-day bucketing, or a relative/percentile-based
  threshold rather than an absolute kΩ cutoff.

## Peak `temp3_c`

Query: `SELECT MIN/MAX/AVG(temp3_c), COUNT(*) FROM inverter_samples WHERE
temp3_c IS NOT NULL` → all 4,472 rows have a value, range **31.5 – 61.0°C**,
average 45.7°C.

- Peak: **61.0°C** on 2026-07-25 at 10:18:28.
- Next highest: 60.9°C on 2026-07-11 at 12:38:07 and again on 2026-07-13 at
  10:50:20.
- Minimum: 31.5°C, first reached 2026-07-11 at 03:56:34 (pre-dawn).

## Fault flags

`SELECT DISTINCT str_break, str_unbalance, str_unmatch, warn_code, fault_code1,
fault_code2, fault_type, derating_mode FROM inverter_samples` returns exactly
**one** row across the entire 16-day, 4,472-sample window: `("0", "0", "0",
"0", "0", "0", "0", "0")`. No string break, imbalance, mismatch, warning,
fault code, fault type, or derating condition has fired at any point in the
observed history, verified across all eight fault/status-flag columns.
Phase C2 has no positive fault example to calibrate against from
this window — every anomaly rule it builds will be tested only against
"everything nominal" data, which is exactly why the share-of-total and
`pv_iso_kohm` noise-floor numbers above matter: they're the only signal this
baseline can offer about what "normal variation" looks like.

## Queries used

All numbers above came from ad-hoc queries against the populated
`data/app.db`, equivalent to (channel/day energy and share):

```python
import statistics as st
from solaranalysis.web import db
c = db.connect('data/app.db')
rows = [dict(r) for r in c.execute(
    'SELECT channel_no, day, energy_kwh, share_of_total FROM channel_day_energy ORDER BY channel_no, day')]
```

grouped by `channel_no`, with `statistics.median`/`min`/`max` over
`share_of_total`; the group ratio, widest-swing, `pv_iso_kohm`, and `temp3_c`
figures came from direct aggregate queries against `channel_day_energy` and
`inverter_samples` (`MIN`/`MAX`/`AVG`/`COUNT`, and adjacent-day delta
computation in Python) run the same session, against the same populated
database, immediately after the Step 3 backfill and Step 4 verification.
