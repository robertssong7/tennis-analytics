# Session 17 Report

**Date:** 2026-05-15
**Branch:** main
**Production URLs:**
- Front-end: https://tennisiq-one.vercel.app
- Backend: https://su7vqmgkbd.us-east-1.awsapprunner.com

## Context

Session 17 launched with five user-visible improvements plus the AI insights
engine as the headline feature. An earlier agent (Antigravity) executed
partial work for Phases B/C/D/E and left it uncommitted in the working tree
without independent verification. This Claude Code continuation session
audited that work, remediated the gaps, shipped the AI insights engine,
verified everything in browser, and documented the result.

## Audit findings (Phase 1)

No Antigravity commits landed on `main`. All claimed work sat uncommitted
in the working tree. Production at `e3bfb1f7` (session 16.4) passed the
14-endpoint regression audit at session start: every endpoint returned
HTTP 200 with a valid body. Full details in `_session17_artifacts/_session17_audit.md`
and `_session17_artifacts/endpoint_audit.log`.

Per-deliverable summary:

- **Phase B (autocomplete)**: Antigravity correctly switched from the
  broken-data `/api/v2/search` to the richer `/players/search`. Missing
  per spec: nationality flag, FIFA tier badge, 8-row cap, 44px touch
  targets. Stale legacy fallback URL pointed at `tennisiq-api.onrender.com`.
- **Phase C (memory fix)**: column-narrowing on `pd.read_parquet` for
  `/patterns` and `/scenarios`. Audit confirmed every consumed column is
  present in the subset and that `/scenarios` is not a new endpoint
  (introduced in commit `6edd8528` Session 2A). Kept as-is.
- **Phase C (empty-state banner)**: `data_confidence in {excluded, none}`
  branch in `player.html`. Cruder than the spec's MCP coverage dot, but
  functional v0. Kept; MCP dot deferred to session 18.
- **Phase D (tournament scraper)**: hit `atptour.com` directly. Mandatory
  replacement per session reconciliation. Reverted; the existing
  calendar-driven `_live()` path in `tools/refresh_live_tournament.py`
  already returns the correct live tournament (Italian Open 2026 Clay).
- **Phase E (Docker)**: real multi-stage Dockerfile and new GHA
  `deploy-api.yml`. Significant concerns: python 3.12 to 3.11 downgrade,
  `COPY . .` instead of selective, unhardened S3 curl, missing
  `MALLOC_ARENA_MAX`, removed `--timeout-keep-alive`. Hardened in Phase
  2e but not activated; App Runner is in source-mode and the switch is a
  Robert action.

Also flagged: three untracked dev-scratch scripts (`test_endpoints.py`,
`test_fastapi.py`, `test_local.py`) and an Antigravity edit to a session
14 historical artifact. All cleaned up.

## Remediation actions taken (Phase 2)

- Reverted `_scrape_atp_current()` in `tools/refresh_live_tournament.py`.
- Kept Antigravity's parquet column-narrowing on `/patterns` and
  `/scenarios`.
- Kept Antigravity's player-profile empty-state branch.
- Enhanced `frontend/public/dashboard/autocomplete.js`: country flag from
  the new `flag` field on `/players/search`, FIFA tier badge with the
  Tiffany Legendary accent plus metallic Gold/Silver/Bronze treatments,
  8-row cap, 44px min height, `escapeHtml` on injected content. Updated
  stale fallback host.
- Added `flag` to the `/players/search` response (one-line addition that
  reuses the existing `_country_flag(ioc)` helper).
- Hardened the Dockerfile: restored python 3.12, `MALLOC_ARENA_MAX=2`,
  `PYTHONDONTWRITEBYTECODE=1`, `--timeout-keep-alive 120`,
  `${PORT:-8000}` parameterization, selective COPYs, retry + `--fail`
  on the build-time S3 fetch, removed the risky build-time engine
  prewarm.
- Gated `.github/workflows/deploy-api.yml` to `workflow_dispatch` only
  so it does not silently push images on every commit. The Docker
  activation path remains blocked on a Robert AWS-console action to
  reconfigure App Runner from source-mode to ECR-image mode.
- Removed three untracked dev scratch tests.
- Triggered a manual Vercel deploy (`vercel --prod --archive=tgz`)
  because Vercel auto-deploy is disabled per handoff documentation
  ("Auto-deploys do NOT happen, must be manual via CLI").

## AI insights engine v1 (Phase 3)

Engine scope landed in `tools/insights_engine/`:

- `generate_candidates.py` calls the deployed API for the active player
  pool (union of `/api/active-players`, tournament favorites, and key
  matchups), then produces candidates in three categories. Each
  candidate carries the structured `supporting_metrics` the verifier
  cross-checks.
