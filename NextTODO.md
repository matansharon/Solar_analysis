# NextTODO — solar-analysis

**Last updated:** 2026-07-21

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(proposed — confirm)*
- State: NOT deployed (pending) · plan: DEPLOYMENT.md (first-time path)
- ⚠️ `master` is **9 commits ahead of origin/master and NOT pushed**. The deploy
  clones/pulls from origin, so `git push origin master` MUST happen before the
  first-time deploy or the server won't get this session's work.

## Done this session
- **System-status overview feature** — every report now opens with a plain-language
  per-system verdict answering "is each system working correctly?": a Hebrew fleet
  headline + traffic-light list (✅ תקין / ⚠️ דורשת תשומת לב / ❌ תקלה) with a short
  reason per system. Built via brainstorm → spec → plan → subagent-driven TDD
  (6 tasks + 1 hardening test), full opus whole-branch review ("Ready to merge: Yes",
  no Critical/Important). Merged to master (fast-forward), branch deleted.
  - `status_overview` (Opus 4.8 xhigh) judges each system from the report's facts,
    grounding untouched (2ace868)
  - `prepend_status` puts it atop report.html; order status → summary → report (b211357)
  - dashboard renders status above summary + status headline as inbox preheader (5f589b3)
  - wired non-fatally into web/runner.py (cf3b93c) and cli.py (4599e13)
  - README documented (76e654f); base_md-invariant hardening test (2f15bdb)
  - Unavailable/unfetched systems show as ❌ "לא ניתן לאחזר נתונים".
- Suite: 336 tests green on merged master. Spec: specs/2026-07-20-system-status-overview-design.md.

## Next
- [ ] **Push master to origin** (`git push origin master`) — prerequisite for deploy;
      9 commits currently local-only.
- [ ] First-time deploy to llmadmin per DEPLOYMENT.md (confirm service name
      `SolarAnalysis` + port 8010 are acceptable/free first)
- [ ] Copy `.env` (Anthropic + Graph creds) and `config.yaml` to the server by
      hand — both are gitignored
- [ ] After deploy: manual snapshot run on the server → confirm the dashboard email
      arrives and now opens with the system-status block; then create the daily schedule in the UI
- [ ] Backlog (optional polish):
  - Deferred minors from the status feature: dedup `_status_html`/`_summary_html`
    into one `_render_rtl_fragment` helper; move mid-file test imports to the top block.
  - In the model-designed email shell the status can render under the summary's
    heading — teach prompts/dashboard.txt about a distinct status region if it reads oddly in real inboxes.
  - Pin a fixed dashboard shell for run-to-run visual consistency; KPI big-number
    strip ({{KPIS}} fragment); further bidi tightening in the summary lead paragraph.
