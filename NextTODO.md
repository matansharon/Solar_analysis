# NextTODO — solar-analysis

**Last updated:** 2026-07-28

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- `master` is **24 commits ahead of `origin/master`** as of this wrap-up (Phase C1 merged locally, never pushed). `DEPLOYMENT.md` step 2 pulls from origin, so **push before deploying** or the server will not have `solaranalysis/strings/` at all.
- The deploy is **hands-on-server work**: llmadmin answers on SMB/WinRM but this dev machine has no usable remote-exec path (WinRM needs a TrustedHosts change + elevation; RPC to the service manager is denied). Every `DEPLOYMENT.md` command has to be run in PowerShell on the server itself — including `nssm status SolarAnalysis`, so "not deployed" is an assumption that has never been verified from here.

## Done this session (2026-07-26 → 2026-07-28)
**Phase C1 — the Growatt per-string collector — designed, built, merged and smoke-tested.** Full arc: discovery spike → spec → plan → 11 subagent-driven TDD tasks with a review after each → whole-branch review → merge to `master` → live smoke test. Suite **417 → 498**.

- **Discovery spike first.** The design hinged on one unknown, so three rounds of live probing settled it before any code: `POST /device/getMAXHistory` (form-encoded; `start` is a 0-based *page* index) returns 288 five-minute samples/day across 4 pages of 80, newest-first, 260 fields each. The decisive field is `epvNToday` — a per-MPPT-input kWh counter that **resets at midnight**, so page 0 row 0 carries the day's totals and no power integration is needed. Also found: `sum(epvNTotal) == epvTotal` exactly, and the inverter's own `StrBreak`/`StrUnblance`/`StrUnmatch` string diagnostics.
- **Three spike findings shaped the whole design.** (1) The inverter was **replaced ~2026-07-10** (serial `MZHRF6K012` @ 9,807 kWh on 07-07 vs `MZHRF6K002` @ 4,857 kWh now), so only ~2 weeks of history exists and the series must accumulate forward. (2) The channels are **not equal peers** — PV1–3 carry ~0.197 share each against PV4–6's ~0.136, a stable 0.69 ratio, because PV4–6 are physically smaller arrays. That invalidates the optimizer analyzer's peer-median approach, so the design uses **share-of-total against each channel's own history** (weather cancels exactly). (3) **10 of 16 MPPT inputs never produced** and must be excluded rather than flagged dead forever — PV7 floats a real 227 V open-circuit with zero current.
- **Shipped:** `solaranalysis/strings/` — `history_client` (IO shell, bounded retry), `mappers` (five pure mappers), `store` (idempotent upserts + loaders), `collector` (per-day page walk, per-day rollback isolation), `cli`. Plus **schema v7**: `inverter_channels`, `channel_day_energy`, `channel_samples`, `inverter_samples`. Spec `specs/2026-07-26-growatt-string-collector-design.md`, plan `docs/superpowers/plans/2026-07-26-growatt-string-collector-c1.md`.
- **Live-proven, not just unit-tested.** The real `app.db` migrated 6 → v7 additively; a 90-day backfill stored 16 days (2026-07-10 → 07-25) with all 74 pre-install days correctly reported `no data` rather than `ERROR`; 18 channels; 80,496 `channel_samples`. 2026-07-25's total independently reproduced the test fixture's 292.6 kWh. Measured baseline in `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md`.
- **The reviews earned their keep — three things would have shipped broken:**
  - **A Critical the eleven per-task reviews structurally could not see:** a non-2xx from the portal was laundered into "this day has no data" at exit 0. A 502 on the 06:30 scheduled run would have lost the day permanently and looked successful. This was a defect in behaviour **the plan itself mandated** — the plan's own test pinned the bug. Fixed by raising inside the existing retry loop; safe because both legitimate empty cases arrive as HTTP 200 with a parseable body, so a null body can only be transport/auth.
  - **A rollback test that proved nothing** — it failed at page 0, before any write, so its assertions were trivially true. Replaced with a monkeypatched failure *after* 18 rows land, plus a negative control that fails with "18 more items" when `rollback()` is removed.
  - **The baseline's noise floor was understated** (0.31pp adjacent-day vs 0.36pp full-window, unreconciled), which would have set C2's threshold ~16% tighter than the data supports.
