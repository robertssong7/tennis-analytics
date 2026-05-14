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

## 16.1 Hotfix — DATABASE_URL removed, 7 endpoints ported to engine memory

Production diagnostic after Session 16 confirmed that `/player/{name}`,
`/patterns/{name}`, `/tournament/{name}`, `/matchup`, `/cards`,
`/elo/history/{name}`, and `/players/search` were all returning
`{"detail": "DATABASE_URL not configured"}` because the Supabase tenant
they targeted has been deleted since before Session 14 (this is the
pre-existing issue documented in Session 15 followup #4 and in §10 of
this doc; Session 16 did not cause it but also did not fix it).

The original hotfix brief asked for the database to be restored. After
audit, Robert picked option (b) from Session 15's followup list: refactor
the endpoints to read from in-memory engine state instead. That path
eliminates the database dependency entirely, doesn't need an external
account or new IAM, and uses data the engine already loads at startup.

Commit `15d61404`. Changes:

- New helpers `find_player(name)`, `get_profile(player_id, surface)`,
  `get_card_attributes(player_id, surface)`, `get_h2h(p1_id, p2_id)`
  read from `engine.glicko.ratings`, `engine.attributes` (which is a
  `PlayerAttributeAccumulator` per player), and `engine.h2h`. `player_id`
  is the canonical full name (the legacy numeric IDs lived only in the
  deleted Postgres).
- `/player/{name}` response now includes `is_retired`, `peak_year`, and
  `peak_rating` (the Phase 6 fields, populated against real engine data).
- `/patterns/{name}` synthesizes ace_rate, first_serve_won, bp_save_pct,
  etc. as ratios from the attribute accumulator. Serve-direction wide/
  body/T fields stay null because that data was only in the deleted
  player_profiles table; the per-direction split needs MCP-derived
  pattern stats which are a Session 17 followup.
- `/tournament/{name}` reads `data/processed/atp_calendar_2026.json`.
  `current` and `live` resolve to the active tournament; if none is
  live, falls through to most-recent-finished.
- `/cards` iterates `engine.glicko.ratings` with the legendary-first
  tier ordering and chosen sort.
- `/players/search` substring-matches against `engine.player_names`.
- `/elo/history/{name}` now returns `available=false` with the current
  per-surface ratings; per-match trajectory is not persisted (P3 future).
- `/matchup` inlines the Elo expected_score formula (the original
  `src.elo.elo_engine` module is no longer in the tree; the production
  prediction path lives in `PredictEngine.predict`).
- `psycopg2` imports removed from `src/api/main.py`. `psycopg2-binary`
  removed from `requirements.txt`. `DATABASE_URL` section removed from
  `.env.example`.

### 16.1 verification

Local TestClient, all 14 probed endpoints return 200:

| Endpoint                                | Local | Notes |
|-----------------------------------------|-------|-------|
| /health                                 | PASS  |       |
| /player/Sinner                          | PASS  | retired=false, rating=100.2 |
| /player/Nadal                           | PASS  | retired=true, peak_rating=2684.6, peak_year=2013 |
| /player/Federer                         | PASS  | retired=true, peak_rating=2700.5, peak_year=2007 |
| /player/Djokovic                        | PASS  | retired=false |
| /tournament/current                     | PASS  | Italian Open status=live |
| /tournament/Italian                     | PASS  |       |
| /patterns/Sinner                        | PASS  | confidence=high, match_count=533 |
| /patterns/Alcaraz                       | PASS  |       |
| /matchup?p1=Sinner&p2=Alcaraz           | PASS  | clay surface, win_prob populated |
| /players/search?q=fed                   | PASS  |       |
| /cards?tier=legendary&page_size=3       | PASS  |       |
| /elo/history/Sinner                     | PASS  | available=false, current ratings included |
| /insight/current                        | PASS  | placeholder shape |

Production verification is pending the App Runner roll of `15d61404` at
the time of writing. The Phase 12 `/insight/current` endpoint did roll
successfully (returns 200 with placeholder); the 16.1 endpoints had not
yet propagated when last polled (still showing the DATABASE_URL 500). 
Robert should re-run the brief's 5-curl gate once the deploy completes.

### Files changed (16.1)

- `src/api/main.py` — helpers + 7 endpoints rewritten, `predict_win_prob` patched, psycopg2 imports removed, `get_conn` deleted
- `requirements.txt` — dropped `psycopg2-binary==2.9.11`
- `.env.example` — `DATABASE_URL` section replaced with a note explaining no DB is required
- `_session16_followups.txt` — Phase 2 full migration item marked RESOLVED with reference to commit 15d61404

