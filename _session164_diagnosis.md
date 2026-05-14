# Session 16.4 — Phase 1 diagnosis

**Method:** Followed the brief's Phase 1 step-by-step. Pulled latest. Curled each of the 4 "broken" endpoints in production. Read each handler body in `src/api/main.py`. Read engine method signatures in `src/api/predict_engine.py`. Read the live calendar JSON. Grepped the frontend consumers.

## Top-line finding

**All 4 endpoints already return HTTP 200 with populated data in production.** The brief's premise that they are broken is not supported by the current production state. What follows is the evidence per endpoint, then a separate diagnosis of the real failure the user is likely observing in the browser.

## /predict/player/{name}

- **Handler location:** `src/api/main.py:938`
- **503 cause:** Only during engine cold-start (`engine = _get_engine()` at line 947 raises `HTTPException(503)` while `_predict_engine_loaded` is False). Never raises 503 once warm.
- **Engine method required:** `engine.find_player(name) -> Optional[str]` and `engine.get_player_card(player_name, surface="hard") -> dict`. Both exist at `predict_engine.py:1022` and `predict_engine.py:1614`.
- **Data files required:** none beyond what the engine already loads (`glicko2_state.pkl`, `player_attributes_v2.pkl`, `peak_elo.json`).
- **Fix scope:** none required. Probe results: `/predict/player/Jannik%20Sinner` → 200 with `tier=legendary`, `overall=100.2`. Fuzzy match `/predict/player/jannik` → 200, returns "Jannik Sinner". Lower-case last-name match `/predict/player/sinner` → 200. Retired player `/predict/player/Rafael%20Nadal` → 200 with `is_retired=true`, `peak_year=2013`, `rating_label="Peak: 2013"`.
- **Frontend consumer:** `compare.html:254-255`, `index.html:297,318`, `player.html:383,966`. All use `r.ok ? r.json() : null` fallbacks — tolerant of failure.

## /player/{name}/patterns

- **Handler location:** `src/api/main.py:1228` (`player_patterns_new`; there is an older `/patterns/{name}` at line 1032 but the brief targets the `/player/.../patterns` variant).
- **503 cause:** Only during engine cold-start (`engine = _get_engine()` at line 1244). Player-not-found raises 404 at line 1247. No-MCP-coverage returns HTTP 200 with `{available: false, player, reason}`.
- **Engine method required:** `engine.find_player(name)`. Exists.
- **Data files required:** `data/processed/parsed_points.parquet` (lazy-loaded from S3 if absent; line 1249). File on App Runner disk after first request.
- **Fix scope:** none for 503 elimination. **Shape mismatch:** brief spec wants `{"data_confidence": "none", "patterns": null, "message": "..."}` for the no-coverage case; handler currently returns `{"available": false, "player": canonical, "reason": "..."}`. Frontend (`compare.html:252-253`, `index.html:317`, `player.html:403,901`) all consume via `r.ok ? r.json() : null` and look at `available` not `data_confidence`, so a shape change to match the brief would BREAK the frontend unless updated in lockstep. Recommendation: leave the existing `available`/`reason` shape (it works); update brief if the spec needs realignment.
- **Frontend consumer:** see above. Probe `/player/Lloyd%20Harris/patterns` (low MCP coverage, 8 matches) → 200 with `available: true`. Probe `/player/Nonexistent%20Player/patterns` → 200 with `available: false`. Never observed 503 when engine warm.

## /api/key-matchups-live

- **Handler location:** `src/api/main.py:4039`
- **503 cause:** Engine cold-start at line 4080. Otherwise returns 200 with `{matchups: []}` or populated list. Existing graceful empty cases at lines 4050, 4054, 4059, 4064, 4068, 4087.
- **Engine method required:** `engine.predict(p1, p2, surface)`. Confirmed exists at `predict_engine.py:1512`.
- **Data files required:** `data/processed/active_players.json`, `data/processed/live_tournament.json`. Both exist; daily action refreshes them.
- **Fix scope:** none. Probe → 200 with `matchups: [{player1: "Carlos Alcaraz", player2: "Jannik Sinner", surface: "clay", round: "The Final", actual_winner: "Carlos Alcaraz", predicted_p1_win_prob: 0.507, ...}]`.
- **Frontend consumer:** `index.html:433`. Single fetch; treats failure as carousel-falls-back-to-static.