- **Post-review fixes landed before merge:** samples now insert **ascending** (measured 20.39 → 11.62 MB on identical DDL; real-world 1.24 → **0.73 MB/day**, a 41% saving); the CLI **exits 4** when any day fails (it previously returned 0 even if every day errored — invisible to a Scheduled Task); and one `_lifetime_kwh` helper taking the day's **max** replaced two divergent order-dependent lookups.
- **Live smoke test (2026-07-28)** on the merged code: exit-2 path clean, live `--backfill 2` pulled 07-26 and 07-27 at `4/4 pages`, series now **18 contiguous days**. Two cross-validations: lifetime counters advanced by exactly the collected energy (597.1 = 291.7 + 305.4), and the storage fix confirmed in production. One finding — **PV4 stepped just outside its 16-day share envelope** on both new days, so the noise floor is a lower bound that widens with more data.
- **`DEPLOYMENT.md` §12** written: the string collector's dry run, backfill, daily 06:45 Scheduled Task, `PYTHONIOENCODING=utf-8`, the exit-code contract, the disk-growth figure, and the migrate-on-first-run note — each with a verify.

## Done — Phase C1: Growatt per-string collector (MERGED)
Standalone `solaranalysis/strings/`: `history_client`/`mappers`/`store`/`collector`/`cli`, mirroring `optimizers/`. Collects per-MPPT-input daily energy (`epvNToday`, max-per-day) plus the full 5-minute per-channel and inverter-health series into `app.db` (schema →v7, four tables), with `--backfill N`, per-day rollback isolation, and page-0-critical failure semantics. Analysis/report/email are Phase C2. 498 tests. Whole-branch review "Ready to merge: Yes with follow-ups".

## Done — 2026-07-26 session: optimizer report polish + deploy docs
- **Optimizer report polish** (2 backlog items, TDD, 410 → **417 tests**): friendly **site names** now reach the report — `collector.parse_site_names` reads them off the same sitelist payload `parse_site_ids` already used, and the CLI threads them into both the grounded block and the markdown, so headings read "Baram (site 2387929)". The fetch is guarded so name resolution can never sink a collection run, including the `--sites` path that previously skipped the call entirely. And **fully-silent optimizers are flagged dead**: a missing row is not a zero row, so an optimizer that drops out of the payload was invisible to every rule and fell through with a stale latest-day; it now reads "stopped reporting for N days". Extracted `cli.compose_report` as a testable seam, which also brought the all-clear narrative skip and the narrator-failure fallback under test.
- **Found and fixed a deploy blocker in the docs**: `DEPLOYMENT.md` never mentioned `playwright install chromium`. Every portal login drives a real Chromium, so the first snapshot run on a fresh server would have failed — and the default install location (`%LOCALAPPDATA%` of the installing user) is invisible to a `LocalSystem` service anyway. Step 3 now installs to a machine-scope `PLAYWRIGHT_BROWSERS_PATH`.
- **`DEPLOYMENT.md` §11**: the optimizer collector's dry run, 90-day backfill, and daily 06:30 Scheduled Task, each with a verify — plus the ordering dependency that it cannot run before the SolarEdge plant exists in the UI (exit 2). Added a Troubleshooting section covering the Playwright/LocalSystem case, stale `data\session_cache`, exit 2 vs exit 3, and the fact that a 0 exit code does not by itself mean mail went out.

