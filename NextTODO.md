# NextTODO — solar-analysis

**Last updated:** 2026-08-02

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- C1's commits **were pushed** — as of 2026-08-02 `master` == `origin/master` at `c44f7a5`, so the earlier "24 commits ahead" warning is resolved. **This session's C2 work is uncommitted.** `DEPLOYMENT.md` step 2 pulls from origin, so commit and push before deploying.
- The deploy is **hands-on-server work**: llmadmin answers on SMB/WinRM but this dev machine has no usable remote-exec path (WinRM needs a TrustedHosts change + elevation; RPC to the service manager is denied). Every `DEPLOYMENT.md` command has to be run in PowerShell on the server itself — including `nssm status SolarAnalysis`, so "not deployed" is an assumption that has never been verified from here.

## Done this session (2026-08-02) — Phase C2: string analysis + report + email

**Built, calibrated against the live database, validated, smoke-tested end to
end against the real portal, and whole-branch reviewed. Suite 498 → 583 (85 new
tests).**

- **The whole-branch review found one thing worth the trouble: rule 7 was
  unreachable for the case it was written for, and its only positive test did
  not test it.** `test_degrading_channel_is_flagged_over_two_seven_day_windows`
  asserted `severity in ("degrading", "underperforming")` and in fact produced
  `underperforming` — rule 4. Sweeping sustained declines from −2% to −10%
  produces `underperforming` at every level that fires and `degrading` at none:
  rules 4/5 `continue` before rule 7, and rule 5 (−2% on 3 of 5 days) is
  strictly more sensitive than rule 7 (−3% between two 7-day means), so **rule 7
  adds no detection over rule 5 for a decline still present today.** It is
  reachable only for a dip in the older half of the recent-7 window that has
  since recovered — narrow but real (intermittent connection, shading that
  cleared), so the rule was **kept and tested honestly** rather than dropped:
  one test now fires it on that shape, and a second pins the shadowing so it
  cannot silently become an assumption that rule 7 catches gradual degradation.
- **Rule 7's provenance figure was stale in two places.** `analyze.py` and the
  calibration doc's summary table still carried the 17-day −0.29%/10× while the
  same doc's erosion section and NextTODO carried the corrected 22-day
  −0.46%/6.5×. Rule 6's 1.02 → 1.45pp update had propagated everywhere; rule 7's
  had propagated nowhere. Corrected in both.
- **The calibration tool counted rule 9's false positives with the wrong
  comparison** — `abs(dev) > t`, which counts temperature *drops* as breaches,
  while the rule only fires on a rise. `_curve` now takes an explicit
  `drop`/`rise`/`abs` mode that has to match the analyzer's own comparison.
- Reviewed and deliberately left alone: an email failure prints and leaves the
  exit code at 0. That shape produced C1's flagship Critical so it was checked
  closely, but here it is documented in `DEPLOYMENT.md`, matches
  `optimizers/cli.py`, and the strings CLI is actually the stricter of the two
  (it returns `exit_code(flat_results)`; the optimizer CLI hardcodes `return 0`).

**Live smoke test (2026-08-02), all green:** a 5-day collection filled the
07-28 → 08-01 gap (`4/4 pages` every day, exit 0, series now **23 days**);
the analyzer reported all-clear on 08-01; the report emailed successfully; and
the Claude narrative path — skipped on the all-clear run by design — was
exercised separately against a perturbed copy, producing correctly grounded
prose that ranked the pair imbalance above the share drop and restated the
own-history caveat the grounded block instructs it to respect. Only the
`STRING_RECIPIENTS` override is still unexercised (the run used the default
`REPORT_RECIPIENTS`).

- **Calibration came first, and it changed the design.** Nothing was written
  until every threshold had a measured number behind it:
  `docs/superpowers/plans/2026-08-02-growatt-string-c2-calibration.md`. The
  method that matters is that thresholds are set from a **trailing-window
  replay** — each day judged against the median of the days before it, counting
  how many findings would have fired on demonstrably healthy data — not from
  static full-window envelopes, which are a different and much looser number.
- **Rule 6, the flagship, is now proven un-implementable as an absolute
  threshold.** On 102 healthy pair-days: `>10%` flags **100% of them**, `>15%`
  flags 83%, `>20%` flags 17%, and the only clean cutoff (`>25%`) sits 0.5pp
  above pair 3+4's permanent 24.09–24.54%. Against each pair's **own** trailing
  median the worst healthy deviation is **1.02pp**, so the rule ships at 3.0pp.
  Pairs are recovered by grouping strings on `channel_samples.voltage_v` — one
  perfectly stable partition across all 18 days.
