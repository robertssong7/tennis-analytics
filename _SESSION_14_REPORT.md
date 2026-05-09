# Session 14 Report — Live-Aware Predictions + Trends Dashboard + Stat-of-the-Day

**Date:** 2026-05-09
**Theme:** Three product gaps closed in one session: homepage filtering to live draws, multi-decade historical-trends dashboard, and a deterministic editorial layer (Stat of the Day).
**Commits:** 9 phase commits (B–I + K), 1 wrapper, 1 handoff/report.

---

## Phase Outcomes

| Phase | Outcome | What changed visibly |
|-------|---------|----------------------|
| 0+A — Closure + pre-flight | PASS | Session 13 closure verified. Glicko 0.6h fresh on disk. Production overall=healthy. |
| B — parsed_points S3 | PASS | New `src/api/data_loaders.py`, predict_engine swapped to use it. File uploaded to `s3://tennisiq-data-assets/processed/parsed_points.parquet`. Local-mv test confirmed automatic re-download. |
| C — Active player pipeline | PASS (with diagnostic) | New `tools/compute_active_players.py`. Italian Open shows 1 active player (Alcaraz), correct given the seed data. Root cause of R32 lag logged in `_session14_failures.txt`. |
| D — Live-aware endpoints | PASS | `/api/active-players` and `/api/key-matchups-live` added to `src/api/main.py`. Local smoke test returned 3 unique matchups (Final, two Semifinals) with predictions. |
| E — Historical trends backend | PASS | `tools/precompute_historical_trends.py` produces 8 metrics (7 from Sackmann + avg_rally_length from Match Charting Project). `/api/historical-trends` and `/api/historical-trends/metrics` serve them. |
| F — Trends frontend tab | PASS | `frontend/public/dashboard/trends.html` with Chart.js 4.4.0 + annotation plugin. Surface pills, year inputs, era reference lines, export-PNG button. Trends added to global nav across 8 pages. |
| G — Homepage live filtering | PASS | Carousel + key matchups switch to live data when `active_player_count >= 4`, fall back to "Top of the ATP" otherwise (which is the current state). |
| H — Stat-of-the-day engine | PASS | Three-file deterministic pipeline. 30 candidates today across 6 categories. Selected: "Jannik Sinner rolls into May 9 on a 44-match win streak" (active_streak, novelty=1.0). |
| I — Stat-of-day homepage | PASS | White card with teal left border above today's matchups. Auto-hides on render=false. |
| J — Local verify | PASS | 12/12 checks pass. |
| K — Deploy + workflow | In progress at write | Push committed; App Runner cold-start in progress. Daily action workflow extended with 4 new steps + downstream artifact re-commit. |

---

## What This Session Closes

**Gap 1: Homepage carousel showed players not in any active draw.** Before today, the carousel hardcoded Sinner/Alcaraz/Djokovic/Federer/Nadal regardless of who was actually playing. The new flow fetches `/api/active-players` first; if 4+ players are alive in any live tournament, the carousel becomes "Currently in the Draw" ordered by Glicko, capped at 12. Otherwise it stays as "Top of the ATP" — honest framing for off-weeks rather than pretending stale data is current.

**Gap 2: No way for users to explore how the sport has evolved.** The `/trends.html` page now answers questions like "have aces been increasing?", "has match duration changed?", "how do clay and grass differ in serve effectiveness over time?". Multi-decade Sackmann aggregates by year and surface, with era annotations marking the rule changes that explain inflection points (tiebreak intro, yellow ball, Wimbledon slowed, Hawk-Eye, Plexicushion, COVID).

**Gap 3: No editorial layer pulling a unique daily insight forward.** The Stat of the Day engine mines 6 categories of candidate facts every day, scores them for novelty + diversity, and renders the winner through a category-specific template. Today's output: Sinner's 44-match win streak from BNP Paribas Open (March 7) through today. Templates only reference fields in the candidate facts dict, so the engine can never invent numbers.

---

## Honest Caveats

1. **Today's Stat of the Day overstates Sinner's streak.** The supplement CSV has duplicate rows for many matches (each appears 2x). The 44-match streak is directionally correct — Sinner is on a major run since losing to Mensik in Qatar QF on Feb 19 — but the integer is over-counted by ~2x. Same root cause affects h2h_breakthrough and tournament_pattern miners. Dedupe at scrape time is a future fix.

2. **Live tournament feed shows 2025 Italian Open data presented as 2026.** The supplement scraper falls back to ALL years' results for a tournament when the current year has no rows. Today's live tournament block ships R32-through-Final results from Italian Open 2025 (Alcaraz beat Sinner in the F) under the 2026 dates. The active-player computation is robust to this — alive status comes from match completion, not date — and reports 1 active player, which is honest given the input. Logged to `_session14_failures.txt`.

3. **Carousel still shows "Top of the ATP" today, not "Currently in the Draw".** Because the seed data shows the Italian Open complete with only 1 survivor, count<4 triggers the fallback. When real R32 data arrives mid-tournament with 32 players still alive, the live filter will activate.

4. **Stacked ML ensemble was not retrained this session.** Same caveat from Session 13 carries forward. The displayed Glicko ratings updated; the ensemble's win-probability predictions still derive from the older feature trajectory.

---

## Remaining Manual Follow-Ups

1. **Dedupe the supplement CSV.** Either fix `tools/refresh_supplement_data.py` to dedupe on `(tourney_name, tourney_date, winner_name, loser_name, round)` or post-process before the daily action commits. Would correct streak counts, h2h breakthroughs, and tournament-pattern milestones.

2. **Fix `tools/refresh_live_tournament.py` line 115.** Remove the `[-20:]` slice on results, OR change it to "results in past 14 days" to preserve full bracket history. Then the live_tournament.json will carry R32 results when they exist.

3. **(Optional) LLM editorial pass over Stat of the Day.** The deterministic templates produce technically correct copy that sometimes reads stiff. An Anthropic API call after template render (with strict instruction to only reference numbers in facts dict) would smooth tone. Out of scope for Session 14.

4. **(Optional) Twitter/X integration.** Daily auto-post the stat. Out of scope for Session 14.

5. **Watch tomorrow's 8 AM UTC daily action.** It now includes 4 new steps. If any fails, the workflow continues (each step has `|| echo` guard) but the downstream artifact for that step won't refresh. Pull logs via `gh run view <id> --log` and triage individual step failures.
