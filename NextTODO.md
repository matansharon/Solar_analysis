# NextTODO — solar-analysis

**Last updated:** 2026-07-26

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- `master` is **20 commits ahead of `origin/master`** (Phase C1 merged locally, not pushed). The server pulls from `origin/master`, so **push before deploying** or it will not see the string collector.
- The deploy is **hands-on-server work**: llmadmin answers on SMB/WinRM but this dev machine has no usable remote-exec path (WinRM needs a TrustedHosts change + elevation; RPC to the service manager is denied). Every `DEPLOYMENT.md` command has to be run in PowerShell on the server itself — including `nssm status SolarAnalysis`, so "not deployed" is an assumption that has never been verified from here.

## Done this session (2026-07-26)
- **Phase C1 live backfill + baseline (final C1 task)**: ran
  `python -m solaranalysis.strings --data-dir data --app-dir . --backfill 90`
  against the real Growatt portal for the first time. The live `app.db`
  additively migrated 6 → **schema v7** (the four new tables:
  `inverter_channels`, `channel_day_energy`, `channel_samples`,
  `inverter_samples`). Result: **16 real days** (2026-07-10 → 2026-07-25, one
  fewer than the ~17 the earlier discovery spike anticipated — see the
  baseline doc) plus 74 correct `no data` pre-install days, no `ERROR`s; 18
  channels (6 MPPT + 12 string) confirmed; 80,496 `channel_samples` rows;
  4,472 `inverter_samples` rows; a single all-`"0"` fault-flag combination
  (no faults observed in the window). Measured statistics — per-input energy
  range, share_of_total median/min/max, widest single-day share swing
  (PV6, 0.0031, the noise floor), and the `pv_iso_kohm`/`temp3_c` spread —
  are written up in
  `docs/superpowers/plans/2026-07-26-growatt-string-baseline.md` for Phase C2
  to calibrate thresholds against.
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
    full-window max−min). A "share collapse" rule must trigger well outside it.
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
- [ ] **`DEPLOYMENT.md` §12** — the daily Growatt string collector Scheduled
  Task. Set **`PYTHONIOENCODING=utf-8`** on the task (the CLI prints an em dash,
  and C2 adds Hebrew output). Note the CLI's exit codes: **2** no enabled Growatt
  plant, **3** empty plant/device list, **4** one or more days failed, 0 clean.
  `db.connect` sets no `busy_timeout`, so contention with the web app surfaces at
  sqlite3's 5 s default as per-day `database is locked` errors — isolated per day,
  but they now correctly produce exit 4.
- [ ] **Retention prune for `channel_samples` / `inverter_samples`** — joins
  the existing `optimizer_energy` / `raw_payloads` prune backlog item.
- [ ] **First-time deploy to llmadmin per `DEPLOYMENT.md`** (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS [+ optional OPTIMIZER_RECIPIENTS]) and `config.yaml` by hand (both gitignored). **Run on the server** — see Deploy status above.
  - `DEPLOYMENT.md` step 3 now covers `playwright install chromium` with a machine-scope `PLAYWRIGHT_BROWSERS_PATH` — this was **missing** before and would have failed the first portal login under `LocalSystem`.
- [ ] **Optimizer live smoke run (was B1 Task 8 + B2 Task 7)** — needs real SolarEdge creds; now written up as `DEPLOYMENT.md` §11 (dry run → `--backfill 90` → scheduled task). Confirm the 4 sites populate `optimizers`/`optimizer_energy` AND an anomaly email arrives; spot-check one flagged optimizer against the Digital-Twin panel.
- [ ] **Schedules (ops, on the server):** web UI daily fleet run ~06:00 (Settings/Schedules, all days, range `snapshot`); optimizer collector+report ~06:30 — both now documented in `DEPLOYMENT.md` (§10 and §11), still need to be *created* on the server.
- [ ] Backlog (non-blocking): populate optimizer module tilt/azimuth/make/model (per-optimizer info call — not in the layout tree); `optimizer_energy`/`raw_payloads` retention prune; strengthen analyze sort-ordering + degradation-guard tests; Phase A `base.py._finish_raw` import placement.

## Environment note
Last session the Git-Bash tool's PATH stopped resolving bare `git`/`bash` mid-session; it resolves fine again as of 2026-07-26. If it recurs, prefix with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"` or just use PowerShell for git.
