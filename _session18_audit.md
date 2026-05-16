# Session 18 Launch Audit

Generated 2026-05-16. Read-only Phase 1 evidence collection. Fixes deferred to Phase 2+.

## Finding: Session 17 close-out state intact

Symptom: n/a — verification step.
Root cause hypothesis: n/a.
Evidence:
- `tools/insights_engine/edit_with_haiku.py:20` reads `INSIGHTS_MODEL` from env, default `anthropic/claude-haiku-4.5`.
- `.github/workflows/insights_engine.yml:37-38` injects `ANTHROPIC_BASE_URL` and `INSIGHTS_MODEL` from secrets.
- `tools/insights_engine/generate_candidates.py:275` keeps `_tournament_narrative_candidates()` commented out; function body intact on line 205 (re-enablement scaffold).
- `data/insights/published.json` contains exactly two `surface_specialists` insights, no `tournament_narrative` entries.
- `gh secret list` shows all expected secrets: ADMIN_TOKEN, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, APPRUNNER_SERVICE_ARN, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, CLOUDFRONT_DIST_ID, INSIGHTS_MODEL, PAT_TOKEN, plus TOKENROUTER_API_KEY.
- Five commits since Session 17 close (`4660812c`): TokenRouter wiring (`c5fc2479`), tournament_narrative disable (`aaa123f1`), published.json purge (`fdf3f524`), and two cron publish commits (`bc2506d6`, `f3d39abb`).

Verified: yes.
Phase 2 priority: n/a (no remediation needed).

Note: secrets list includes both `ANTHROPIC_API_KEY` (used by the SDK) and a separate `TOKENROUTER_API_KEY`. Per onboarding, the live wiring is `ANTHROPIC_API_KEY = <TokenRouter key value>` with `ANTHROPIC_BASE_URL = api.tokenrouter.com`. `TOKENROUTER_API_KEY` is duplicate/legacy storage of the same value; not in active use by the insights workflow.

## Finding: No regressions on known-working endpoints

Symptom: n/a — verification step.
Evidence: All 15 endpoints in the Phase 1c list returned HTTP 200 after a 50-second `/ready` warm. See `_session18_artifacts/endpoint_audit.log`. Bodies inspected confirm shape, not correctness; the three reconciliation-target endpoints (`/api/live-tournament`, `/api/key-matchups-live`, `/api/tournament-predictions`) return well-formed JSON that contains the user-visible lies described in onboarding (Italian Open "Round 4th Round" / draw_size 96, Alcaraz in tournament-predictions favorites, key-matchups-live returning a long-past Rome final). These are content lies, not endpoint regressions.
Verified: yes.
Phase 2 priority: n/a.

## Finding: Compare Players renders, but only after ~63s spinner

Symptom: Compare Players dashboard shows "Analyzing matchup..." spinner for an extended interval after a player pair is selected; a normal user gives up before render.
Root cause hypothesis: Backend `/api/v2/player/{name}` and `/api/v2/matchup` return persistent HTTP 503 because the underlying data files (`data/processed/player_profiles.parquet`, `data/processed/parsed_points.parquet`) are gitignored and never bundled into the App Runner deploy. The 503 path in `src/api/pattern_endpoints.py:33` raises "Player profiles not available". Frontend `frontend/public/dashboard/warmup.js` (line 60) wraps `window.fetch` and retries 503s with delays `[1s, 2s, 4s, 8s, 16s, 32s]` = 63s total budget. `compare.html` `go()` awaits a `Promise.all` that includes `fetchLegacyMatchup()`, which calls those v2 endpoints, so the entire compare render is gated by the 63s retry budget. Once the budget exhausts, `legacyData = null` and the page falls through to the card-attribute branch (`else if (card1 && card2)`) and does render.
Evidence:
- Production curl: `GET /api/v2/player/Jannik%20Sinner` → 503 `{"detail":"Player profiles not available"}`. Same for `/api/v2/matchup`.
- Production curl: `POST /predict` → 200 with full prediction. `GET /predict/player/Jannik%20Sinner` → 200 with `attributes` object.
- Local: both parquet files exist (`-rw-r--r-- ... 22M parsed_points.parquet`, `203K player_profiles.parquet`).
- `/usr/bin/git check-ignore data/processed/player_profiles.parquet data/processed/parsed_points.parquet` → both reported as ignored.
- `/usr/bin/git ls-files data/processed/` returns no parquet files; only json/pkl/png headshots are tracked.
- Playwright reproduction (`tests/e2e/session18_compare_diagnose.spec.js`): after 15s wait, `#content` is still `<div class="loading"><div class="spinner"></div>...Analyzing matchup...</div>`. 12 console errors of the form "Failed to load resource: 503". Backend response log shows the three v2 endpoints retried 6+ times each, consistent with warmup.js' retry budget.
- Screenshot: `_session18_artifacts/compare_players_bug.png`.
- Diagnostic JSON: `_session18_artifacts/compare_players_diagnose.json`.

