# Growatt Per-String Collector & Anomaly Report — Design

Date: 2026-07-26
Status: draft — pending user review

## 1. Purpose

SolarEdge got two layers of depth: the adapter (fleet snapshot + history) and the
standalone `solaranalysis/optimizers/` package (Phase B1+B2) that accumulates
per-panel daily energy, detects anomalies, and emails a narrated report. The
Growatt adapter is at rough parity on the first layer and has **nothing** on the
second.

Growatt has no optimizers, so there is no per-panel signal. The finest
granularity it exposes is **per-MPPT-input** and **per-individual-string**
electrical data from the inverter itself, at a 5-minute cadence — including
per-input daily energy counters and the inverter's own string-fault diagnostics.
This is the Growatt analogue of the optimizer work, and on this hardware it is
strictly richer than what SolarEdge provides.

This project builds a **standalone daily collector** that pulls every 5-minute
sample for the Growatt inverter, stores per-input daily energy plus the full
intraday series, **analyzes** it for dead / underperforming / degrading strings,
and emails a **per-string anomaly report**.

### Chosen behavior (from brainstorming, 2026-07-26)

- **New sibling package** `solaranalysis/strings/`, mirroring `optimizers/`.
  Purely additive — no refactor of the merged, 417-test optimizer code. Reuses
  the shared seams (`core.report.render_email_html`, `web.mailer`, `web.db`,
  `web.repo`, `web.crypto`, `web.paths`). Unification with `optimizers/` is
  deferred until SMA becomes a third case.
- **Collect all 4 pages per day** — every 5-minute row (288/day), not just the
  end-of-day sample. Enables intraday shape analysis, guaranteed fault-flag
  capture, and per-string current comparison at the day's peak.
- **Storage:** shared `app.db` (new v7 tables), as a *projection* of the 260
  fields per row rather than the raw payload.
- **Thresholds calibrated against real stored data**, not guessed: the collector
  and store land first, the 17 available days are backfilled, and only then are
  `analyze.py`'s cutoffs written against the observed series. With 6 channels on
  a two-week-old inverter and no portal-supplied normalized performance value,
  guessed cutoffs are the dominant false-positive risk.
- **Delivery:** its own daily email, separate from the optimizer report.

### Non-goals

- **Changes to `adapters/growatt.py`** — the fleet adapter is untouched. The CLI
  reuses `GrowattAdapter` only for login and session reuse.
- **Surfacing flagged channels in the web UI** (as `PlantData` alerts) — noted as
  a follow-up.
- **Retention pruning** of the 5-minute tables — follow-up, alongside the
  existing `optimizer_energy`/`raw_payloads` prune backlog item.
- **Unifying with `optimizers/`** — premature with two dissimilar data models.
- **Remote commands / writes** — strictly read-only.

## 2. Discovered Growatt device API (verified live 2026-07-26)

Authenticated POSTs on `https://server.growatt.com`, reusing the existing
headless-browser session (cookies shared via `context.request`). As already
documented in `adapters/growatt.py`, this portal requires **form-encoded**
params — query strings return null. Spike artifacts are in
`.discovery/growatt/string-spike/` (git-ignored; fixtures get sanitized into
`tests/fixtures/` during implementation).

| Endpoint | Form params | Returns |
|---|---|---|
| `POST /panel/getDevicesByPlantList` | `plantId`, `currPage` | `obj.datas[]` — `sn`, `deviceModel`, `deviceTypeName` (`max`), `datalogSn`, `nominalPower`, `eToday/eMonth/eTotal`, `pac`, `status`, `lastUpdateTime`. Already used by the adapter; here it supplies the serial + datalogger the history call needs. |
| `POST /device/getMAXHistory` | `maxSn`, `startDate`, `endDate`, `start`, `allDatalogSns` | **The payload this whole design rests on.** `obj.datas[]` — 5-minute samples, **260 fields each**, newest-first. Envelope: `obj.{totalRecord, totalPageSize, haveNext, start, endDate}`. |

Endpoint name confirmed from the portal's own inline JS (`/device/getMAXHisPage`
is the UI page; `getMAXHistory` is its data call). `newMaxApi.do` does not exist
on this host. `/device/photovoltaic` and `/device/getMAXHisPage` are GET pages
(405 on POST) and are not needed.

