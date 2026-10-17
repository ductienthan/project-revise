# Preflight build, phases 1-3

- [x] Read the Principles section of the poteto-mode skill
- [x] Phase A: Frame (predicate, scope, rigor) and checkpoint with the operator
- [x] Phase B: Design the workflow (phase list below)
- [ ] Phase C: Run the loop
  - [ ] P0 harness: git, app factory with injectable DB, test fixtures, scripts/e2e.py skeleton
  - [ ] P1 foundation: all tables + migration, dashboard login (argon2, sessions, CSRF, lockout, TOTP), device flow + tokens, API auth, audit log
  - [ ] P1 verify: auth tests + e2e login slice (password fallback and device flow)
  - [ ] P2 capture: projects, requirement versions, failures, wins, search, offline queue with idempotent replay
  - [ ] P2 verify: tests + e2e capture slice
  - [ ] P3 checklist: patterns, rules, rule-based checklist, priority/required, strict gate + override, failure linking
  - [ ] P3 verify: tests + e2e checklist slice (requirements section 19 flow without AI)
  - [ ] Independent review of auth and gate (subagent)
- [ ] Phase D: Keep the audit trail (.audit/preflight-build.tsv, a row per landed step)
- [ ] Phase E: Verify the whole against the predicate and hand back