Verified: yes.
Phase 2 priority: high (Phase 3a).

Fix candidates (decision deferred to Phase 3a):
1. Bundle the parquet files into the App Runner deploy (Dockerfile COPY or workflow S3 sync). Smallest change, fastest result.
2. Have `warmup.js` short-circuit 503 retry for endpoints under `/api/v2/` since those are data-static rather than warming up. Decouples the symptom from cold-start latency.
3. Have `compare.html`'s `fetchLegacyMatchup` use `signal: AbortSignal.timeout(5000)` and treat the v2 endpoints as best-effort, not blocking.

## Finding: Docker pipeline followup mischaracterised in onboarding

Symptom: Onboarding doc asked whether Session 17 followups claimed Docker was "blocked on AWS secrets".
Evidence:
- `_session17_followups.txt:40-49` describes Docker activation as scaffolded-not-active and lists the gating items: create ECR repo `tennisiq-api` in us-east-1, reconfigure App Runner service from source-code to ECR-image mode in AWS console, confirm `APPRUNNER_SERVICE_ARN` still points at the reconfigured service, then `gh workflow run deploy-api.yml`.
- `gh secret list` confirms `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `APPRUNNER_SERVICE_ARN`, `CLOUDFRONT_DIST_ID` all present.
- The followups doc itself does not claim secrets are the blocker; the gate is manual AWS console infrastructure work.

Resolution: the onboarding-implied conflict does not exist. Followups doc is accurate as written. Real Docker blocker is a Robert-action item (manual ECR repo creation + App Runner mode flip), not a secrets gap.
Verified: yes.
Phase 2 priority: low (defer to Session 19+).

## Summary

Session 17 close-out: verified clean (TokenRouter wired, tournament_narrative off, published.json scrubbed, secrets present).

Endpoints with regressions: none. All 15 endpoints in the audit list return 200.

Compare Players bug classification: combined backend-deploy gap + frontend retry-budget interaction. Backend v2 endpoints persistently 503 because parquet data files are gitignored and not deployed; frontend warmup.js retries 503 for 63s, blocking the compare render's `Promise.all`. Fix is small and safe — three viable approaches listed above.

Robert-action items: none required for Phase 2 to proceed. (Docker pipeline activation remains a Session 19+ infrastructure task, unchanged.)

Phase 2 priorities (ordered):
1. Build Tennis Abstract scraper + canonical `data/live/tournament_state.json` (Phase 2).
2. Fix Compare Players bug (Phase 3a), most likely by short-circuiting `/api/v2/*` 503 retries in `warmup.js` or shipping the parquet files via S3 sync at container start.
3. Reconcile `/api/live-tournament`, `/api/key-matchups-live`, `/api/tournament-predictions` to read the canonical state (Phase 3b-d).
4. Browser verification (Phase 5) and docs (Phase 6).
