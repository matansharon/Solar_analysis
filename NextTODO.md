# NextTODO — solar-analysis

**Last updated:** 2026-07-24

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- ⚠️ `master` is **ahead of origin/master by 10 commits and NOT pushed** (this session: design specs + Phase A plan + Phase A implementation). The deploy clones/pulls from origin, so `git push origin master` MUST happen before the first-time deploy.

## Done this session — Phase A: daily raw-snapshot persistence (MERGED to master)
Built via brainstorm → 2 specs → plan → subagent-driven TDD (7 tasks) → per-task reviews → whole-branch opus review ("Ready to merge: Yes", no Critical/Important) → 1 post-review fix (per-site raw routing) → re-review clean → fast-forward merge, branch deleted. Suite: **368 tests green.**
- Every web-app run now persists the **untouched portal JSON** for all systems into a new **`raw_payloads`** table (schema v3→**v5**, additive DDL, auto-migrates on startup), zlib-compressed. Capture is opt-in via `record_raw` (base-adapter `_begin_raw`/`_finish_raw` + `BrowserSession.start_raw_capture`), enabled only by the web runner — CLI/existing behavior byte-for-byte unchanged. Raw is excluded from `to_dict` so it never reaches the LLM.
- Multi-site accounts (SolarEdge×4): each raw payload is routed to its own site by matching the site id in the URL; account/fleet-level payloads fall to the first site.
- SolarEdge now emits **`energyYesterday`** as a day-granularity `energy_points` row, so a clean per-site daily-kWh series accumulates from snapshot runs.
- Specs: `specs/2026-07-23-daily-raw-persistence-design.md` (Phase A) + `specs/2026-07-23-optimizer-collector-design.md` (Phase B). Plan: `docs/superpowers/plans/2026-07-23-daily-raw-persistence.md`.
- Endpoint discovery for Phase B captured live and preserved (git-ignored) under `.discovery/solaredge/optimizer-spike/`.

## Next
- [ ] **Push master to origin** (`git push origin master`) — 10 commits local-only; prerequisite for deploy.
- [ ] **Phase A ops step (Task 8):** on the deployed server, create the daily schedule in the UI (Settings/Schedules: ~06:00, all days, range `snapshot`). Now code-supported — it will accumulate a raw snapshot + a yesterday energy point per run. Then confirm the next day's run persisted new `raw_payloads` + `energy_points` day rows.
- [ ] **Phase B — per-optimizer collector (spec ready, plan NOT yet written).** Standalone `solaranalysis/optimizers/` module: pull every optimizer's daily energy across the 4 SolarEdge sites via `/services/layout/*` (see spec §2 for the exact endpoints + fixtures), store inventory + daily energy in `app.db`, detect underperformers (the `color` field is SolarEdge's own peer-normalized signal) + degradation, email a separate daily anomaly report; 90-day backfill on first run. Next action: run `superpowers:writing-plans` against `specs/2026-07-23-optimizer-collector-design.md`.
- [ ] First-time deploy to llmadmin per `DEPLOYMENT.md` (confirm `SolarAnalysis` + port 8010 free); copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS) and `config.yaml` by hand (both gitignored).
- [ ] Backlog (optional polish):
  - Phase A deferred Minors (non-blocking, simplify-pass candidates): move `import re`/`RawPayload`/`raw_label` in `base.py._finish_raw` to module top (no real circular import); `raw_payloads` retention prune (delete rows older than N days); a web-UI to browse raw payloads.
  - Growatt token-mode persists no raw (browser-only design) — note if a Growatt account is ever switched to `mode: token`.
