# NextTODO — solar-analysis

**Last updated:** 2026-07-26

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- `master` is in sync with `origin/master` — the earlier "22 commits unpushed" warning is resolved; the deploy can clone/pull straight away.
- The deploy is **hands-on-server work**: llmadmin answers on SMB/WinRM but this dev machine has no usable remote-exec path (WinRM needs a TrustedHosts change + elevation), so every `DEPLOYMENT.md` command has to be run in PowerShell on the server itself.

## Done — Phase A: daily raw-snapshot persistence (MERGED)
Every web-app run persists the **untouched portal JSON** (new **`raw_payloads`** table, schema →v5, zlib, opt-in `record_raw`, web-runner only, excluded from the LLM); SolarEdge emits **`energyYesterday`** as a daily `energy_points` row. Subagent-driven TDD, whole-branch review "Yes", 368 tests. Spec `specs/2026-07-23-daily-raw-persistence-design.md`.

## Done — Phase B1: SolarEdge per-optimizer collector (MERGED)
Standalone `solaranalysis/optimizers/`: `mappers`/`layout_client`/`store`/`collector`/`cli`. Pulls every optimizer's daily energy + inventory across all sites via `/services/layout/*` into `app.db` (new `optimizers` + `optimizer_energy` tables, schema →v6), with `--backfill N` and per-site DB-level failure isolation. 390 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-collector.md`.

## Done — Phase B2: optimizer anomaly analysis + report + email (MERGED)
On top of B1's stored series: `optimizers/analyze.py` (pure `analyze_site` — dead / underperforming / degrading / watch, via the peer-normalized `color` + string-median + multi-day persistence + a recent-vs-prior degradation trend) and `optimizers/report.py` (grounded data block → Claude narrative → markdown → email-safe HTML via `core.report.render_email_html`). The `optimizers` CLI now analyzes + emails after collecting (`--no-email`; skips the Opus call on all-clear days; `OPTIMIZER_RECIPIENTS` override wired through a new `mailer.send_report(to=…)`). Subagent-driven TDD, whole-branch review "Ready to merge: Yes", 410 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-analysis-report.md`.

## Done — report polish (MERGED, 2026-07-26)
Two backlog items, TDD, 417 tests: friendly **site names** now flow into the optimizer report (`collector.parse_site_names` off the same sitelist payload → the grounded block and the markdown, so the email says "Baram (site 2387929)" not "site 2387929"), and **fully-silent optimizers are flagged dead** — a missing row is not a zero row, so an optimizer that drops out of the payload used to be invisible to every rule; it now reads "stopped reporting for N days". Extracted `cli.compose_report` as a testable seam, which also put the all-clear narrative skip and the narrator-failure fallback under test.

## Next
- [ ] **First-time deploy to llmadmin per `DEPLOYMENT.md`** (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS [+ optional OPTIMIZER_RECIPIENTS]) and `config.yaml` by hand (both gitignored). **Run on the server** — see Deploy status above.
  - `DEPLOYMENT.md` step 3 now covers `playwright install chromium` with a machine-scope `PLAYWRIGHT_BROWSERS_PATH` — this was **missing** before and would have failed the first portal login under `LocalSystem`.
- [ ] **Optimizer live smoke run (was B1 Task 8 + B2 Task 7)** — needs real SolarEdge creds; now written up as `DEPLOYMENT.md` §11 (dry run → `--backfill 90` → scheduled task). Confirm the 4 sites populate `optimizers`/`optimizer_energy` AND an anomaly email arrives; spot-check one flagged optimizer against the Digital-Twin panel.
- [ ] **Schedules (ops, on the server):** web UI daily fleet run ~06:00 (Settings/Schedules, all days, range `snapshot`); optimizer collector+report ~06:30 — both now documented in `DEPLOYMENT.md` (§10 and §11), still need to be *created* on the server.
- [ ] Backlog (non-blocking): populate optimizer module tilt/azimuth/make/model (per-optimizer info call — not in the layout tree); `optimizer_energy`/`raw_payloads` retention prune; strengthen analyze sort-ordering + degradation-guard tests; Phase A `base.py._finish_raw` import placement.

## Environment note
Last session the Git-Bash tool's PATH stopped resolving bare `git`/`bash` mid-session; it resolves fine again as of 2026-07-26. If it recurs, prefix with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"` or just use PowerShell for git.
