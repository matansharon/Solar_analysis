# NextTODO — solar-analysis

**Last updated:** 2026-07-23

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(both proposed in `.deploy.yml` — confirm free on the server before first deploy)*
- State: NOT deployed (pending) · plan: `DEPLOYMENT.md` (first-time path, FastAPI/uvicorn — **not** Waitress)
- ⚠️ `master` is **ahead of origin/master by 9 commits and NOT pushed**. The deploy
  clones/pulls from origin, so `git push origin master` MUST happen before the
  first-time deploy or the server won't get this session's work.

## Done this session
- **Per-system analysis run feature** — a run can now target one system instead of
  only the whole fleet. Built via brainstorm → spec → plan → subagent-driven TDD
  (6 tasks) → whole-branch opus review ("Ready to merge: Yes", no Critical/Important)
  → 1 post-review Minor fix. Merged to master (fast-forward), branch deleted.
  Suite: 348 tests green.
  - A run carries an optional `runs.plant_id` (NULL = today's fleet run of all
    enabled systems; a value = just that system). Schema v3→v4 via guarded ALTER —
    **runs automatically on startup**, so an update-deploy needs no manual DB step.
  - Runs page: a "System" picker (default "All enabled systems") beside the
    time-range selector; a "System" column in run history (labels a now-disabled
    target by name too). `POST /api/runs` validates the target (unknown/disabled → 422).
  - The single-system run reuses the whole existing pipeline (fetch → analyze →
    status/summary → dashboard → **email**) with N=1; scheduled runs stay fleet-wide.
  - Fleet path is byte-for-byte unchanged (verified in review).
  - Spec: `specs/2026-07-23-per-system-run-design.md`; plan:
    `docs/superpowers/plans/2026-07-23-per-system-run.md`.

## Next
- [ ] **Push master to origin** (`git push origin master`) — prerequisite for deploy;
      9 commits currently local-only (per-system spec+plan+feature, plus the prior
      session's handoff commit).
- [ ] First-time deploy to llmadmin per `DEPLOYMENT.md` (confirm service name
      `SolarAnalysis` + port 8010 are free on the server first).
- [ ] Copy `.env` (ANTHROPIC_API_KEY + GRAPH_* + REPORT_RECIPIENTS) and `config.yaml`
      to the server by hand — both are gitignored.
- [ ] After deploy: a manual snapshot run on the server → confirm the dashboard email
      arrives; try a single-system run from the Runs picker; then create the daily
      schedule in the UI.
- [ ] Backlog (optional polish):
  - Deferred Minors from the per-system feature (non-blocking): unify the
    "unresolvable target" label between backend (`system {id}`) and frontend (`#{id}`);
    add a unit test for the runner's `names.get(plant_id, …)` fallback; the Runs
    picker can show a blank value if the selected system is disabled mid-session
    (submitting just 422s — no bad run created).
  - Deferred Minors from the status-overview feature: dedup `_status_html`/`_summary_html`
    into one `_render_rtl_fragment` helper; move mid-file test imports to the top block.
  - Pin a fixed dashboard shell for run-to-run visual consistency; KPI big-number strip.