### Paging and day semantics (verified)

- **80 rows per page.** `start` is a **0-based page index** on input; the
  response's `start` field is the *next* page index. `haveNext` terminates.
- **288 rows per day** — a full 24 h at 5-minute cadence, including night.
- `startDate == endDate == D` returns exactly day `D`, newest-first, so **page 0
  row 0 is that day's last sample (~23:57)**. A 4-page walk yields the whole day
  (80+80+80+48 = 288, matching `totalRecord`).
- `time` is `"YYYY-MM-DD HH:MM:SS"` in **plant-local** time (plant timezone
  `2.0`), so day keys are plant-local — consistent with the optimizer collector.
  A sibling `calendar` object exists but its `month` is **0-based** (July → 6);
  parse `time`, not `calendar`.

### Field families in a sample row

| Family | Fields | Notes |
|---|---|---|
| MPPT inputs 1..16 | `ipvN` (A), `vpvN` (V), `ppvN` (W), `epvNToday` (kWh), `epvNTotal` (kWh) | `epvNToday` is a **cumulative daily counter that resets at midnight** — verified rising 0.0 → 57.3 across a day. The only per-channel *energy* available. |
| Individual strings 1..32 | `currentStringN` (A), `vStringN` (V) | Current + voltage only, **no energy**. |
| Inverter | `pac`, `pacr/s/t`, `eacToday`, `eacTotal`, `ppv`, `epvTotal`, `vac*`, `iac*`, `fac`, `pf`, `temperature`..`temperature5`, `pvIso` (kΩ), `gfci` (mA), `status`, `deratingMode`, `realOPPercent`, `timeTotal` | |
| Native fault/warn | `StrBreak`, `StrUnblance`, `StrUnmatch`, `WarnBit`, `warnCode`, `warnCode1`, `warningValue1..3`, `faultCode1`, `faultCode2`, `faultType`, `faultValue`, `afciStatus` | The inverter's **own** string diagnostics — break, unbalance, mismatch. All zero across the 17 days sampled: a clean baseline, authoritative when they fire. |

**Units are trustworthy:** `sum(epvNTotal) == epvTotal` exactly (4832.0 observed).
Energy is kWh, power W, current A, voltage V. No conversion guesswork.

## 3. Site reality and the constraints it imposes

The Growatt account is **one plant, one inverter**: plant `10950561`
"Elcam Baram", inverter `MZHRF6K002`, `MAX 70KTL3 LV`, 70 kW nominal, on a
ShineWiFi-X datalogger (`XGD6CH21BK`).

**(a) 6 active MPPT inputs of 16.** PV1–PV6 produce; PV7–PV16 have
`epvNTotal == 0` — never produced. PV7 shows ~227 V open-circuit with zero
current, i.e. an unused input picking up stray voltage, **not** a fault. The
analyzer must exclude never-produced channels rather than flag them dead forever.

**(b) 12 active individual strings, 2 per MPPT input.** `vString` values arrive
in **exactly-equal groups** (`vString1 == vString2 == 408.30002`,
`vString3 == vString4 == 402.7`, …) because parallel strings on one MPPT
necessarily share voltage. Grouping by exact voltage equality is therefore
reliable. Mapping a group back to a specific `vpvN`/`ipvN` is **not** — pair
current sums do not cleanly biject to the MPPT currents. So the string→MPPT link
is best-effort and nullable, and the imbalance rule depends only on the
exact-voltage grouping.

**(c) The channels are not equal peers.** Daily energy splits into two stable
groups: PV1–3 ≈ 57/61/56 kWh and PV4–6 ≈ 38/42/42 kWh, a **0.66 ratio held
across all 17 days** — PV4–6 are structurally smaller arrays, not degraded. This
invalidates the optimizer analyzer's central assumption (equal-peer panels
compared to a string median) and there is no Growatt equivalent of SolarEdge's
normalized `color`. Within a group, however, day-to-day spread is under 3%.

The resolution: **share-of-total** — each channel's fraction of the plant's daily
energy. Because `sum(epvNTotal) == epvTotal` exactly, weather cancels out
completely, and each channel is compared against **its own** trailing median
rather than against unequal siblings.