## Done — Phase A: daily raw-snapshot persistence (MERGED)
Every web-app run persists the **untouched portal JSON** (new **`raw_payloads`** table, schema →v5, zlib, opt-in `record_raw`, web-runner only, excluded from the LLM); SolarEdge emits **`energyYesterday`** as a daily `energy_points` row. Subagent-driven TDD, whole-branch review "Yes", 368 tests. Spec `specs/2026-07-23-daily-raw-persistence-design.md`.

## Done — Phase B1: SolarEdge per-optimizer collector (MERGED)
Standalone `solaranalysis/optimizers/`: `mappers`/`layout_client`/`store`/`collector`/`cli`. Pulls every optimizer's daily energy + inventory across all sites via `/services/layout/*` into `app.db` (new `optimizers` + `optimizer_energy` tables, schema →v6), with `--backfill N` and per-site DB-level failure isolation. 390 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-collector.md`.

## Done — Phase B2: optimizer anomaly analysis + report + email (MERGED)
On top of B1's stored series: `optimizers/analyze.py` (pure `analyze_site` — dead / underperforming / degrading / watch, via the peer-normalized `color` + string-median + multi-day persistence + a recent-vs-prior degradation trend) and `optimizers/report.py` (grounded data block → Claude narrative → markdown → email-safe HTML via `core.report.render_email_html`). The `optimizers` CLI now analyzes + emails after collecting (`--no-email`; skips the Opus call on all-clear days; `OPTIMIZER_RECIPIENTS` override wired through a new `mailer.send_report(to=…)`). Subagent-driven TDD, whole-branch review "Ready to merge: Yes", 410 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-analysis-report.md`.

## Next
- [ ] **Phase C2** — analyze + report + email for the Growatt string collector,
  thresholds calibrated from
  `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md`.
  **Read this list first — it is what C1's whole-branch review found, and none of
  it is derivable from the code:**
  - **The baseline has NO data for the spec's flagship rule.** Spec §6 rule 6
    (string-pair imbalance) is called "the finest signal available", but the
    review measured the *healthy* plant at **12.2–22.5% pair imbalance, median
    16.7%**, stable per pair (pair 3/4 sits at 20.5–22.5% every single day). Any
    **absolute** threshold under ~25% fires on all six healthy pairs on day one.
    The correct rule shape is the same compare-each-pair-against-its-own-history
    insight already derived for share-of-total. **Gather this calibration data
    before writing rule 6.**
  - **The noise floor for share-of-total is 0.36 percentage points** (PV6
    full-window max−min), and **treat it as a lower bound, not a ceiling.** A
    smoke test on 2026-07-28 collected two further days (07-26, 07-27; the series
    is now **18 contiguous days**, 07-10 → 07-27). Five of six inputs landed
    inside their 16-day envelope — but **PV4 exceeded its baseline max on both
    new days** (0.12821 and 0.12803 vs a measured max of 0.1279). A 0.03pp
    excursion, no kind of fault, but direct evidence that a 16-day max−min
    understates the long-run spread. Recompute the envelope from whatever is
    stored when C2 sets its thresholds rather than hardcoding the baseline doc's
    figures.
  - **Group pairs from `channel_samples.voltage_v`** at each day's peak, not from
    `inverter_channels.group_voltage` — the latter is a last-write-wins snapshot,
    the former is immutable history. `parent_channel_no` is deliberately NULL and
    the rule doesn't need it.
  - **Never cross-use AC and DC counters.** `inverter_samples.e_ac_today_kwh`
    *exceeds* `sum(epvNToday)` on all 16 days by 0.9–2.0 kWh (~0.5%) — physically
    impossible, but it is what the portal reports. Any efficiency sanity-check or
    AC-denominated share would fire daily. `share_of_total` correctly uses DC.
  - **`temp4_c` is constant 0.0** in all 4,472 samples (sensor absent on this
    hardware). A naive §6 rule 9 "temperature outlier across temp_c..temp5_c"
    would flag it as a permanent cold outlier forever.
  - **`pv_iso_kohm` is not threshold-able as stored.** Exclude the 65,530
    sentinel (10.0% of samples), and note that even excluding it the range is
    105–7,831 kΩ — a ~75× spread. Needs time-of-day bucketing or a percentile
    rule, not an absolute kΩ cutoff. `status` ∈ {"0","1"} is a cleaner daylight
    filter than `pac > 0`.
  - **2026-07-10 is a half day** (149 samples) inside any 14-day degradation
    window. `producing_minutes` (385–430 vs 765–835) is the clean discriminator;
    there is no short-day flag.
  - **Zero positive fault examples exist** — all 8 fault/status columns are "0"
    across the whole window. Every rule will be tested only against nominal data.
