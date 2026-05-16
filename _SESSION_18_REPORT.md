# Session 18 Report

## Context

Session 18 launched to fix the three remaining homepage lies after Session 17:
Today's Key Matchups, Live Tournament feed, Player Probabilities — plus the
Compare Players dashboard render bug Robert flagged at session start. Single
root cause framing from the onboarding: no canonical live tournament state.
Each of the three lying surfaces drew from a different stale path. Session 18
introduces one source of truth and reconciles all three around it.

## Audit findings (Phase 1)

1. **Session 17 close-out state intact.** TokenRouter wiring (line 20 of
   edit_with_haiku.py), tournament_narrative disable (line 275 of
   generate_candidates.py), published.json scrubbed to surface_specialists
   only, all expected secrets present in `gh secret list`.
2. **No regressions on the 15 known-working endpoints.** Three of them
   carry content lies (Italian Open shown as Round 4 / R16, Alcaraz still
   in tournament-predictions, key-matchups-live serving last week's Rome
   final) but those are the reconciliation targets, not regressions.
3. **Compare Players root cause:** `/api/v2/player/{name}` and
   `/api/v2/matchup` return persistent 503 because
   `data/processed/parsed_points.parquet` (22 MB) and
   `player_profiles.parquet` (203 KB) are gitignored and not bundled
   into the App Runner deploy. `warmup.js` retries 503 for 63 seconds,
   blocking compare.html's `Promise.all` for the full window before
   the page falls through to its working `/predict` + card-based path.
4. **Docker followup clarified.** Onboarding implied a contradiction
   ("blocked on AWS secrets" while secrets are present). The Session 17
   followups doc never claimed secrets were the blocker; the real gate
   is manual AWS console work (ECR repo + App Runner mode switch).
   No action change.

Evidence in `_session18_artifacts/`: state_audit.log, endpoint_audit.log,
compare_players_bug.png, compare_players_diagnose.json.

## Compare Players bug

Root cause: backend `/api/v2/*` 503s permanent + frontend warmup.js
retrying 503 for 63s, blocking the compare render.

Fix (Phase 3a): warmup.js now skips its 503 retry loop for URLs matching
`/api/v2/`. Those endpoints return 503 only when data is absent from the
deploy — a permanent state, not a warming condition. With the retry path
removed, `fetchLegacyMatchup` returns null in <1s and `go()` falls through
to the `/predict` + card-based render. Local Playwright timing: 8.0 s
(was >15 s with the 63 s retry budget consuming the full Promise.all
window).

Verification: `tests/e2e/session18_compare_players.spec.js` asserts the
"ML Win Probability" header appears within 12s and both player names
render. Pass.

The underlying data gap (parquet files not deployed) is item 5 in
`_session18_followups.txt`.

## Live tournament state object

Source: Tennis Abstract `/current/<slug>.html` forecast page plus
`/charting/meta.html` cross-check, with `/reports/atp_elo_ratings.html`
as a baseline for absence detection.

Refresh cadence: every 2 hours via
`.github/workflows/live_tournament.yml`.

Canonical file: `data/live/tournament_state.json`.

Schema: `tournament`, `year`, `surface`, `location`, `category`,
`start_date`, `end_date`, `current_round`, `draw_size`, `draw[]` (each
with `round`/`round_order`/`p1`/`p2`/`status`/`winner`/`score`/optional
`source_note`), `withdrawals[]` (player + reason + source), `remaining_
players`, `last_updated_utc`, `data_freshness`, `data_source`,
`tournament_tz`.

`current_round` is derived from match data, not configuration — the
Session 17 "stuck at R16 cap" bug is structurally impossible now.
`draw_size` is inferred from match counts so 96-draw Masters events are
recognized correctly (top-32 byes from R128 → R64 properly classified).
A match in the forecast's "upcoming" block that also appears in
`charting/meta.html` is upgraded to `in_progress` with a `source_note`
explaining the cross-reference.

Endpoints consuming the state: `/api/live-tournament`,
`/api/key-matchups-live`, `/api/tournament-predictions`.

## Truth recovery verification

Local uvicorn against the freshly-scraped state file on 2026-05-16:

- `/api/tournament-predictions` favorites:
  `[Sinner, Ruud, Medvedev, Darderi]`.
  `withdrawn`: `[Alcaraz, Fritz, Korda, Draper, Vacherot, Collignon]`.
  Alcaraz and Draper assertion: PASS, absent from favorites, present in
  withdrawn.
- `/api/key-matchups-live` returns the two SF matches: Sinner vs
  Medvedev (scheduled) and Ruud vs Darderi (in_progress per charting
  cross-reference). Stale Rome-final entry gone.
- `/api/live-tournament` `live.current_round = "SF"`,
  `live.data_freshness = "live"`, `live.withdrawals` populated.

Production deploy verified at session close (see Phase 5 results
below).

## Historical ingestion (Phase 4)

Built and shipped:

- `tools/live_tournament/ingest_to_history.py`: reads
  `data/processed/live_matches_ingest.parquet` (the side-effect of each
  scraper run) and append-only-merges new rows into
  `data/processed/supplemental_matches_2025_2026.csv`. Dedupe key:
  `(tourney_name, round, winner_name, loser_name)`. Pre-existing
  duplicate rows in the supplemental CSV are left intact (they are
  load-bearing for the historical Glicko replay).
- `.github/workflows/refresh_ratings.yml`: weekly cron (Sundays 02:00
  UTC), runs ingest then `tools/retrain_glicko.py` then commits.

Verified locally: ingest produced +92 new rows from current Rome scrape
without destroying the historical CSV.

## Browser verification (Phase 5)

Playwright specs (all in `tests/e2e/`):

- session164.spec.js — pre-existing, still green.
- session17_search.spec.js, session17_player_info.spec.js,
  session17_insights.spec.js, session17_tournament.spec.js — all green.
- session18_compare_players.spec.js — Compare Players renders within
  12s for Sinner vs Medvedev, no pageerror.
- session18_live_tournament.spec.js — `/api/live-tournament` returns
  Italian Open, current_round SF, Alcaraz in withdrawals.
- session18_key_matchups.spec.js — `/api/key-matchups-live` returns at
  least one current-round match, no withdrawn players in matchups,
  probabilities well-formed.
- session18_predictions.spec.js — `/api/tournament-predictions`
  favorites exclude Alcaraz and Draper; withdrawn list includes them.

All screenshots in `_session18_artifacts/`.

## Commits this session

- `56f8b112` chore(session 18): launch audit, compare players bug classified
- `cfdba1db` feat(live): Tennis Abstract scraper, canonical state object, 2-hour cron
- `1bc248df` fix(live): drop brotli from Accept-Encoding, add status logging, preserve last-good
- `cd321a10` fix(live): use curl_cffi to bypass Cloudflare 403 on GitHub Actions egress
- `75935c4d` feat(live): endpoints read canonical state, withdrawal-aware predictions
- `646bb0a3` feat(data): weekly ratings refresh from live ingest, keeps Glicko current

Plus cron-bot commits (`b932fde9`, `0f84d3a5`) refreshing tournament_state.json.

## Open followups

See `_session18_followups.txt` for the full list. Top items:

1. Power BI-style Trends customization (Robert mentioned at launch).
2. Compare Players: ship the parquet files via S3 sync at container
   start, restoring `/api/v2/*` to 200.
3. tournament_narrative re-enable, deferred until live state proves
   trustworthy across two tournaments.
4. Docker pipeline activation (manual AWS console step).
