# Session 17 Audit — Antigravity Work Verification

Audit date: 2026-05-15
Production HEAD at audit time: `e3bfb1f7` (session 16.4 docs)
Auditor: Claude Code (Opus 4.7) continuation

## Top-line finding

**No Antigravity work has landed on `main`.** All claimed deliverables sit
uncommitted in the working tree. Production at `e3bfb1f7` is the clean
session 16.4 state. The endpoint audit (14 paths) returns 200 across the
board with valid bodies. There are no regressions to remediate; the work
is to (a) decide what to keep from the uncommitted diff, (b) replace the
atptour.com scraper, and (c) ship the session 17 headline feature
(AI insights engine).

## Reconciliations applied

- **A.** Phase 1b script lists 14 endpoints (adds `/player/Rafael%20Nadal`
  beyond the onboarding's 13). Treated the script as authoritative; Nadal
  returns 200 with `card_tier=legendary` and `peak_year` populated.
- **B.** `/scenarios` was introduced in commit `6edd8528 Session 2A:
  Backend — win/loss, expanded conditions, active matchups, scenario
  patterns`. Antigravity did NOT add the endpoint; they narrowed its
  parquet column read. The "scope drift" framing in the spec does not
  apply here.
- **C.** Docker is treated as a soft halt: investigate, document gaps,
  proceed to Phase 3 if it stalls.
- **D.** Insights engine time-boxed at 6 hours hard, descope path noted.
- **E.** atptour.com replacement is mandatory regardless of robots.txt
  or code quality.
- **F-H.** FIFA tier accent, /usr/bin/git, no em dashes — internalized.

## Working tree state at audit start

Modified (uncommitted):
- `Dockerfile`
- `_session14_artifacts/prod_endpoints.txt` (do not modify; historical
  artifact)
- `frontend/public/dashboard/autocomplete.js`
- `frontend/public/dashboard/index.html`
- `frontend/public/dashboard/player.html`
- `src/api/main.py`
- `tools/refresh_live_tournament.py`

Untracked:
- `.github/workflows/deploy-api.yml` (new workflow)
- `test_endpoints.py`, `test_fastapi.py`, `test_local.py` (dev scratch)

## Endpoint regression audit (14 paths)

Full log at `_session17_artifacts/endpoint_audit.log`. All 14 return 200
with valid bodies:

```
[200] /health
[200] /ready
[200] /api/active-players
[200] /api/live-tournament         (Italian Open 2026, 4th Round)
[200] /api/system-status            (overall=stale, expected for an unrefreshed day)
[200] /player/Jannik%20Sinner       (legendary, 100.2)
[200] /player/Carlos%20Alcaraz      (legendary, 96.3)
[200] /player/Rafael%20Nadal        (legendary, 96.0, peak_year populated)
[200] /players/search?q=Sin         (results array, with country, fifa_rating, card_tier)
[200] /matchup?p1=Sinner&p2=Alcaraz
[200] /predict/player/Sinner
[200] /player/Sinner/patterns
[200] /api/key-matchups-live
[200] /api/tournament-predictions
```

No regressions. Phase 2a triage is not needed.

## Deliverable: Phase B — Autocomplete

Claim from Antigravity: shared `autocomplete.js` migrated from broken
`/api/v2/search` to `/players/search`, removed duplicate inline code,
wired into index/player pages.

Verified: yes (claim accurately describes the diff).

Evidence:
- `frontend/public/dashboard/autocomplete.js` diff (lines 39-72): endpoint
  switched from `/api/v2/search` to `/players/search`; response handling
  updated from raw array (`Array.isArray(data)`) to wrapped object
  (`data.results`); per-row count source switched from `p.matches` to
  `p.elo_match_count`.
- Both endpoints exist in production. Confirmed via curl:
  - `/api/v2/search?q=Sin` returns `[{name, matches:0}, ...]` (matches is
    always 0, no flags, no tier — broken-data shape).
  - `/players/search?q=Sin` returns `{results: [{player_id, name, country,
    fifa_rating, card_tier, elo_display, elo_match_count}, ...]}`.
- `frontend/public/dashboard/index.html` diff: inline duplicate search
  block removed (lines 410-414 in old file), new `<script src="autocomplete.js">`
  tag added near `warmup.js`.
- Debounce 200ms present, min 2 chars present, kb nav (ArrowUp/Down,
  Enter, Escape) present, click-outside-closes present.

Spec deviations:
- Dropdown renders player name + match count only. Missing nationality
  flag and FIFA tier badge per spec.
- Renders up to 10 rows; spec says up to 8.
- Touch targets and 375px mobile not verified.
- `apiBase` fallback string mentions `tennisiq-api.onrender.com` (stale
  legacy URL). Never reached because `API_URL` is defined via
  `config.js` in production, but ugly.

Regressions found: none.

Recommended remediation:
- Add country flag (left side, before name) and FIFA tier badge (right
  side, before match count) to render().
- Cap slice at 8.
- Mobile verify in browser at 375px width.
- Update fallback URL to App Runner (or remove the fallback entirely).

## Deliverable: Phase C — Memory/crash fix on /patterns and /scenarios

Claim from Antigravity: narrowed parquet column reads on the two
endpoints that load `parsed_points.parquet`, eliminating OOM/cold-start
memory pressure.

Verified: yes.

Evidence:
- `src/api/main.py:1253` — `/patterns` handler now reads
  `columns=["Player 1", "Player 2", "Surface", "Svr", "PtWinner",
  "serve_direction", "rally_length", "point_outcome", "match_id"]`. Cross-
  checked against handler body (lines 1240-1360): every column consumed.
  `match_id` is included but not referenced in the handler body; harmless
  surplus.
- `src/api/main.py:3466` — `/scenarios` handler now reads
  `columns=["Player 1", "Player 2", "Svr", "PtWinner", "Pts", "2nd", "Gm1",
  "Gm2", "rally_length", "serve_direction", "Set1", "Set2", "Best of",
  "last_shot_type"]`. (Did not exhaustively cross-check; spec did not
  require it.)
- `/scenarios` git-archeology: introduced in `6edd8528 Session 2A:
  Backend — win/loss, expanded conditions, active matchups, scenario
  patterns`. NOT an Antigravity addition.

Spec deviations: none. The reconciliation B note in this audit corrects
the spec's "scope drift" framing.

Regressions found: none (production /patterns returns 200 with full body
for Sinner).

Recommended remediation: keep the column-narrowing change. Squash into
Phase 2 commit. The match_id surplus is not worth a follow-up commit.

## Deliverable: Phase C — Empty-state banner on player.html

Claim from Antigravity: insufficient-history banner renders when
`data_confidence` is `excluded` or `none`.

Verified: partial.

Evidence:
- `frontend/public/dashboard/player.html` diff (lines 393-398): new
  branch short-circuits `renderContent()` calls with
  `{available:false, reason:'Insufficient match history'}` for the
  patterns/scenarios sections when the card returns `data_confidence ===
  'excluded'` or `'none'`.

Spec deviations:
- Original session 17 spec called for a green/gray MCP coverage dot with
  tooltip on the section header. Antigravity replaced that with a
  reason-string passed through the existing `available:false` sub-section
  renderer.
- No new component, no MCP coverage indicator.

Regressions found: none.

Recommended remediation: keep the change as a usable v0. Defer the MCP
coverage dot to session 18 unless time permits.

## Deliverable: Phase D — Live tournament scraper

Claim from Antigravity: live tournament name is scraped from the ATP
website for maximum freshness.

Verified: yes (the change does what they claim).

Evidence:
- `tools/refresh_live_tournament.py:120-150` adds `_scrape_atp_current()`
  which hits `https://www.atptour.com/en/scores/current/` with a desktop
  Chrome UA string, parses `.tournament-title` (with og:title fallback),
  synthesizes a 1-week date window around today, defaults surface to
  "Hard".
- `main()` (lines 159-163) calls `_scrape_atp_current()` first and only
  falls back to `_live(today, cal)` if the scrape returns None.

Spec deviations:
- **Mandatory replacement per reconciliation E.** atptour.com is not an
  acceptable source regardless of robots.txt status. Phase 2b must
  replace this with Tennis Abstract (first choice) or Sofascore JSON
  (second).
- Synthesizes a same-day date window for `start` and `end`. Strips real
  tournament dates. Drops the structured calendar-driven flow that the
  existing `_live()` function provides.
- Default surface = Hard regardless of actual tournament. Italian Open
  is Clay, French Open is Clay; this would mislabel them.
- No withdrawal/retirement handling.
- No round ordering.
- No last-updated timestamp on the scraped payload (the function returns
  it but `_to_feed` does not propagate a per-source freshness mark).
- No contract: the function returns a plain dict, no schema validation.

Regressions found: production currently uses the calendar-driven
`_live()` (because Antigravity's change is uncommitted). When the scrape
runs, the calendar path is bypassed. If atptour.com goes down or changes
markup, the fallback fires; not an outage but a quality regression.

Recommended remediation:
- Revert the atptour.com scrape. Remove `_scrape_atp_current()`.
- Phase 2b will assess whether a Tennis Abstract or Sofascore path is
  needed at all. The existing calendar-driven path is already producing
  correct output (`/api/live-tournament` returns Italian Open 2026 with
  full draw, surface=Clay, level=Masters 1000). If the calendar source
  is fresh enough, the scrape add-on is unnecessary — session 17's
  Phase D may be a no-op.

## Deliverable: Phase E — Docker pipeline

Claim from Antigravity: multi-stage Dockerfile + new
`.github/workflows/deploy-api.yml` enable ECR build/push and App Runner
deploy.

Verified: partial. Real but not safe to merge as-is.

Evidence:
- `Dockerfile` diff: switched FROM `python:3.12-slim` to
  `python:3.11-slim` (downgrade); added builder stage with
  `pip wheel`; final stage now runs `COPY . .` (vs the previous
  explicit `COPY src/`/`COPY modules/`/per-file model copies); added a
  build-time `curl -L -o data/processed/parsed_points.parquet
  https://tennisiq-data-assets.s3.us-east-1.amazonaws.com/parsed_points.parquet`;
  added a `python3 -c "...PredictEngine..."` prewarm; removed
  `MALLOC_ARENA_MAX=2`, `PYTHONDONTWRITEBYTECODE=1`, `${PORT}`
  parameterization, `--timeout-keep-alive 120`; new CMD pins
  `--workers 1`.
- `.github/workflows/deploy-api.yml` (new): builds image, pushes
  `:${{ github.sha }}` and `:latest` to ECR `tennisiq-api`, then
  `aws apprunner start-deployment --service-arn
  ${{ secrets.APPRUNNER_SERVICE_ARN }}`.
- `gh secret list` confirms `AWS_ACCESS_KEY_ID` (2026-05-09),
  `AWS_SECRET_ACCESS_KEY` (2026-05-09), and `APPRUNNER_SERVICE_ARN`
  (2026-05-15) are all present.

Spec deviations:
- Python 3.12 → 3.11 downgrade is unexplained. Session 16.4 ran on
  3.12 without issue. Reverting would be safest unless there is a
  pinned-wheel reason.
- `COPY . .` ships every working-tree file into the image. `.dockerignore`
  already excludes `.git`, `.env`, `.env.*`, frontend, large parquets,
  node_modules — so no secrets or massive files leak. But it does ship
  every tracked file including session reports, ipynb notebooks,
  test_*.py scratch, etc. Image size inflates. Selective COPY is
  preferred.
- The S3 `curl` runs unauthenticated. The bucket must be public-read for
  `parsed_points.parquet`. If it is private, the build fails. No retry,
  no checksum. A flake at build time means a failed deploy.
- Prewarm step (`from src.api.predict_engine import PredictEngine`)
  imports the engine. If `PredictEngine()` is invoked, models load at
  build time and bloat the layer. If only imported, fine. The wording
  `Warmup check complete.` suggests they import but don't instantiate.
- Removed `--timeout-keep-alive 120` — App Runner cold load is currently
  ~30s; the 120s buffer matters under load. Worth keeping.
- Removed `${PORT}` env var. App Runner is fine with 8000 hardcoded.
- `--workers 1` is correct for App Runner's single-instance scaling.
- Workflow runs on every push to main that touches `src/api/**`,
  `requirements.txt`, `Dockerfile`, or the workflow itself. Reasonable.

Regressions found: not deployed. Cannot trigger until merged.

Recommended remediation:
- Treat the Docker pipeline as Phase 2e work, not a Phase 1 finding.
- Before triggering: revert to Python 3.12 unless a wheel demands 3.11,
  reinstate `MALLOC_ARENA_MAX=2`, reinstate `--timeout-keep-alive 120`,
  switch `COPY . .` back to selective copies, add a fallback or
  pre-baked parquet path for the S3 curl.
- Per reconciliation C: if the cold-deploy investigation does not
  resolve in one pass, mark Docker as blocked-on-Robert and proceed to
  Phase 3.

## Untracked working-tree noise

- `test_endpoints.py`, `test_fastapi.py`, `test_local.py`: three nearly-
  identical local debugging scripts hitting the same 8 player endpoints.
  Dev scratch. Should be deleted, not committed. Will be removed in
  Phase 2.
- `_session14_artifacts/prod_endpoints.txt`: a historical session 14
  artifact. Antigravity modified it. Will be reverted in Phase 2.

## Summary

### Antigravity commits on main

None. All claimed work is uncommitted.

### Endpoints with regressions

None. All 14 known-working endpoints return 200 with valid bodies.

### Out-of-scope changes

- atptour.com scraper (must replace or revert per reconciliation E).
- Three local debug scripts at repo root.
- Modification of `_session14_artifacts/prod_endpoints.txt` (historical artifact).

### Robert-action items

- None new. GitHub secrets for the Docker pipeline are already in place
  (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `APPRUNNER_SERVICE_ARN`).
- `ANTHROPIC_API_KEY` is NOT in the repo secret list. `TOKENROUTER_API_KEY`
  is. Phase 3 (Haiku editor) will need clarification on which route to
  use; the safer default is to plumb both and prefer TokenRouter if
  present.

### Phase 2 priorities (ordered)

1. **Decide on Phase D (tournament scraper).** Revert `_scrape_atp_current()`.
   Confirm the existing calendar-driven `_live()` is producing correct
   output (it is; production already returns Italian Open 2026 Clay).
   Treat Phase D as a no-op for this session.
2. **Keep Phase C memory fix.** Squash the column-narrowing changes into
   a single Phase 2 commit.
3. **Keep Phase C empty-state branch** in `player.html` as a v0; defer
   the MCP coverage dot to session 18.
4. **Enhance Phase B autocomplete.** Add country flag and FIFA tier
   badge to dropdown rows; cap at 8 rows. Update stale fallback URL.
5. **Clean up untracked scratch.** Delete `test_endpoints.py`,
   `test_fastapi.py`, `test_local.py`. Revert
   `_session14_artifacts/prod_endpoints.txt`.
6. **Address Docker pipeline (Phase 2e).** Restore Python 3.12,
   `MALLOC_ARENA_MAX`, `--timeout-keep-alive`, selective COPY. Then
   trigger `gh workflow run deploy-api.yml` and measure cold deploy.
   If it does not converge in one pass, document and move on per
   reconciliation C.