- [ ] **Partiality marker (deferred C1 finding, do before trusting intraday
      rules)** — a day truncated by a pages-1–3 failure is stored with no marker.
  Paging is newest-first, so only the day's *last* 80 samples survive:
  `energy_kwh`/`share_of_total` stay correct (page 0 carries the counters) but
  `peak_w`, `peak_at` and `producing_minutes` come out evening-only and the
  intraday series has a morning hole that rule 8 would read as a collapse. A row
  count can't tell a genuinely short day (2026-07-10, complete) from a truncated
  one. Fix: one additive nullable column on `channel_day_energy` (`pages_ok` or
  `complete`), written from the value `collector.collect_day` already computes.
- [ ] **`parse_devices` ignores `deviceTypeName`** — `getMAXHistory` is
  MAX-specific, so adding a MIN or storage device to the plant would POST its
  serial to a MAX endpoint. One-line guard.
- [ ] **Retention prune for `channel_samples` / `inverter_samples`** — joins
  the existing `optimizer_energy` / `raw_payloads` prune backlog item. Now
  quantified: the string tables grow ~**0.73 MB/day** (~260 MB/year) in the same
  `app.db` as the web app's data, so the DB will dominate backups within a year.
- [ ] **Exit-4 path has never fired against the real portal** — it is unit-tested
  via the pure `cli.exit_code` helper, but a live portal failure has not been
  observed end to end. Expect it to surface naturally once the scheduled task runs.
- [ ] **First-time deploy to llmadmin per `DEPLOYMENT.md`** (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS [+ optional OPTIMIZER_RECIPIENTS]) and `config.yaml` by hand (both gitignored). **Push `master` first** — see Deploy status. **Run on the server.**
  - `DEPLOYMENT.md` step 3 now covers `playwright install chromium` with a machine-scope `PLAYWRIGHT_BROWSERS_PATH` — this was **missing** before and would have failed the first portal login under `LocalSystem`.
- [ ] **Optimizer live smoke run (was B1 Task 8 + B2 Task 7)** — needs real SolarEdge creds; now written up as `DEPLOYMENT.md` §11 (dry run → `--backfill 90` → scheduled task). Confirm the 4 sites populate `optimizers`/`optimizer_energy` AND an anomaly email arrives; spot-check one flagged optimizer against the Digital-Twin panel.
- [ ] **Schedules (ops, on the server):** web UI daily fleet run ~06:00 (Settings/Schedules, all days, range `snapshot`); optimizer collector+report ~06:30 (§11); Growatt string collector ~06:45 (§12). All three documented in `DEPLOYMENT.md`, none yet *created* on the server.
- [ ] Backlog (non-blocking): populate optimizer module tilt/azimuth/make/model (per-optimizer info call — not in the layout tree); strengthen analyze sort-ordering + degradation-guard tests; Phase A `base.py._finish_raw` import placement; `db.connect` sets no `busy_timeout` so web-app contention surfaces at sqlite3's 5 s default.

## Environment note
The Git-Bash tool's PATH resolved bare `git`/`bash` fine throughout this session (2026-07-26 → 07-28). If the earlier breakage recurs, prefix with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"` or just use PowerShell for git.
