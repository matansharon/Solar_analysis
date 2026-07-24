# NextTODO — solar-analysis

**Last updated:** 2026-07-24

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- ⚠️ `master` is **ahead of origin/master by 11 commits and NOT pushed** (design specs + Phase A + Phase B1). The deploy clones/pulls from origin, so `git push origin master` MUST happen before the first-time deploy.

## Done — Phase A: daily raw-snapshot persistence (MERGED to master)
Brainstorm → specs → plan → subagent-driven TDD (7 tasks) → reviews → whole-branch opus review ("Ready to merge: Yes") → 1 post-review fix → merged. **368 tests.**
- Every web-app run persists the **untouched portal JSON** for all systems into a new **`raw_payloads`** table (schema →v5, additive, auto-migrates), zlib-compressed; opt-in via `record_raw`, enabled only by the web runner (CLI unchanged). Raw excluded from `to_dict` (never hits the LLM). Multi-site SolarEdge payloads routed per-site by URL.
- SolarEdge emits **`energyYesterday`** as a day-granularity `energy_points` row → clean per-site daily-kWh series from snapshot runs.
- Spec: `specs/2026-07-23-daily-raw-persistence-design.md`; plan: `docs/superpowers/plans/2026-07-23-daily-raw-persistence.md`.

## Done — Phase B1: SolarEdge per-optimizer collector (MERGED to master)
Standalone collector built via plan → subagent-driven TDD (7 tasks) → reviews → whole-branch opus review ("Ready to merge: Yes", all findings Minor) → 2 post-review fixes (collector `conn.rollback()` on per-site failure; CLI empty-site guard) → merged. **390 tests.**
- New package `solaranalysis/optimizers/`: `mappers` (logical tree → inventory; by-inverter → per-optimizer energy), `layout_client` (`/services/layout/*` GETs), `store` (upserts), `collector` (per-site, backfill, DB-level failure isolation), `cli` (`python -m solaranalysis.optimizers --data-dir … --app-dir … [--date] [--backfill N] [--sites]`).
- New tables `optimizers` + `optimizer_energy` in `app.db` (schema →**v6**, additive). Reuses the SolarEdge adapter login/session + app.db creds.
- Spec: `specs/2026-07-23-optimizer-collector-design.md` (B1 = collect+store portion); plan: `docs/superpowers/plans/2026-07-24-optimizer-collector.md`. Discovery fixtures (git-ignored): `.discovery/solaredge/optimizer-spike/`.
- ⚠️ **Task 8 (live smoke run + past-date backfill probe) NOT run** — needs real SolarEdge creds. Validate before trusting collected data: `python -m solaranalysis.optimizers --data-dir ./data --app-dir . --date <yesterday>` then probe a past date, then `--backfill 90`.

## Next
- [ ] **Push master to origin** (`git push origin master`) — 11 commits local-only; prerequisite for deploy.
- [ ] **Phase B1 live smoke run (Task 8)** — run the collector against the real account (see ⚠️ above); confirm the 4 sites populate `optimizers`/`optimizer_energy` and that a past date returns rows (backfill viability).
- [ ] **Phase B2 — optimizer analysis + report + email + schedule (spec ready, plan NOT written).** On top of B1's stored data: an `analyze` module (underperformers via the `color` peer-normalized signal + string-median + persistence; degradation trend), a grounded daily anomaly report, a separate daily email (reuse `web/mailer`), and its own scheduled task. Next action: `superpowers:writing-plans` against `specs/2026-07-23-optimizer-collector-design.md` (§5–§7).
- [ ] **Phase A ops step:** on the deployed server, create the daily schedule in the UI (Settings/Schedules: ~06:00, all days, range `snapshot`) so raw + daily-energy history accumulates. Confirm the next day's run persisted new rows.
- [ ] First-time deploy to llmadmin per `DEPLOYMENT.md` (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS) and `config.yaml` by hand (both gitignored).
- [ ] Backlog (non-blocking): populate optimizer module tilt/azimuth/manufacturer/model (a per-optimizer info call, not in the layout tree — nullable today); `optimizer_energy`/`raw_payloads` retention prune; strengthen the store upsert test to mutate a field; Phase A `base.py._finish_raw` import placement.

## Environment note (transient, this session)
The Git-Bash tool's PATH stopped resolving bare `git`/`bash` mid-session (full paths work). Used PowerShell for git + `bash -lc` / `export PATH=…` prefix for scripts. If it recurs, prefix Bash commands with `export PATH="/c/Program Files/Git/cmd:/c/Program Files/Git/bin:/usr/bin:$PATH"`.