**(d) Only 17 days of history exist, and there is no deep backfill.** Data begins
**2026-07-10** (partial, 149 rows — commissioning day); 2026-07-09 and earlier
return `result: 1` with empty `datas`. Beyond roughly 90 days the endpoint hard
rejects with `result: 0` (2026-04-01 and older). The cause: **the inverter was
replaced around 2026-07-10** — the 2026-07-07 capture recorded serial
`MZHRF6K012` at 9807.6 kWh lifetime, where today's is `MZHRF6K002` at 4857.6 kWh.

Consequences: the series must accumulate forward; `--backfill` is capped by
reality at ~17 days today; and every multi-day rule must declare a minimum
history and **skip rather than fire** below it. On a two-week-old inverter with 6
channels, false positives are the primary risk.

## 4. Architecture

```
solaranalysis/strings/
├── __init__.py
├── __main__.py
├── _now.py             # now_utc(), mirroring optimizers/_now.py
├── history_client.py   # IO shell: authenticated POSTs, no parsing
├── mappers.py          # pure: payload -> records
├── store.py            # upserts + window loaders (shared app.db)
├── analyze.py          # pure: series -> anomalies (no IO, no clock)
├── report.py           # grounded block -> narrative -> markdown
└── cli.py              # python -m solaranalysis.strings
```

`history_client.py`: `get_device_list(bs, plant_id)`,
`get_history_page(bs, sn, day, page, datalog_sn)`. Bounded retry per POST — this
portal intermittently stalls requests outright (see `_goto_retry` in
`adapters/growatt.py`).

`mappers.py` (all pure, fixture-testable without a browser):

- `parse_devices(payload) -> list[InverterInfo]` — sn, model, datalog_sn,
  nominal_power_w.
- `channel_inventory(rows) -> list[ChannelInfo]` — which channels are live:
  MPPT inputs with `epvNTotal > 0`, individual strings with a non-null
  `currentStringN`/`vStringN`; groups strings by exact `vString` equality and
  best-effort-links each group to an MPPT input.
- `map_day_energy(rows) -> list[ChannelDayEnergy]` — per-input `energy_kwh` from
  the day's **maximum** `epvNToday` (robust to a mid-day datalogger restate; see
  §10), plus `share_of_total`, `peak_w`, `peak_at` and `producing_minutes`
  derived across the day's samples.
- `map_channel_samples(rows) -> list[ChannelSample]` — flattens 288 rows ×
  channels into per-(time, channel) power/voltage/current.
- `map_inverter_samples(rows) -> list[InverterSample]` — per-sample health and
  fault fields.

