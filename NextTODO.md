# NextTODO — solar-analysis

**Last updated:** 2026-07-16

## Deploy status
- Target: windows — llmadmin (192.168.30.84), NSSM service `SolarAnalysis`, port 8010 *(proposed — confirm)*
- State: NOT deployed (pending) · plan: DEPLOYMENT.md (first-time path)

## Done this session
- Code review of the emailed dashboard HTML → 4 improvement steps, each shipped
  with tests and verified by a real end-to-end run + email example (4 emails sent,
  subject stamps 080709 / 081338 / 082043 / 082731 UTC):
  1. **Summary readability** — bullet-list normalization in `summarize_executive`,
     LRM bidi marks + fewer vendor IDs via `exec_summary.txt` (9f45f53)
  2. **Fluid width** — shell 100%/max-640px rule, wrappable chart labels (66c485f)
  3. **Chart quality** — Hebrew RTL charts, bars sorted leader-first, fixed aligned
     value column, uniform decimals per chart (d87972f)
  4. **Shell polish** — `{{DATE}}` token, hidden inbox preheader, fixed footer
     wording (no invented boilerplate), label width hint (0a64482)
- Suite: 322 tests green. All 4 commits pushed to origin/master.
- `.deploy.yml` + `DEPLOYMENT.md` created (windows/llmadmin target chosen).
- Note: the dev web server on :8000 was stopped; `run_dev.bat` relaunches it.

## Next
- [ ] First-time deploy to llmadmin per DEPLOYMENT.md (confirm service name
      `SolarAnalysis` + port 8010 are acceptable/free first)
- [ ] Copy `.env` (Anthropic + Graph creds) and `config.yaml` to the server by
      hand — both are gitignored
- [ ] After deploy: manual snapshot run on the server → confirm dashboard email
      arrives; then create the daily schedule in the UI
- [ ] Backlog (optional polish): pin a fixed dashboard shell for run-to-run visual
      consistency; KPI big-number strip ({{KPIS}} fragment); further bidi tightening
      in the summary lead paragraph