## 16.2 Hotfix — Cold-start UX (decoupled health + frontend banner + diagnostics)

After 16.1 every data endpoint runs through `_get_engine()`, so the 30-90s engine warmup window now showed as a 503 wall on every page. Three layers applied:

### L1 — Endpoint split (commit `2d0e50af`)

- `/health` stays cheap, no engine call, used by App Runner health probe.
- `/ready` returns 200 only when `_predict_engine_loaded`, 503 otherwise with the warming-up retry hint. Frontend gates UI hydration on this.
- `/warm` forces the background preload to fire if not started, blocks up to 110s on the loaded event (under App Runner's edge timeout), returns 200 + `load_ms` on success or 202 + `elapsed_ms` if still loading at timeout (keeps the cron green so the next run retries without alarms).
- Keep-warm cron now hits `/warm` (was `/health`) every 2 minutes (was 4). Shorter cadence is cheap on the AWS credit pool and gives faster recovery if App Runner reaps an instance between scheduled pings.
- All three endpoints have `Cache-Control: no-store` via the existing middleware so CloudFront never serves stale state.

### L2 — Frontend resilience (commit `288997cc`)

New `frontend/public/dashboard/warmup.js` loaded after `config.js` on `index.html`, `player.html`, `compare.html`, `tournament.html`, `trends.html`. Two concerns in one file:

1. **Fetch wrapper.** Monkey-patches `window.fetch`. Requests targeting `window.API_URL` get auto-retried at 2 s / 5 s / 10 s on HTTP 503. `/warm` and `/ready` themselves pass through unwrapped so the banner poller sees raw 503s and the helper does not recurse.
2. **Warming-up banner.** Calls `GET /ready` on page load. If 200, no-op. If 503, injects a fixed-top banner reading "TennisIQ is warming up. Data will load in 30-60 seconds." with a pulsing teal dot, polls every 5 s for up to 5 minutes, hides on first 200 and emits a `tiq:ready` window event for pages that want to re-kick their data loads.

Skeleton loaders on the underlying pages stay in their loading state during the retry window because their fetch() calls are silently looping behind the banner — no flash of error.

### L3 — Engine startup logging (commit `e6564cce`)

`PredictEngine.load()` now emits per-phase timestamps + durations as `[engine-load] phase=<name> dt=<sec> total=<sec>`. Local M3 Pro cold-load: 11.61 s, dominated by `attribute_proxies` (5.85 s, parsed_points iteration) and `match_caches` (4.05 s, Sackmann CSV iteration). Production cold-load is in the 60-200 s range, roughly 5-17x slower on App Runner's single vCPU; exact numbers wait on Robert pulling the next cold deploy's CloudWatch logs (the `tennisiq-deploy` IAM user does not have CloudWatch read). Both bottlenecks have concrete fixes already listed under Phase 3 Docker in `_session16_followups.txt`.

### 16.2 verification (production, after deploy + engine warm)

| Endpoint                                | Result |
|-----------------------------------------|--------|
| /health                                 | PASS — `{status: ok}`, no engine call |
| /ready                                  | PASS — `{ready: true}` |
| /players/search?q=sinner                | PASS — Sinner card returned with fifa_rating 100.2 |
| /matchup?p1=sinner&p2=alcaraz&surface=hard | PASS — full matchup payload |
| /api/key-matchups-live                  | PASS — Alcaraz vs Sinner clay final |
| /api/live-tournament                    | PASS — Italian Open clay |
| /api/active-players                     | PASS — Alcaraz |
| /api/stat-of-the-day                    | PASS — Cerundolo clay-edge stat rendered |
| /cards                                  | PASS — Sinner first, legendary tier sort |

Frontend: all five dashboard pages load with `warmup.js` referenced once each (verified via curl). Banner UX assumes a cold-start window; impossible to time perfectly from CLI but will surface on the next App Runner reap.

Keep-warm workflow dispatched manually on the rewired `/warm` target (run `25847387254`, success, 8 s).

### Files changed (16.2)

- `src/api/main.py` — new `/ready`, `/warm`; cache-control list extended
- `src/api/predict_engine.py` — per-phase timing logs in `load()`
- `.github/workflows/keep-warm.yml` — endpoint is now `/warm`, cron `*/2 * * * *`
- `frontend/public/dashboard/warmup.js` — new, 159 lines
- `frontend/public/dashboard/index.html`, `player.html`, `compare.html`, `tournament.html`, `trends.html` — one new `<script src="warmup.js">` each
- `_session16_followups.txt` — added engine-warmup profiling section with local + prod numbers and the Phase 3 Docker fix path
