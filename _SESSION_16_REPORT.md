# Session 16 — Infrastructure pass + insight card scaffold

**Date:** 2026-05-13 evening (UTC 2026-05-14 early morning)
**Brief:** Originally 13 phases (Track A infra + Track B insight engine).
Scope reduced mid-session by Robert after preflight surfaced hard
environment blockers (no local Docker, no ANTHROPIC_API_KEY) and a
realistic time budget for a single chat session.

## Shipped this session

| Phase | Subject                                                       | Commit     | Status |
|-------|---------------------------------------------------------------|------------|--------|
| 1     | boto3 in requirements, CORS tightened, .env.example           | 51c785ef   | DONE   |
| 2 (partial) | Supabase audit + dead-path comment (full migration deferred) | 832e3468   | DONE   |
| 3 (fallback) | start.sh cold-start optimization (Docker deferred)         | 41e4abaf   | DONE   |
| 5     | Cache-Control + gzip + keep-warm cron + homepage skeleton     | df659ba3   | DONE   |
| 6     | Retired-player peak verify (Session 10 work re-verified)      | (no edit)  | DONE   |
| 12    | Insight admin override + homepage card with placeholder       | afb0b367   | DONE   |

## Deferred to Session 17 (see `_session16_followups.txt` for per-item details)

- Phase 2 full S3 migration of 7 get_conn() endpoints (~3-4 hr, needs profile export pipeline)
- Phase 3 Docker image deploy (~4-6 hr, needs local Docker install + extended IAM)
- Phase 4 live tournament source (~6-10 hr, needs source evaluation; do NOT reverse-engineer ATP internal API)
- Phase 7 MCP coverage tiers + regression (~6-8 hr)
- Phase 8 candidate generator extension (~6-8 hr)
- Phase 9 hard-gate verifier (~3-4 hr)
- Phase 10 90-day novelty dedup (~4-6 hr, ~500 MB sentence-transformers dep)
- Phase 11 Haiku 4.5 editorial layer + $5/mo spend cap (~4-5 hr, needs ANTHROPIC_API_KEY)
- Phase 13 6-hour insight cron (~1-2 hr, depends on 8-11)
- App Runner instance role IAM grant on `processed/*` (15 min, AWS console)

## Verification-gate results

### Local (TestClient against current main branch)

| Gate                                              | Result |
|---------------------------------------------------|--------|
| `import boto3` succeeds                           | PASS (1.42.67) |
| /health 200                                       | PASS |
| Origin `https://example.com` → no ACAO header     | PASS |
| Origin `https://tennisiq-one.vercel.app` → 200    | PASS |
| /health Cache-Control: no-store                   | PASS |
| /api/live-tournament Cache-Control: max-age=120   | PASS |
| /api/historical-trends Cache-Control: max-age=3600| PASS |
| /insight/current Cache-Control: max-age=120       | PASS |
| /api/historical-trends Content-Encoding: gzip     | PASS |
| /insight/current returns placeholder shape        | PASS |
| /admin/insights/override Bearer ADMIN_TOKEN auth  | PASS (401 without, 200 with) |
| Pin/clear cycle round-trip                        | PASS |
| Nadal/Federer/Murray render Legendary with peak   | PASS (96.0/96.1/95.8) |
| Djokovic/Alcaraz/Sinner render active with form   | PASS (95.8/96.3/100.2) |

### Production (post-Phase-1 push at commit 51c785ef)

App Runner auto-deploy is in flight at the time this report is written.
Per V3_7 §3, cold-deploys take 12-13 min on the source-code path; the
keep-warm action will hold the instance warm once the new code lands.
Production verification of cache headers, gzip, and /insight/current
should be repeated after the App Runner deploy completes.

| Gate                                          | Result |
|-----------------------------------------------|--------|
| GET /health 200                               | PASS (200, x-envoy-upstream-service-time: 17 ms) |
| Frontend live at tennisiq-one.vercel.app      | PASS (HTTP 200, ETag matches new deploy) |
| Insight placeholder copy rendered server-side | PASS (`AI insight engine launches in Session 17`) |
| Keep-warm workflow dispatch                   | PASS (run 25844497338, success, 9 s) |
| /insight/current on production                | PENDING (still 404 — pre-Phase-12 code) |
| Cache-Control headers on production           | PENDING (App Runner deploy not yet rolled) |

## Frontend deploy

Pushed via `npx vercel --prod --archive=tgz --yes` from repo root.
Deployment ID: `dpl_HRwqiGhuCo9HqJp2ujM77BY5b82K`. Live at
`https://tennisiq-one.vercel.app`. Insight card placeholder visible
above Stat-of-the-Day on first paint.

## Model state (unchanged this session)

- **Glicko-2:** still on the Session-13 retrain (2026-05-09, 7,571 players, single-pass).
- **Stacked ensemble Brier:** XGB 0.1887 / LGB 0.1855 / Stacked ~0.1845 (unchanged since training).
- **parsed_points.parquet:** 22 MB on S3 at both root and `processed/`, last modified 2026-05-09.

## Anthropic spend

$0.00 MTD. `ANTHROPIC_API_KEY` not set; Phase 11 deferred entirely.

## Production URLs

- Frontend: https://tennisiq-one.vercel.app (Vercel) + https://d3aogk1vtnp91d.cloudfront.net (CloudFront)
- API: https://su7vqmgkbd.us-east-1.awsapprunner.com (AWS App Runner)
- New endpoints (post-deploy): `GET /insight/current`, `POST /admin/insights/override`
- New workflow: `.github/workflows/keep-warm.yml` (cron `*/4 * * * *`, manual dispatch verified)

## Notes on what surfaced during the session

1. **Brief preflight failed:** the brief referenced `TENNISIQ_HANDOFF_V4_0.md`
   (does not exist; latest was V3_7) and a clean tree (was not — Session 15
   left two files modified). Surfaced to Robert, scope renegotiated.
2. **Brief assumed Docker installed + ANTHROPIC_API_KEY set + Supabase data
   on S3.** None held. Per Robert's call, deferred all blocked phases to
   Session 17 with detailed followups rather than attempting partial implementations.
3. **`tennisiq-deploy` IAM user cannot read App Runner, IAM, or attach policies.**
   This blocks the brief's Phase 2 instance-role grant from CLI. Logged as
   a console-only manual followup.
4. **Federer peak year is 2007 in `peak_elo.json` (Elo peak), not 2017** as
   the brief suggested. The data is correct — 2007 was peak Elo, 2017 was a
   memorable comeback year. No override applied; flagged in followups.
5. **Session 10 already shipped retired-player peak display.** Phase 6
   collapsed to a verify-only step; all three canonical players render correctly.

## Files changed

- `src/api/main.py` — CORS list, GZip + CacheControl middleware, `/insight/current`, `/admin/insights/override`, get_conn dead-path comment
- `requirements.txt` — `boto3>=1.34`
- `start.sh` — multi-module probe, --no-cache-dir, compileall, exec uvicorn
- `.env.example` — full env-var template
- `.github/workflows/keep-warm.yml` — new workflow
- `frontend/public/dashboard/index.html` — skeleton-loader CSS + DOM, insight card section, `loadInsight()` script