## /api/tournament-predictions

- **Handler location:** `src/api/main.py:3161`
- **503 cause:** Engine cold-start at line 3182. Otherwise returns 200 with `{available: false, reason}` for no-calendar case, or populated favorites + dark horses.
- **Engine method required:** `engine.glicko.ratings` dict, `engine.latest_data_date`, `engine._supplemental_name_map`. All exist.
- **Data files required:** `data/processed/atp_calendar_2026.json` (14 entries, current; live entry resolves to Italian Open 2026-05-06 to 2026-05-17), `data/processed/supplemental_matches_2025_2026.csv`.
- **Fix scope:** none. Probe → 200 with `tournament: "Italian Open", year: 2026, surface: "Clay", draw_size: 91, favorites: [{player: "Jannik Sinner", overall_rating: 27..., ...}], dark_horses: [...]`.
- **Frontend consumer:** `index.html:487-490`.

## What the user is actually seeing — the real failure

If all 4 endpoints work warm and the brief says the browser shows broken state, the failure is in the cold-start UX path. Concrete numbers:

- Engine cold load on the **already-warm-disk** App Runner instance was measured at **23.14 seconds** in Session 16.3 (`/warm` returned `load_ms: 23140`). A truly cold deploy (fresh container, pip install, S3 fetch for `parsed_points.parquet`) takes 60-200 seconds.
- `frontend/public/dashboard/warmup.js` line 53 sets retry schedule `_retryDelaysMs = [2000, 5000, 10000]`. That's 4 attempts at `t=0, 2s, 7s, 17s`. Max wait before giving up: **17 seconds**.
- 17s < 23s engine load < 60-200s cold deploy. **warmup.js gives up before the engine is ready.** The page sees the final 503 and renders the broken state.
- Additionally, `warmup.js` dispatches a `tiq:ready` event when `/ready` flips 200, but no page-level code listens for it to re-trigger data loads. Even if the banner shows correctly, page fetches that already gave up after 17s do not retry.

## What would actually fix the user's symptom

Two cheap, contained changes (both in warmup.js, neither touches any of the 4 "broken" handlers):

1. **Extend the retry schedule** from `[2000, 5000, 10000]` to something like `[1000, 2000, 3000, 5000, 8000, 13000, 21000]`. Total budget ≈ 53s, which covers the warm-disk cold load and gives the engine enough time on a fresh deploy.
2. **Have pages listen for `tiq:ready`** and re-trigger their data loads. Or, simpler, have warmup.js trigger a full `location.reload()` after the engine flips ready, but only if any page fetch returned 503. This is less elegant but completely robust.

I would not pursue any backend handler change as part of this session. There is nothing wrong with the 4 endpoints.

## Recommended next steps

- **Skip Phases 2-5.** No endpoint code change is justified by the evidence. Doing them anyway risks breaking the working endpoints, which violates "Preserve all 9 working endpoints" in the brief.
- **Do Phase 6 (Playwright browser verification).** It will surface either (a) clean pages because the engine is currently warm, or (b) the actual cold-start UX bug, which is on warmup.js.
- **If 6 fails, fix warmup.js** (extend retry schedule + tiq:ready re-fetch hook) as the real Session 16.4 deliverable. Not the 4 handlers.
- **Then do Phase 8 (docs).**

The brief explicitly grants the 3-strike-and-skip rule; this finding triggers a "0-strike skip" on Phases 2-5 because the diagnosis shows no bug to fix.

## Sanity: I read the actual code

Per the brief's Phase 1 step 8: for each section above, the line numbers, response shapes, and engine method signatures were extracted from real reads of `src/api/main.py`, `src/api/predict_engine.py`, `data/processed/atp_calendar_2026.json`, and live curl probes against production. No claim above is a guess; the cold-start timing numbers come from Session 16.3's measured `/warm` response.