- `verify_facts.py` re-fetches the live engine state and rejects any
  candidate whose numbers have drifted more than 0.6 FIFA points.
- `edit_with_haiku.py` calls Claude Haiku 4.5
  (`claude-haiku-4-5-20251001`) with a strict ESPN/538 system prompt
  (no em dashes, no marketing, 60-word body cap, JSON-only output).
  Records `input_tokens`, `output_tokens`, and a USD cost per call.
- `run.py` orchestrates: generate, verify, edit (or fall back to a
  deterministic seed template when `ANTHROPIC_API_KEY` is missing or
  the monthly budget would exceed $5), diversify to two-per-category
  capped at six total, write `data/insights/published.json` + a rolling
  `history.json`, log spend.

First seed run:

- candidates generated: 5
- fact-verified: 5 (no rejections; tolerance was sufficient for the
  same-process drift window)
- published: 3
- categories represented: surface_specialists (2), tournament_narrative
  (1)
- haiku run cost: $0.00 (seed mode, no API call)

`form_reversals` produced zero candidates today because no top-pool
player's display rating crossed a tier boundary. That is honest
signal, not a bug; the category will populate the day Lehecka, Rinderknech,
or any rising/falling player flips above 80/below 91.

API:

- `GET /api/insights/recent?limit=N[&subject=Name]` returns the
  published items, cached 300 s.

Homepage:

- The single placeholder insight card was replaced with a three-card
  feed using `/api/insights/recent`. Existing pinned-insight admin
  override still takes precedence and renders in a Pinned card above
  the feed. Skeleton placeholder shows during the initial fetch.
  Auto-refresh every five minutes.

Cron:

- `.github/workflows/insights_engine.yml` runs every six hours and on
  manual dispatch. Commits `published.json` + `history.json` back to
  main via `PAT_TOKEN`. Until `ANTHROPIC_API_KEY` lands in the repo
  secrets, the cron will continue publishing deterministic seed
  insights with identical structure to the Haiku output.

## Descope decision

The session 17 reconciliation D allowed a v0 descope at the 4-hour
Phase-3 mark to: 3 categories, homepage feed only, no profile-page
section, no novelty dedup, no admin override (the admin override
already partially existed). The full v0 shipped on time: 3 categories,
homepage feed, GHA cron. Profile-page section and embedding dedup were
deferred to session 18 to make room for browser verification and docs
within the session window.

## Cold-deploy timing

App Runner is in source-mode and has not been switched to image-based.
Cold deploy timing is unchanged from session 16.4 (~30-60s when warm,
multiple minutes from cold). The hardened Dockerfile + `deploy-api.yml`
workflow are committed as scaffolding for a future switch.

App Runner deploy windows during this session were slow (multiple
operations stacking on the same service). Backend changes from Phases
2 and 3 took ~30+ minutes to fully propagate.

## Browser verification (Phase 4)

Existing `tests/e2e/session164.spec.js` (homepage, compare, tournament):
3/3 green.

New session-17 specs:

- `session17_search.spec.js`: autocomplete dropdown shows player rows
  for "Sin", keyboard navigation selects, 375 px mobile viewport keeps
  44 px touch targets.
- `session17_player_info.spec.js`: Sinner profile renders rating tier,
  Nadal profile renders Legendary with `Peak: YYYY` label.
- `session17_tournament.spec.js`: tournament page shows current
  tournament name and year.
- `session17_insights.spec.js`: homepage insights feed renders at least
  one card.

All specs use the `pageerror` strict-gate, `console.error` diagnostic-
log convention from session 16.4. Screenshots saved under
`_session17_artifacts/`.

## Robert action items

1. **`ANTHROPIC_API_KEY` not in repo secrets.** Phase 3 cron currently
   falls back to seed-template rendering. Add the secret so the next
   cron run replaces seed copy with Haiku-edited prose at identical
   factual content. The seed insights are still valid; this only
   upgrades the voice.
2. **Docker pipeline activation.** App Runner is in source-mode
   (auto-deploys from GitHub). To use the image-based pipeline in
   `.github/workflows/deploy-api.yml`: (a) create ECR repo
   `tennisiq-api` in `us-east-1`, (b) reconfigure the App Runner
   service in the AWS console to pull from ECR, (c) confirm
   `APPRUNNER_SERVICE_ARN` still points at the reconfigured service,
   (d) `gh workflow run deploy-api.yml` manually for the first build.

## Commits this continuation

- `a48c1430` chore(session 17): audit antigravity's claimed deliverables with evidence
- `b2881e6f` session 17 phase 2: remediation of antigravity uncommitted work
- `ab808ad6` session 17 phase 3: AI insights engine v1 (seed mode, haiku-ready)
- (Phase 4 and Phase 5 commits land at session close)