`collector.py`: per-day orchestration with per-day isolation (commit/rollback per
day, mirroring the optimizer collector's per-site isolation), plus `day_range`.

### Data flow (per daily run)

1. Load the enabled Growatt plant's auth from `app.db` (`repo.load_plant_auth`),
   build `GrowattAdapter`, `login()`, reuse the cached browser session.
2. Fetch the device list → serial + datalogger + model.
3. Per target day: walk pages 0..3 of `getMAXHistory` → sample rows.
4. Map → inventory (upsert), per-input daily energy (upsert), channel samples,
   inverter samples.
5. Analyze the accumulated series → anomalies.
6. Render a grounded report and email it.

## 5. Storage (shared `app.db`; additive DDL, `SCHEMA_VERSION` → 7 after Phase B1's 6)

```sql
CREATE TABLE IF NOT EXISTS inverter_channels(
  device_sn TEXT NOT NULL,
  channel_kind TEXT NOT NULL CHECK (channel_kind IN ('mppt','string')),
  channel_no INTEGER NOT NULL,
  parent_channel_no INTEGER,        -- for 'string': its MPPT input (best-effort, nullable)
  group_voltage REAL,               -- the exact shared vString that grouped it
  plant_uid TEXT,                   -- 'growatt-10950561'
  lifetime_kwh REAL,                -- epvNTotal; 0 => never produced => excluded from analysis
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_kind, channel_no)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS channel_day_energy(
  device_sn TEXT NOT NULL,
  channel_no INTEGER NOT NULL,      -- MPPT inputs only (the only tier with energy)
  day TEXT NOT NULL,                -- 'YYYY-MM-DD', plant-local
  energy_kwh REAL,
  share_of_total REAL,              -- energy_kwh / plant day total; weather-immune
  peak_w REAL,
  peak_at TEXT,
  producing_minutes INTEGER,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (device_sn, channel_no, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chanenergy_day ON channel_day_energy(device_sn, day);

CREATE TABLE IF NOT EXISTS channel_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,         -- 'YYYY-MM-DD HH:MM:SS', plant-local
  day TEXT NOT NULL,
  channel_kind TEXT NOT NULL,
  channel_no INTEGER NOT NULL,
  power_w REAL,                     -- NULL for 'string' (not reported)
  voltage_v REAL,
  current_a REAL,
  PRIMARY KEY (device_sn, sampled_at, channel_kind, channel_no)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_chansamples_day ON channel_samples(device_sn, day);

CREATE TABLE IF NOT EXISTS inverter_samples(
  device_sn TEXT NOT NULL,
  sampled_at TEXT NOT NULL,
  day TEXT NOT NULL,
  pac_w REAL,
  e_ac_today_kwh REAL,
  temp_c REAL, temp2_c REAL, temp3_c REAL, temp4_c REAL, temp5_c REAL,
  pv_iso_kohm REAL,
  gfci_ma REAL,
  status TEXT,
  derating_mode TEXT,
  str_break TEXT, str_unbalance TEXT, str_unmatch TEXT,
  warn_code TEXT, fault_code1 TEXT, fault_code2 TEXT, fault_type TEXT,
  PRIMARY KEY (device_sn, sampled_at)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_invsamples_day ON inverter_samples(device_sn, day);
```

Volume: ~5,200 `channel_samples` + 288 `inverter_samples` + 6
`channel_day_energy` rows per day (~1.9M rows/year before pruning). The two
sample tables are the prune targets; `channel_day_energy` is small and kept.

Fault/warn codes are stored as TEXT because the portal returns them as strings
(`"0"`), and preserving the raw token avoids lossy coercion.

## 6. Analysis (pure; thresholds calibrated against real data)

`analyze.py` takes loaded inventory + rows + an explicit `as_of_day` — no IO, no
clock — matching `optimizers/analyze.py`. Thresholds are named module constants.

**Sequencing.** The collector, store and backfill land *first*. The 17 available
days are then inspected, and the constants are written against that observed
series.

That checkpoint is a natural seam, so this spec becomes **two implementation
plans**, mirroring how the optimizer work split into B1/B2:

- **Phase C1** — schema v7, `history_client`, `mappers`, `store`, `collector`,
  CLI collect path, backfill. Ends with the 17 days stored and inspected.
- **Phase C2** — `analyze` (thresholds calibrated against C1's stored series),
  `report`, email, `DEPLOYMENT.md` §12.

Rules, roughly in severity order — rule 1 is an exclusion filter, not a finding:

1. **Never-produced** — `lifetime_kwh == 0` → excluded from analysis entirely.
   Never flagged. (PV7–16 here, incl. PV7's stray 227 V.)
2. **Stopped reporting / dead** — in inventory but absent from recent days, or
   zero energy for N consecutive days while the plant produced. A missing row is
   not a zero row; both cases are handled (the bug already fixed once in the
   optimizer analyzer).
3. **Native inverter flags** — any non-zero `StrBreak` / `StrUnblance` /
   `StrUnmatch` / `warnCode` / `faultCode1` / `faultCode2` during the day.
   Authoritative — this is the inverter's own diagnosis — so high severity.
4. **Share collapse** (primary rule) — `share_of_total` below the channel's own
   trailing-median share by more than a tolerance. Weather-immune; requires no
   peer equality.
5. **Persistent underperformance** — share low on ≥3 of the last 5 days, so a
   single cloudy-afternoon dip does not fire.
6. **String-pair imbalance** — within an exact-voltage group, member
   `current_a` at the day's peak-power sample diverging persistently. The finest
   signal available and it has no SolarEdge equivalent: it catches one bad string
   in a parallel pair, which per-MPPT energy averages away.
7. **Degrading** — recent-7 vs prior-7 mean share. Needs ≥14 days; only became
   possible on this inverter within the last few days.
8. **Intraday shape** — a channel collapsing over a contiguous
   morning/afternoon window while peers hold: a shading/soiling signature rather
   than a hardware fault. Enabled by storing all 288 samples.
9. **Health drift** — `pv_iso_kohm` trending down (insulation degradation, a real
   safety signal); temperature outliers across `temp_c..temp5_c`.

Every multi-day rule declares its minimum history and is **skipped, not fired**,
below it. Every figure the report states is computed here.

## 7. Report & email

`report.py` mirrors `optimizers/report.py`: an authoritative grounded data block
(every number straight from the anomaly records), a single Claude call that
writes narrative prose only, then markdown → `core.report.render_email_html` →
`web.mailer.send_report`. The narrative call is **skipped outright on an
all-clear day**, and a narrator failure degrades to tables-only.

- Subject: `Growatt Strings · {N} flagged · {YYYY-MM-DD}`.
- Recipients: `STRING_RECIPIENTS` if set, else `mailer.recipients()` — the same
  override pattern as `OPTIMIZER_RECIPIENTS`.
- Language from the app's `output_language` setting, as elsewhere.

Its own email rather than merging into the optimizer report: different platform,
different failure modes, and the optimizer path is already documented and
scheduled in `DEPLOYMENT.md` §11. This one gets a §12.

## 8. CLI, backfill & scheduling

```
python -m solaranalysis.strings --data-dir <dir> --app-dir <dir>
    [--date YYYY-MM-DD]   # default: yesterday (last complete day)
    [--backfill N]        # N days ending at --date (default 1)
    [--no-email]          # collect + analyze only
```

- Exit **2** if no enabled Growatt plant is configured in `app.db`; exit **3** if
  the device list comes back empty (an empty list is almost always a failed or
  unauthorized fetch, not a genuinely deviceless plant) — matching the optimizer
  CLI's contract.
- **Failure isolation.** Page 0 carries end-of-day energy, so **page 0 failing
  fails the day** (rollback, continue to the next day); **pages 1–3 failing still
  stores the day's energy**, with samples recorded as partial. An empty day
  (pre-install, or beyond the ~90-day rejection window) stores nothing and is
  **not** an error.
- **Backfill:** `--backfill 90` is safe — pre-install days return empty and are
  skipped — but today it will only find ~17 days. 4 POSTs/day, ~68 calls,
  one login via the session cache.
- **Scheduling:** a daily Scheduled Task on the server after the fleet run, as an
  ops step in `DEPLOYMENT.md` §12, not code.

## 9. Testing

TDD throughout, following the optimizer plan's structure.

- **Mappers (pure, fixture-driven):** a sanitized real `getMAXHistory` page into
  `tests/fixtures/growatt_max_history.json` → 6 active MPPT inputs with correct
  kWh, 12 string currents, exact-voltage grouping, and the day's last-sample
  energy. Malformed/partial payloads degrade gracefully rather than crash. The
  0-based `calendar.month` trap gets an explicit test.
- **Store:** upsert idempotency (re-running a day must not duplicate),
  never-produced channels recorded but marked, in-memory SQLite.
- **Analyze:** synthetic series per rule — unequal-peer groups that must *not*
  fire, never-produced channels, a dead channel, a single-day dip, a persistent
  underperformer, a pair imbalance, and insufficient history for each multi-day
  rule.
- **Collector:** per-day isolation with fake sessions; page-0 failure vs pages
  1–3 failure; empty-day handling.
- **Report:** grounded-numbers check; all-clear skip; narrator-failure fallback.
- Live smoke run against the real portal, as with the other adapters.

## 10. Open items to confirm during implementation (low-risk)

- **Threshold values** in `analyze.py` — deliberately deferred to the calibration
  checkpoint after backfill (§6).
- **String→MPPT association** — best-effort and nullable by design; if a reliable
  mapping emerges from more data, the `parent_channel_no` column is already
  there.
- **`producing_minutes` definition** — samples with `power_w > 0`; exact cutoff
  settled against real data.
- **Whether `epvNToday` ever resets mid-day** (a datalogger reconnect could
  restate it). The 17 days sampled show clean monotonic daily ramps; the mapper
  should take the day's **maximum** `epvNToday` rather than blindly the last
  sample, which is robust either way.
- **Inverter replacement handling** — rows are keyed by `device_sn`, so a swap
  starts a fresh series rather than corrupting the old one. Worth surfacing in
  the report when the active serial changes.
