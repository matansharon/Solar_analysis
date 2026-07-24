# NextTODO — solar-analysis

**Last updated:** 2026-07-24

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- ⚠️ `master` is **ahead of origin/master by 22 commits and NOT pushed** (design specs + Phase A + Phase B1 + Phase B2). The deploy clones/pulls from origin, so `git push origin master` MUST happen before the first-time deploy.

## Done — Phase A: daily raw-snapshot persistence (MERGED)
Every web-app run persists the **untouched portal JSON** (new **`raw_payloads`** table, schema →v5, zlib, opt-in `record_raw`, web-runner only, excluded from the LLM); SolarEdge emits **`energyYesterday`** as a daily `energy_points` row. Subagent-driven TDD, whole-branch review "Yes", 368 tests. Spec `specs/2026-07-23-daily-raw-persistence-design.md`.

## Done — Phase B1: SolarEdge per-optimizer collector (MERGED)
Standalone `solaranalysis/optimizers/`: `mappers`/`layout_client`/`store`/`collector`/`cli`. Pulls every optimizer's daily energy + inventory across all sites via `/services/layout/*` into `app.db` (new `optimizers` + `optimizer_energy` tables, schema →v6), with `--backfill N` and per-site DB-level failure isolation. 390 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-collector.md`.

## Done — Phase B2: optimizer anomaly analysis + report + email (MERGED)
On top of B1's stored series: `optimizers/analyze.py` (pure `analyze_site` — dead / underperforming / degrading / watch, via the peer-normalized `color` + string-median + multi-day persistence + a recent-vs-prior degradation trend) and `optimizers/report.py` (grounded data block → Claude narrative → markdown → email-safe HTML via `core.report.render_email_html`). The `optimizers` CLI now analyzes + emails after collecting (`--no-email`; skips the Opus call on all-clear days; `OPTIMIZER_RECIPIENTS` override wired through a new `mailer.send_report(to=…)`). Subagent-driven TDD, whole-branch review "Ready to merge: Yes", 410 tests. Plan `docs/superpowers/plans/2026-07-24-optimizer-analysis-report.md`.

## Next
- [ ] **Push master to origin** (`git push origin master`) — 22 commits local-only; prerequisite for deploy.
- [ ] **Optimizer live smoke run (was B1 Task 8 + B2 Task 7)** — needs real SolarEdge creds. On the deployed machine: `python -m solaranalysis.optimizers --data-dir ./data --app-dir . --date <yesterday>` → confirm the 4 sites populate `optimizers`/`optimizer_energy` AND an anomaly email arrives (spot-check one flagged optimizer against the Digital-Twin panel). Probe a past date, then `--backfill 90` for history. Use `--no-email` for a dry run.
- [ ] **Schedules (ops, on the server):**
  - Web UI daily fleet run: Settings/Schedules ~06:00, all days, range `snapshot` (accumulates raw + daily-energy history).
  - Optimizer collector+report: a separate daily task (NSSM/Task Scheduler) `python -m solaranalysis.optimizers …` ~06:30. Document in `DEPLOYMENT.md`.
- [ ] First-time deploy to llmadmin per `DEPLOYMENT.md` (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS [+ optional OPTIMIZER_RECIPIENTS]) and `config.yaml` by hand (both gitignored).
- [ ] Backlog (non-blocking): wire friendly `site_names` into the optimizer report (vs "site {id}"); flag fully-silent (missing-row) optimizers as dead ("stopped reporting N days"); populate optimizer module tilt/azimuth/make/model (per-optimizer info call — not in the layout tree); `optimizer_energy`/`raw_payloads` retention prune; strengthen analyze sort-ordering + degradation-guard tests; Phase A `base.py._finish_raw` import placement.

## Environment note (transient, this session)
The Git-Bash tool's PATH stopped resolving bare `git`/`bash` mid-session (full paths work). Used PowerShell for git + `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"` prefix for the sdd bash scripts. If it recurs, use that prefix.