- **`pv_iso_kohm` is dropped, and not because it is noisy.** The portal reports
  **one value per day** — every day has exactly 1 distinct value among
  `status='1'` samples, 66 distinct values in the whole 5,045-sample series.
  That is why the C1 review's suggested hourly bucketing came out byte-identical
  for every hour: there is no intraday variation to bucket. It swings 131 →
  7,831 kΩ on consecutive healthy days, and healthy days read as low as 131 kΩ,
  so no floor works either. Rule 3 (the inverter's own flags) covers this ground
  authoritatively and needs no threshold at all.
- **`status='1'` is the correct daylight filter** — it excludes the 65,530
  sentinel perfectly (0 of 3,032 producing samples carry it), better than both
  `pac > 0` and the `< 60000` value hack.
- **The short-day filter incidentally closed the partiality gap.** Paging is
  newest-first, so a day truncated by a pages-1–3 failure keeps only its last 80
  samples and its `producing_minutes` necessarily collapses far below the
  measured 600-minute cutoff (full days read 760–835, the partial commissioning
  day 385–430). Truncated days are therefore excluded from every history
  comparison automatically, and the analyzer emits an explicit `day incomplete`
  finding rather than a false all-clear. **`pages_ok` is no longer a C2
  blocker** — still worth adding, but for diagnosis, not correctness.
- **Rule 9's temperature rule was reshaped.** The spec's "outlier *across*
  `temp_c..temp5_c`" is wrong by construction — five sensors at five different
  operating points (daily maxima near 50 / 56 / 61 / 43 °C), so the coolest is
  flagged forever. Each column is compared to its own history instead, and
  `temp4_c` is excluded outright (constant 0.0, sensor not fitted).
- **Validated against the real database, both directions.** Replaying all 18
  real days produced **zero findings on the 17 usable ones** plus the expected
  incomplete-day notice on 07-10; eight perturbed copies of that same real data
  each fired the right rule and only that rule. The rule-8 case is the
  interesting one — halving PV4's power for three morning hours moves the daily
  total too little for rules 4/5 to see, and the intraday rule caught it and
  named the window (`08:00-11:00`).
- **One threshold was tightened during validation.** Rule 5 started at −2.5% on
  3-of-5 days and was close to a duplicate of rule 4 rather than a more
  sensitive companion: a sustained −3% loss failed to fire because one of three
  days landed 0.0003 above the cut. It ships at −2%, still more than double the
  worst healthy single-day deviation (−0.96%), with three-of-five persistence on
  top.
- **Shipped:** `strings/analyze.py` (pure, 8 rules), `strings/report.py`
  (grounded block → Claude narrative → markdown → email HTML,
  `STRING_RECIPIENTS` override), three SQL-side derived-metric loaders in
  `strings/store.py`, and `strings/cli.py` wired with `--no-email` and a
  testable `compose_report`/`analyze_device` seam. **No schema change** — the
  derived metrics are recomputed from `channel_samples`, which keeps the
  analyzer pure and needs no migration.
- **One real bug found by the tests:** `SUM(flag <> '0')` returns NULL, not 0,
  when a flag column is NULL for every sample of a day (the mapper stores NULL
  for a field the payload omitted). Absence of a reading would have surfaced as
  an unreadable finding. Fixed with `COALESCE` in the loader.

## Done — earlier session (2026-07-26 → 2026-07-28)
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
- [ ] **Deploy is now the only thing standing between C2 and production.** The
  whole-branch review ran on 2026-08-02 and its fixes are folded in (see "Done
  this session"); the suite is **582** and green.
- [ ] **Recalibrate periodically — this is not a someday item, it moved twice in
  one day.** Five extra days pushed rule 6's worst healthy deviation 1.02 →
  **1.45pp** (headroom 2.9× → 2.1×) and rule 7's −0.29% → **−0.46%** (10.2× →
  6.5×). Rule 6 was widened 3.0 → **4.0pp** in response. The max of a sample
  keeps growing, so any threshold set from "the worst a healthy plant has done
  so far" is a moving target. Run
  `python -m solaranalysis.tools.string_calibration --verify` against the
  server's `app.db` every month or two; it flags any rule below 2× headroom.
  **Rule 7 stays the weakest** — it needs 14 days, so its figure still comes
  from a handful of overlapping windows.
- [ ] **Still zero positive fault examples.** All 8 fault/status columns read
  `'0'` across 18 days and 5,045 samples. Every threshold bounds the
  **false-positive** rate and says nothing about the true-positive rate; the
  perturbation tests are synthetic, not observed faults. Treat the first real
  finding as a calibration event — check it physically before trusting the rule
  that raised it.
- [ ] **The retention prune must not silently break rules 6 and 8.** They
  recompute from `channel_samples` at analyze time (deliberately — no schema
  change), so a prune window shorter than `ANALYSIS_WINDOW_DAYS` (30) would make
  them quietly stop firing rather than error. Either keep ≥30 days of samples,
  or materialize the two derived metrics into small per-day tables as part of
  the prune work. See the retention item below.
- [ ] **Partiality marker — downgraded to nice-to-have by C2.** A day truncated
  by a pages-1–3 failure is still stored with no marker, but it is **no longer a
  correctness risk**: paging is newest-first, so such a day keeps only its last
  80 samples, its `producing_minutes` collapses far below the analyzer's
  measured 600-minute cutoff, and it is excluded from every history comparison
  automatically (and reported as `day incomplete`). What the column would still
  buy is *diagnosis* — distinguishing "the collector lost pages" from "short
  winter day", which the minutes alone cannot. Fix remains one additive nullable
  column on `channel_day_energy` (`pages_ok`), written from the value
  `collector.collect_day` already computes.
- [ ] **`parse_devices` ignores `deviceTypeName`** — `getMAXHistory` is
  MAX-specific, so adding a MIN or storage device to the plant would POST its
  serial to a MAX endpoint. One-line guard.
- [ ] **Retention prune for `channel_samples` / `inverter_samples`** — joins
  the existing `optimizer_energy` / `raw_payloads` prune backlog item. Now
  quantified: the string tables grow ~**0.73 MB/day** (~260 MB/year) in the same
  `app.db` as the web app's data, so the DB will dominate backups within a year.
  **C2 constrains this**: rules 6 and 8 recompute from `channel_samples` over a
  30-day window, so the prune must keep ≥30 days or materialize those metrics
  first — see the C2 item above.
- [ ] **Exit-4 path has never fired against the real portal** — it is unit-tested
  via the pure `cli.exit_code` helper, but a live portal failure has not been
  observed end to end. Expect it to surface naturally once the scheduled task runs.
- [ ] **First-time deploy to llmadmin per `DEPLOYMENT.md`** (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS [+ optional OPTIMIZER_RECIPIENTS and STRING_RECIPIENTS]) and `config.yaml` by hand (both gitignored). **Push `master` first** — see Deploy status. **Run on the server.**
  - `DEPLOYMENT.md` step 3 now covers `playwright install chromium` with a machine-scope `PLAYWRIGHT_BROWSERS_PATH` — this was **missing** before and would have failed the first portal login under `LocalSystem`.
- [ ] **Optimizer live smoke run (was B1 Task 8 + B2 Task 7)** — needs real SolarEdge creds; now written up as `DEPLOYMENT.md` §11 (dry run → `--backfill 90` → scheduled task). Confirm the 4 sites populate `optimizers`/`optimizer_energy` AND an anomaly email arrives; spot-check one flagged optimizer against the Digital-Twin panel.
- [ ] **Schedules (ops, on the server):** web UI daily fleet run ~06:00 (Settings/Schedules, all days, range `snapshot`); optimizer collector+report ~06:30 (§11); Growatt string collector ~06:45 (§12). All three documented in `DEPLOYMENT.md`, none yet *created* on the server.
- [ ] Backlog (non-blocking): populate optimizer module tilt/azimuth/make/model (per-optimizer info call — not in the layout tree); strengthen analyze sort-ordering + degradation-guard tests; Phase A `base.py._finish_raw` import placement; `db.connect` sets no `busy_timeout` so web-app contention surfaces at sqlite3's 5 s default.

## Environment note
**Run everything through `.venv\Scripts\python.exe`, not the bare `python` on PATH.** They are different environments and the difference is not cosmetic: the system Python has `anthropic` **0.75.0**, which rejects the `output_config={"effort": ...}` argument every Claude call in this repo passes (`core/analyze.py`, `core/charts.py`, `core/dashboard.py`, `optimizers/report.py`, `strings/report.py`). The `.venv` has **0.116.0** and works. Because every narrative call site catches its own exception and degrades to tables-only, running under the wrong interpreter does not fail loudly — it silently drops the narrative and looks like a working run. Cost an incorrect "this is broken in B2 as well" conclusion during the C2 smoke test.

The Git-Bash tool's PATH resolved bare `git`/`bash` fine through 2026-08-02. If the earlier breakage recurs, prefix with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"` or just use PowerShell for git.

## Recalibrating C2
`python -m solaranalysis.tools.string_calibration --verify` re-measures every threshold against whatever is stored and prints each rule's worst healthy deviation, its shipped constant, the headroom between them (flagging any that has eroded below 2×), and the false-positive curve for candidate thresholds — plus, with `--verify`, a replay of the shipped analyzer over every stored day. It reproduces the numbers in `docs/superpowers/plans/2026-08-02-growatt-string-c2-calibration.md` exactly. Run it against the server's `app.db` once a few months of history exist.
