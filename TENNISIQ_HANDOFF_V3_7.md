# TennisIQ — Complete Technical Handoff v3.7

**Date:** May 9, 2026 (updated from v3.6, Session 14)
**Author:** Robert Song + development session context (Sessions 1-14)
**Status:** Live in production, actively iterating
**Live URLs:**
- Frontend (Vercel): https://tennisiq-one.vercel.app
- Frontend (CloudFront): https://d3aogk1vtnp91d.cloudfront.net
- Backend API: https://su7vqmgkbd.us-east-1.awsapprunner.com (AWS App Runner)
- Repo: github.com/robertssong7/tennis-analytics
- Daily updates: github.com/robertssong7/tennis-analytics/actions

---

## 1. WHAT TENNISIQ IS

A 538-style ATP tennis analytics platform combining Glicko-2 ratings, stacked ML ensemble (XGBoost + LightGBM + logistic regression meta-model), and charted shot-by-shot data. Serves player profiles, match predictions, head-to-head comparisons, tournament intelligence, and AI-powered match narratives.

**Target users:** Tennis fans, coaches, analysts who want deeper insights than ATP.com.

**Stack:** Python/FastAPI backend, vanilla HTML/CSS/JS frontend, XGBoost/LightGBM ML, Glicko-2 ratings.

---

## 2. INFRASTRUCTURE — WHERE EVERYTHING LIVES

### Frontend: Vercel
- Project: "tennisiq" → https://tennisiq-one.vercel.app
- Static HTML/CSS/JS deploy from `frontend/public/dashboard/`
- Deploy command: `cd frontend/public/dashboard && npx vercel --prod --yes`
- Auto-deploys do NOT happen — must be manual via CLI
- **SECURITY CONCERN:** See Section 16 (Vercel Security Incident) for details and migration plan

### Backend API: AWS App Runner
- Service: tennisiq-api
- URL: https://su7vqmgkbd.us-east-1.awsapprunner.com
- Region: us-east-1 (N. Virginia)
- Runtime: Python 3.11 (App Runner managed)
- Memory: 2 GB, 1 vCPU
- Auto-deploys from `main` branch on push
- Start command: `sh start.sh` (installs pip packages then runs uvicorn on port 8080)
- Environment variables:
  - MALLOC_ARENA_MAX=2
  - CORS_ORIGINS=https://tennisiq-one.vercel.app,http://localhost:3001
- CORS: Set to `allow_origins=["*"]` in main.py (overrides env var)

### Source Control: GitHub
- Repo: robertssong7/tennis-analytics
- Branch: main (single branch — feature/phase-8-agent-loop is deprecated)
- PAT_TOKEN secret configured for GitHub Actions
- Daily update workflow: `.github/workflows/daily-update.yml` runs at 8 AM UTC

### Previous Hosting (Abandoned)
- **Railway:** Abandoned due to 512MB memory limit + $4.17 remaining credits. Project "disciplined-blessing" may still exist but is disconnected.
- **Render:** Free tier abandoned for same memory reason. Service tennisiq-api may still exist at tennisiq-api.onrender.com but is not in use.

---

## 3. COLD START WARNING

`start.sh` runs `pip3 install -r requirements.txt` on EVERY startup because App Runner's Python runtime doesn't persist build-phase packages to runtime. This adds 2-3 minutes to every cold start. The `start.sh` includes a check to skip install if packages are present, but first deploys are always slow.

**To fix permanently:** Move to Docker-based App Runner deployment where packages are baked into the image. Not done yet because source-code mode is simpler to iterate on.

---

## 4. DATA SOURCES

### Jeff Sackmann's tennis_atp (Primary)
- Location: `data/sackmann/tennis_atp/` (directly in git, NOT a submodule anymore)
- License: CC BY-NC-SA 4.0 (attribution required, non-commercial only)
- Coverage: 1968-2024, ~57 main-tour CSV files
- Converted from git submodule to direct files in Session 8 (the submodule didn't clone properly on cloud hosts)
- Contains: match results, serve/return stats (1991+), rankings, biographical data
- Does NOT contain: indoor/outdoor flag, ball type, CPI, weather, shot-by-shot

### Supplemental Data (tennis-data.co.uk)
- Location: `data/processed/supplemental_matches_2025_2026.csv`
- Coverage: Dec 2024 - Mar 2026 (~3,272 matches)
- Scraped via `scripts/scrape_atp_results.py`
- Has: winner, loser, score, tournament, date, surface, betting odds, indoor/outdoor
- Does NOT have: detailed match stats (serve %, winners, UE)
- Name mapping: 91.8% success rate. 18 manual overrides in predict_engine.py. ~50 obscure players unmapped.
- IMPORTANT: `atp_matches_2025_supplement.csv` also exists in the Sackmann directory. The glob filter EXCLUDES it (has 'supplement' in name) to prevent double-counting. The supplemental CSV in data/processed/ is the canonical source.

### Match Charting Project
- Source: github.com/JeffSackmann/tennis_MatchChartingProject
- Processed into: `data/processed/parsed_points.parquet` (~200MB)
- Coverage: ~7,200 matches, ~980 players
- Enables: serve direction, rally length, scenario analysis, pattern profiles
- **STATUS: NOT ON PRODUCTION.** File is too large for git (200MB > GitHub 100MB limit). Needs Git LFS to deploy. Without it, pattern endpoints return `available: false` and attribute bars on player/compare pages are limited.

### Other Data Files (in git)
- `data/processed/glicko2_state.pkl` (6.3MB) — 28,533 player Glicko-2 ratings
- `data/processed/player_attributes_v2.pkl` (10MB) — FIFA-style attributes
- `data/processed/matchup_grid.json` — precomputed top 100x100x3 predictions
- `data/processed/court_speed.csv` — CPI by tournament+year
- `data/processed/live_tournament.json` — static tournament feed (updated by scraper)
- `data/processed/headshot_codes.json` — ATP player image codes
- `models/ensemble/xgb_model.pkl` (3MB), `lgb_model.pkl` (9MB) — ML models
- `models/ensemble/stacked_meta.json` — logistic regression coefficients (replaced pickle)

---

## 5. CODE ARCHITECTURE

### Backend: src/api/
- `main.py` (~2000+ lines) — ALL route handlers, helper functions, startup caches
- `predict_engine.py` (~1400+ lines) — PredictEngine singleton, data loading, prediction logic
- `stacked_ensemble.py` (~20 lines) — class definition for pickle backward compat
- `config.py` (~15 lines) — reads .env for CORS, host, port
- `pattern_endpoints.py` — LIVE router mounted at /api/v2 prefix. Not dead code.

### Frontend: frontend/public/dashboard/
- `index.html` (~1200+ lines) — homepage with carousel, matchups, tournament feed
- `player.html` (~1500+ lines) — player profile with attributes, conditions, matchups
- `compare.html` (~800+ lines) — head-to-head comparison
- `tournament.html` (~600+ lines) — CPI intelligence
- `config.js` — API_URL auto-detection (localhost vs production)
- `autocomplete.js` — shared search component

### Pipeline Scripts (DO NOT MODIFY):
- `scripts/build_edge_features_v2.py` — builds 168-feature training matrix (~20 min)
- `scripts/ensemble_trainer.py` — trains XGB + LGB + stacker (~10 min)
- `scripts/precompute_matchups.py` — generates matchup_grid.json
- `scripts/scrape_atp_results.py` — scrapes tennis-data.co.uk for recent matches

### Modules (DO NOT MODIFY):
- `modules/glicko2.py` — Glicko-2 implementation (tau=0.6)
- `modules/player_attributes.py` — computes 8 FIFA attributes
- `modules/fatigue.py` — match fatigue modeling
- `modules/weather_v2.py` — weather feature engineering

---

## 6. KEY API ENDPOINTS

| Endpoint | Method | Description |
|----------|--------|-------------|
| /health | GET | Health check |
| /predict/player/{name} | GET | Full player card (rating, W/L, attributes, form) |
| /predict | POST | Match prediction (body: {player1, player2, surface}) |
| /api/match-insight | POST | Deep analysis with reasons, x-factor, upset indicators |
| /api/match-narrative | POST | Analyst-style prose commentary (template v1) |
| /player/{name}/conditions | GET | Win rates by climate/court speed/ball type |
| /player/{name}/matchups | GET | Toughest/easiest opponents (10 each) |
| /player/{name}/scenarios | GET | Pressure pattern analysis |
| /player/{name}/patterns | GET | Charted play patterns (needs parsed_points) |
| /player/{name}/similar | GET | Similar play style players |
| /player/{name}/surface-dna | GET | Surface identity profile |
| /api/player-image/{code} | GET | Headshot proxy (cached to disk) |
| /api/live-tournament | GET | Split tournament feed |
| /api/tournament-predictions | GET | Favorites + dark horses |
| /api/v2/search?q= | GET | Player name autocomplete |
| /player/{name}/outliers | GET | Top 5 percentile-extreme stats with narratives |
| /api/tournament-weather?city= | GET | Open-Meteo current + 3-day forecast |
| /api/model-accuracy?surface=&window= | GET | Back-tested accuracy + Brier on last N matches |
| /api/system-status | GET | Comprehensive health: overall + data + model + coverage + infrastructure |

---

## 7. THE ML PIPELINE

### Rating System
- Sigmoid mapping: `99 / (1 + exp(-0.004 * (elo - 1500)))`
- Form modifier: `(form_3 - 0.5) * 8.0` (continuous, +/- 4 max)
- Tiers: Legendary 91+ (#0ABAB5), Gold 80-90 (#DAA520), Silver 69-79 (#A8A9AD), Bronze <69 (#CD7F32)

### Prediction Models
- 168 features built chronologically (zero-leakage)
- XGBoost Brier: 0.1887, LightGBM: 0.1855, Stacked: ~0.1845
- Pinnacle benchmark: ~0.170-0.175

### Full Rebuild (~40 min)
```bash
python3 -u scripts/build_edge_features_v2.py
python3 -u scripts/ensemble_trainer.py --data data/processed/training_edge_v4.pkl --output models/ensemble --skip-ft
python3 -u scripts/precompute_matchups.py
```

---

## 8. WHAT HAS BEEN BUILT (Sessions 1-9)

**Session 1-5 (Mar 19-24):** Foundation, core features, data scraping, headshot proxy, surface toggle, caching, carousel, tournament feed, matchups, conditions, scenarios, similar players.

**Session 6 (Mar 25):** Pickle to JSON fix for stacked ensemble, matchup expansion 5 to 10, compare page Overall prediction (weighted 50/30/20), pattern_endpoints.py confirmed live.

**Session 7 (Mar 28):** Match insight endpoint (POST /api/match-insight) with reasons, x-factor, upset indicators. Featured player carousel card-only rendering. Today's Key Matchups frontend section. Data integrity audit with 18 name mapping overrides, H2H verified.

**Session 8 (Mar 29-30):** Homepage layout fixes, compare page attribute fallback. Win-loss record fix traced full data path, found .dockerignore excluding all Sackmann CSVs. Converted from git submodule to direct files. Fixed double-counting supplement bug. CORS set to ["*"]. Startup optimization in start.sh.

**Session 9 (May 6):** Upset Risk Score, Surface DNA Profile, Match Narrative Generator.

**Session 14 (May 9):** Live-aware homepage + Historical Trends dashboard + Stat-of-the-Day engine.
- B: parsed_points moved to S3 under `processed/` prefix; new `src/api/data_loaders.py` with boto3-based fallback. Existing urllib fallback at the bucket root still in place. Closes the long-running Git LFS blocker (file too large for git, now lazy-pulled on App Runner cold start if absent).
- C: New `tools/compute_active_players.py` derives the in-draw player set from `live_tournament.json` (which itself comes from the supplement scraper). Names normalised through predict_engine's `_build_supplemental_name_map`. Output: `data/processed/active_players.json` with both short and canonical names per tournament. Diagnosed but did not fix the upstream R32 lag — the supplement returns 2025 Italian Open data when 2026 has no rows yet, and `tools/refresh_live_tournament.py` slices results to last 20 (drops R32). See `_session14_failures.txt` for the full diagnosis.
- D: Two new endpoints in `src/api/main.py`. `/api/active-players` exposes the in-draw set + per-tournament metadata. `/api/key-matchups-live` runs each candidate match through `engine.predict()` and returns the top 6 by interest score (rating sum minus half the rating gap, descending). Both endpoints honor the rule that all numeric returns wrap in `float()` / `int()`.
- E: `tools/precompute_historical_trends.py` aggregates Sackmann across 8 metrics: matches per year, avg aces, avg double faults, avg match minutes, avg first-serve in %, avg break points saved %, ace rate (court-speed proxy, labeled honestly), and avg rally length (Match Charting Project, sparser coverage). Surface-bucketed (all/Hard/Clay/Grass/Carpet) with min-50 sample threshold. New `/api/historical-trends` and `/api/historical-trends/metrics` endpoints serve the data.
- F: New `frontend/public/dashboard/trends.html` with Chart.js 4.4.0 + chartjs-plugin-annotation. Pill controls for metric and surface, year-range inputs, era annotations as vertical reference lines, tooltip surfaces n-count, export-to-PNG button. Trends added to global nav across all 8 existing pages.
- G: Homepage (`index.html`) now fetches `/api/active-players` on load. If `active_player_count >= 4`, the carousel switches to "Currently in the Draw" ordered by current Glicko, capped at 12, with per-card tournament-context badge. Below the threshold (which is the current state — only Alcaraz "alive" in the seed data), copy reverts to "Top of the ATP" framing. Today's Key Matchups now reads from `/api/key-matchups-live`.
- H: Stat-of-the-Day deterministic engine. `tools/mine_stat_candidates.py` produces 30 candidates across 6 categories (active streak, surface specialist, age anomaly, h2h breakthrough, tournament pattern, rating jump). `tools/stat_templates.py` holds category-specific copy templates that only reference fields in the candidate facts dict. `tools/generate_stat_of_the_day.py` scores candidates with diversity + repeat penalties and picks one. Today's stat: "Jannik Sinner rolls into May 9 on a 44-match win streak" (active_streak, novelty 1.0).
- I: Stat-of-the-Day card on the homepage above today's matchups. Card matches the design system (white background, teal left border, no gradients, no emojis).
- K: Daily Action workflow extended with 4 new steps + a downstream-artifact re-commit: compute_active_players, precompute_historical_trends, mine_stat_candidates, generate_stat_of_the_day.

**Session 13 (May 9):** Operational verification + Glicko full retrain.
- B: GitHub Secrets (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, CLOUDFRONT_DIST_ID=E3V9RBJ247GXR1) confirmed present.
- C: Daily action analyzed — every run since 2026-04-30 had failed at the AWS S3 sync step ("Unable to locate credentials"). After secrets were added the manual dispatch (run 25596117962) went green end-to-end including S3 sync + CloudFront invalidate. Root cause = missing secrets, not workflow yaml.
- D: Glicko-2 fully retrained via new `tools/retrain_glicko.py` (uses modules/glicko2.py directly, single chronological pass over Sackmann + supplement, ~6 sec). Old pickle backed up to `glicko2_state_pre_session13.pkl`. Top-5 overlap before/after = 4/5 (Zverev replaced Federer; Federer is now #7). Sinner +250 mu (2470→2720) reflects 2025-26 streak that the March-24 snapshot missed. Player count went from 28,533 to 7,571 because the retrain mirrors the engine's main-tour filter (excludes quals/futures/doubles/amateur), whereas the prior pickle was built off the unfiltered glob.
- E: Production verified — `/api/system-status` reports `overall: healthy` with fresh Glicko, top players' tier + ratings sane, Nadal correctly flagged retired.
- F: 4/4 local checks PASS.

**Session 12 (May 8 evening):** Data integrity + observability + ship-readiness sweep.
- B: Data pipeline diagnosed and fixed. Daily action had been failing every day for 8+ days due to phantom MCP submodule entry in git index (no .gitmodules) and a referenced scraper script that didn't exist. Fixed both. New tools/refresh_supplement_data.py scrapes tennis-data.co.uk xlsx feed. Supplement freshness 2026-03-15 to 2026-05-03. Model history backtest sample doubled to 4,814 matches.
- C: /api/system-status expanded to comprehensive health (overall + data + model + coverage + infrastructure). New /status.html public dashboard. 8px health dot in nav.
- D: Headshot coverage expanded via Wikipedia REST API. 81 to 114 real photos covering top-30 ATP plus all major retired legends.
- E: Playwright mobile audit at 375/430/768px. Fixed nav overflow + tournament table h-scroll. 0 horizontal scroll across all tested pages and viewports.
- F: Page-specific OG metadata, favicon (T white-on-teal), apple-touch-icon.

**Session 11 (May 8):** Product-polish sweep — every phase produces a viewer-perceivable change.
- B: Headshots via S3 mirror + initials fallback (every player card has a face)
- C: Prediction depth — confidence bands, decision drivers panel, model accuracy badge
- D: Attribute display polish — (?) info icon with source attribution + coverage badge
- E: Freshness UI — /api/system-status, footer timestamps on every page
- F: Mobile responsive — full layout pass at 768/1024 breakpoints
- G: /methodology page + Open Graph + Twitter card meta + branded OG image (1200x630)

**Session 10 (May 7):** Bug-fix sweep + new features.
- B1: Date-based retirement detection (425-day threshold, datetime.now()) — Nadal/Federer/Murray correctly Legendary with rating_label="Peak: YYYY"
- B2: parsed_points eager S3 load + null-attribute graceful rendering + 2-col attributes layout
- B3: Player image proxy lowercases codes + SVG silhouette fallback (ATP behind Cloudflare challenge)
- B4: Conditions threshold lowered to 5 + low_sample badge for partial coverage
- B5: AWS migration cleanup — created tennisiq-data-assets bucket (parsed_points uploaded), tennisiq-frontend bucket, CloudFront distribution E3V9RBJ247GXR1
- C: ATP 2026 calendar (atp_calendar_2026.json) + date-driven live/just_finished/next_upcoming logic — Italian Open live, Madrid just finished, Roland Garros up next
- D: Percentile outlier engine — 17 stats × all-time ranking + "What Makes Them Different" card on player page
- E: Tournament hero on tournament.html + Open-Meteo weather endpoint + court speed slow/medium/fast badge

---

## 9. WHAT DIDN'T WORK AND WHY (FAILURE LOG)

### Deployment Failures
| Platform | Issue | Root Cause |
|----------|-------|------------|
| Railway | Out of memory | 512MB limit, API loads ~500MB at startup |
| Railway | Dockerfile not found | Was only on feature branch, not main |
| Railway | Rollback overwritten | Auto-deploy from branch kept replacing rollback |
| Render free | Out of memory at startup | Same 512MB limit, startup spike exceeds it |
| AWS App Runner | pip: command not found | Python runtime needs pip3 or python3 -m pip |
| AWS App Runner | uvicorn not found | Build/runtime are separate containers, packages don't persist |
| AWS App Runner | Quote mangling | UI mangled quotes in start command, broke sh -c |
| AWS App Runner | 502 on all endpoints | .dockerignore excluded all Sackmann CSVs |

### Data Failures
| Issue | Root Cause | Fix |
|-------|------------|-----|
| Djokovic 47-13 instead of 1186-237 | .dockerignore had `data/sackmann/` excluding all CSVs | Changed to targeted exclusions |
| Djokovic 1233-250 (double count) | atp_matches_2025_supplement.csv + supplemental CSV both loaded | Exclude 'supplement' from glob |
| Sackmann CSVs missing on cloud | Git submodule not cloned by cloud hosts | Converted to direct files |
| Federer 0-0 wins | CSV filter was 2015+ only, missed pre-2015 career | Changed to load all years |
| Nadal shows Gold not Legendary | Retired player peak rating not implemented | Open issue |
| 8% name mapping failures | tennis-data.co.uk format differs from Sackmann | 18 manual overrides added |

### Frontend Failures
| Issue | Sessions | Root Cause |
|-------|----------|------------|
| Carousel "Start API for live data" | Multiple | Old Supabase route dead, path mismatches |
| Carousel "Loading player data..." | Multiple | Patterns unavailable on prod, no fallback |
| Config.js pointing to wrong host | 3 times | Had to update Railway to Render to AWS |
| CORS errors | Multiple | Each host needed different CORS config |

---

## 10. OPEN ISSUES (Prioritized)

### P0 — Blocking
*(All Session 10 P0 items resolved.)*

### P1 — Important
1. ~~Glicko hasn't been retrained since March 24~~ — RESOLVED in Session 13 via `tools/retrain_glicko.py`. Run any time on a laptop in ~10 sec; the daily action does not yet invoke it (out of scope for Session 13). If the stacked ML ensemble itself needs retraining on fresh ratings, that still requires the heavier `scripts/build_edge_features_v2.py` + `scripts/ensemble_trainer.py` chain.
2. **Dark horse predictions need draw data** — Currently rating-based projections only. Can't do bracket-path analysis without actual tournament draws.
3. **start.sh pip install adds 2-3 min cold start** — Move to Docker-based App Runner or find a way to persist packages.
4. **Toughest matchup logic needs improvement** — Current logic is basic. Should consider: surface-specific H2H, recent form weighting, stylistic matchup analysis.
5. **Vercel sunset decision** — CloudFront frontend is live (d3aogk1vtnp91d.cloudfront.net). Once verified stable, decommission Vercel deploy and remove the dual-deploy step. (Still pending after Session 13.)
6. ~~GitHub Secrets not set~~ — RESOLVED in Session 13. AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, CLOUDFRONT_DIST_ID all present (added 2026-05-09 07:11 UTC). Daily action verified end-to-end (run 25596117962 = success).
7. **Headshot coverage at 114 of ~280 active players** — Wikipedia covers most named players but mid-tour challengers without wiki pages still show initials. Could add Tennis Explorer / ITF as fallback sources.
8. **Glicko player count dropped from 28,533 to 7,571 after Session 13 retrain** — The new state mirrors predict_engine's main-tour filter (excludes quals/futures/doubles/amateur). All active top-100 players are present; obscure quals-only names that were never queryable through the UI are gone. If a player ever 404s after this change, the cause is name-mapping, not the retrain.
9. **R32 lag in live tournament feed** — The supplement scraper either preserves only the last 20 rows of a tournament (dropping R32) or returns prior-year results when the current year's data hasn't arrived yet. Diagnosed in Session 14 Phase C; left in place because the active-player computation derives "alive" status from match-completion data regardless of round labels. Future session can fix by removing the `[-20:]` slice in `tools/refresh_live_tournament.py`. Today's Italian Open seed data is from 2025.
10. **Stat-of-the-day rendering today shows Sinner 44-match streak** — That number is inflated by duplicate rows in the supplement CSV (each match recorded twice, sometimes more). The directional finding (Sinner is on a major run) is correct; the integer is over-counted by ~2x. Same root cause affects h2h_breakthrough and tournament_pattern miners. Dedupe the supplement at scrape time as a future fix.

### P2 — Nice to Have
7. **React migration** — Single-file HTML pages are hard to maintain. Proposed components: PlayerCard, AttributeGrid, ConditionsPanel, ScenarioCards, MatchupList, etc.
8. **Prediction accuracy tracking** — Log predictions, measure calibration over time. Currently we back-test pure Elo daily (lower bound); the production stacked ensemble's accuracy is not yet tracked.
9. **WTA data** — Sackmann has tennis_wta repo, same format.

### P3 — Future Features (Designed, Not Built)
12. **Career trajectory charts** — Elo over time + ranking overlay on dual-axis Chart.js. Needs historical Elo computation at runtime (lazy, per-player).
13. **What If Simulator** — New page (simulator.html). Override surface/weather/form/court speed to see how predictions shift. Needs predict_with_overrides() backend method.
14. **Match Narrative Generator** — Template-based v1 built. Upgrade path: Claude API for natural prose generation (needs ANTHROPIC_API_KEY env var).
15. **Dream match generator** — Peak Federer vs current Alcaraz simulations using historical peak ratings.
16. **Upset Risk Score** — 0-100 formula-based score from match-insight data. Designed with 5 adjustment factors (Elo gap, H2H, form, surface, attributes).
17. **Surface DNA Profile** — Per-surface identity analysis. Endpoint designed: GET /player/{name}/surface-dna. Three colored cards showing surface-specific ratings, narrative, and player identity type.

---

## 11. DESIGN SYSTEM

| Element | Value |
|---------|-------|
| Page background | #F5F0EB |
| Text color | #2C2C2C |
| Card background | #FFFFFF |
| Card shadow | 0 2px 8px rgba(0,0,0,0.08) |
| Card border-radius | 12px |
| Heading font | Playfair Display |
| Body font | DM Sans |
| Accent (Legendary) | #0ABAB5 |
| Gold tier | #DAA520 |
| Silver tier | #A8A9AD |
| Bronze tier | #CD7F32 |
| Hard court | #4A90D9 |
| Clay court | #D4724E |
| Grass court | #5AA469 |
| Bar track | #D0C9C0, height 8px |
| Tooltip | #2C2C2C bg, white text, 12px DM Sans |
| No emojis | Flags acceptable |
| No gradients on cards | |
| No radar/diamond charts | Horizontal bars only |
| "Explore the Dashboards" | ALWAYS last section before footer |

---

## 12. TIPS AND LESSONS LEARNED

### Claude Code Prompts
- Be ABSURDLY specific. "Change background to #D0C9C0, add inset shadow" succeeds. "Make it look better" fails.
- Include exact colors, sizes, font specs, behaviors.
- Split into phases with commits between each.
- Anti-stall rules prevent Claude Code from modifying pipeline files.
- Always include "Re-read files before editing" — editing from memory causes bugs.

### Deployment
- **Cloud hosts only have what's in git.** Any file in .gitignore or .dockerignore does NOT exist on production. This is the #1 deployment debugging principle.
- **Git submodules don't work on cloud hosts.** Convert to direct files.
- **App Runner Python runtime splits build and runtime containers.** Packages installed during build don't persist. Use start.sh to install at runtime (slow but works).
- **Always test with the actual production URL before declaring something "deployed."**
- **CORS must include the frontend origin.** Or set to ["*"] for public APIs.

### Data
- Sackmann is the gold standard. His data has ~16 more wins than ATP official for Djokovic — the difference is Davis Cup/Olympics/team events that ATP counts separately.
- The supplemental scraper maps "Sinner J." to "Jannik Sinner" with 91.8% accuracy. Failures are hyphenated names, name changes, and obscure challengers.
- Double-counting happens when both Sackmann's supplement file AND the scraped supplement are loaded. The glob filter must exclude files with 'supplement' in the name.

### Git
- Use `/usr/bin/git` for push/pull on macOS — Homebrew git has a curl library crash.
- Regular `git` works for add, commit, status, diff.

---

## 13. ACCOUNTS AND CREDENTIALS

| Service | Details |
|---------|---------|
| GitHub | robertssong7/tennis-analytics |
| AWS | Account 3025-2462-9522, App Runner in us-east-1, $140 credits (exp Mar 2027) |
| Vercel | "tennisiq" project under Robert's account |
| Railway | "disciplined-blessing" — DEPRECATED, disconnect |
| Render | tennisiq-api — DEPRECATED, can delete |

### Secrets
- GitHub: PAT_TOKEN (repo scope, 1-year expiry from Mar 2026) — used by GitHub Actions daily update
- AWS: App Runner env vars (MALLOC_ARENA_MAX, CORS_ORIGINS)
- No API keys stored in Vercel (frontend is static, no secrets)

---

## 14. HOW TO DEPLOY

### Frontend Change Only
```bash
cd frontend/public/dashboard && npx vercel --prod --yes
```

### Backend Change
```bash
git add -A && git commit -m "description"
/usr/bin/git push origin main
# AWS App Runner auto-deploys. Wait 5-10 min.
```

### Full Stack
```bash
git add -A && git commit -m "description"
/usr/bin/git push origin main
cd frontend/public/dashboard && npx vercel --prod --yes
# Wait for AWS rebuild
```

### Verify Production
```bash
curl -s https://su7vqmgkbd.us-east-1.awsapprunner.com/health
curl -s "https://su7vqmgkbd.us-east-1.awsapprunner.com/predict/player/Sinner" | python3 -m json.tool | head -10
```

---

## 15. DATA ATTRIBUTION (REQUIRED)

- **Sackmann data:** "Tennis databases by Jeff Sackmann / Tennis Abstract" — CC BY-NC-SA 4.0
- **Match Charting Project:** Same license
- **tennis-data.co.uk:** Free for non-commercial use
- **These licenses mean TennisIQ cannot be monetized directly without renegotiating data rights.**

---

## 16. SECURITY: VERCEL BREACH (April 2026)

### What Happened
On April 20, 2026, Vercel disclosed a security breach via a compromised third-party AI tool (Context.ai). An attacker gained access to some Vercel internal systems and a "limited subset" of customer environment variables not marked as "sensitive." Source: https://thehackernews.com/2026/04/vercel-breach-tied-to-context-ai-hack.html

ShinyHunters claimed responsibility and listed stolen data for $2 million. Vercel is working with Mandiant and law enforcement.

### Our Exposure Assessment
**Risk: LOW but non-zero.**

What we have on Vercel:
- Static HTML/CSS/JS files only (no server-side code runs on Vercel)
- No API keys, database credentials, or secrets stored as Vercel env vars
- config.js contains only the public AWS API URL (not a secret)
- No Google Workspace integration with Vercel

What is NOT on Vercel:
- Backend code (on AWS)
- ML models, data files, credentials (on AWS)
- GitHub tokens (in GitHub Secrets, not Vercel)

### Actions Taken
1. Reviewed Vercel activity log for suspicious deployments — none found
2. Checked Google account for malicious OAuth app ID `110671459871-30f1spbu0hptbs60cb4vsmv79i7bbvqj.apps.googleusercontent.com` — not present
3. Rotated Vercel CLI authentication
4. Verified GitHub PAT_TOKEN has not been used suspiciously

### Recommended Next Steps
1. **Check Vercel activity log periodically** — vercel.com, project dashboard, activity tab
2. **If Vercel contacts you about compromised credentials**, rotate everything immediately
3. **Monitor for suspicious deployments** — any deployment you didn't trigger is a red flag
4. **Consider migrating frontend to AWS S3 + CloudFront** — consolidates all infrastructure on AWS, removes Vercel dependency, covered by existing AWS credits. The frontend is pure static files, so this is a straightforward migration. Steps:
   - Create S3 bucket with static website hosting enabled
   - Upload `frontend/public/dashboard/` contents to S3
   - Create CloudFront distribution pointing to S3
   - Optional: attach custom domain
   - Update GitHub Actions to deploy to S3 instead of Vercel
   - Estimated effort: 1-2 hours
5. **Alternatively, use GitHub Pages** — free, no third-party dependency, but lacks custom headers and redirects. Works fine for pure static HTML.

### If Migrating to S3 + CloudFront (Claude Code prompt ready)
The migration can be fully automated by Claude Code. It would:
- Create an S3 bucket via AWS CLI (CloudShell or local)
- Configure static website hosting
- Create a CloudFront distribution
- Upload frontend files
- Update the GitHub Actions daily-update workflow to sync frontend to S3
- Update any hardcoded Vercel URLs in the codebase

This keeps the entire stack on AWS (App Runner for backend, S3+CloudFront for frontend, GitHub Actions for CI/CD).

---

## 17. FUTURE ARCHITECTURE VISION

### Current State
```
GitHub (source) → AWS App Runner (API) ← Vercel (frontend) → Browser
                                          ↑
                              GitHub Actions (daily scraper)
```

### Recommended Target State
```
GitHub (source) → AWS App Runner (API) ← AWS S3 + CloudFront (frontend) → Browser
                                          ↑
                              GitHub Actions (daily scraper)
```

Benefits: Single cloud provider, unified billing against AWS credits, no third-party security exposure for frontend hosting, no Vercel dependency.

**Session 10 status:** S3 (`tennisiq-frontend`) + CloudFront (E3V9RBJ247GXR1 → d3aogk1vtnp91d.cloudfront.net) both live and synced. Vercel remains live in parallel; sunset pending verification.

---

## 18. AWS DATA BUCKET (Session 10)

`tennisiq-data-assets` — public-read S3 bucket for runtime data the API can lazy-fetch on cold start.

Currently contains:
- `parsed_points.parquet` (21MB) — Match Charting Project shot-by-shot data. Downloaded at module import via `_ensure_parsed_points()` in `predict_engine.py`. Without it, pattern endpoints + footwork/volley proxies degrade.

URL pattern: `https://tennisiq-data-assets.s3.us-east-1.amazonaws.com/<file>`

To upload a new file:
```bash
aws s3 cp data/processed/parsed_points.parquet s3://tennisiq-data-assets/parsed_points.parquet
```

The eager-load is idempotent and cached: once the file is on the App Runner local disk, no S3 calls happen on subsequent requests.

---

## 19. PERCENTILE OUTLIER ENGINE (Session 10)

**Source:** `tools/compute_percentiles.py` aggregates 17 career stats from Sackmann CSVs for every player with ≥20 main-tour matches. Output: `data/processed/percentile_rankings.json` (~14MB, ~1857 qualifying players).

**Stats computed:**

Match-result (no serve/return data needed):
- `tiebreak_win_rate` — assumes match winner won all tiebreaks (Sackmann doesn't break out per-tb winner; biased toward winners but consistent across population)
- `deciding_set_wr` — 3rd set in BO3 / 5th in BO5
- `three_set_wr` — BO3 going the distance, win rate
- `vs_top10_wr`, `vs_top20_wr` — opponent rank ≤ 10 / 20 at match time
- `comeback_rate` — wins after losing 1st set / matches with lost 1st set
- `first_set_winner_conv` — wins after winning 1st set / matches with won 1st set
- `bagels_per_match`, `bagels_conceded_per_match` — 6-0 sets

Serve/return (1991+ when Sackmann has stats):
- `hold_pct`, `break_pct`
- `bp_save_pct`, `bp_convert_pct`
- `first_serve_win_pct`, `second_serve_win_pct`
- `aces_per_match`, `df_per_match`

**Refresh cadence:** Run manually after Sackmann data updates. Recommended addition to `.github/workflows/daily-update.yml`:
```yaml
- name: Recompute percentile rankings
  run: python3 tools/compute_percentiles.py
```

**Endpoint:** `GET /player/{name}/outliers` returns the 5 most extreme percentile rankings (≥90 or ≤10) sorted by `abs(percentile - 50)` descending. Each entry includes value, sample_size, percentile, rank, total_qualifying, direction, and an ESPN-style narrative ("#1 of 864 qualifying players in Tiebreak Win Rate (77.7, n=404)").

For lower-is-better stats (DFs, bagels conceded), the percentile is inverted so 99th percentile always means "best in stat".

**Frontend:** "What Makes [First Name] Different" section on player.html, between Surface DNA and Play Patterns. Five horizontal cards with bar fill (teal for top, red for bottom).

---

## 20. ATP 2026 CALENDAR (Session 10)

`data/processed/atp_calendar_2026.json` — single source of truth for the 2026 tour calendar. 14 entries: 4 Slams, 9 Masters 1000, 1 ATP Finals.

Each entry: `{name, category, city, country, surface, indoor, start, end, draw_size}` with ISO dates.

**Helpers** in `src/api/main.py`:
- `get_live_tournament(today)` — returns calendar entry where today ∈ [start, end]
- `get_just_finished(today)` — most recent Masters/Slam/Finals that ended
- `get_next_upcoming(today)` — soonest upcoming start

**Endpoints driven by calendar:**
- `GET /api/live-tournament` — returns `{live, just_finished, next_upcoming}` plus backward-compat `{finished, current}` for older frontends
- `GET /api/tournament-predictions` — auto-switches to live tournament's surface (Italian Open clay → Alcaraz top favorite)

**Daily refresh:** `tools/refresh_live_tournament.py` rebuilds `data/processed/live_tournament.json` from calendar + supplemental match results. Wired into the daily GH Actions workflow.

**Maintenance:** Update the JSON file at year-end with the next year's dates. Ideally automated by scraping ATP tour schedule.

---

## 21. WEATHER INTEGRATION (Session 10)

`GET /api/tournament-weather?city=<city>` — Open-Meteo current + 3-day forecast. No API key required; free for non-commercial use.

**City lookup:** `TOURNAMENT_GEO` dict in `main.py` maps city → (lat, lon) for the 13 calendar cities.

**Cache:** 1-hour in-memory TTL keyed by city. Open-Meteo is free but rate-limited.

**Court speed badge:** `get_court_speed_label(cpi_base, weather)` combines per-city baseline CPI with weather adjustments:
- temp_c > 28 → +3 CPI (faster ball flight)
- humidity > 75% → -3 CPI (slower)
- wind_kmh > 25 → +2 CPI

Returns `{label: Slow|Medium|Fast, cpi, color}`. Cutoffs: <35 Slow (clay-orange), 35-45 Medium (gold), ≥45 Fast (hard-blue).

**Surfaced via:** `/api/live-tournament` includes `weather` and `court_speed` keys on `live` and (CPI only) `just_finished` entries. The tournament.html hero card renders the badge and an expandable conditions panel (Today / 3-Day Forecast / Court Speed gauge).

---

## 22. HEADSHOTS (Session 11)

**Where to get one when the carousel asks for it:**
1. Local disk cache `data/processed/headshots/<code>.png` — fast path on a warm App Runner container, ships in the deploy
2. S3 mirror `https://tennisiq-data-assets.s3.us-east-1.amazonaws.com/headshots/<code>.png` — populated by `aws s3 sync data/processed/headshots/ s3://tennisiq-data-assets/headshots/` once
3. ATP CDN `https://www.atptour.com/-/media/alias/player-headshot/<code>` — almost always 403 due to Cloudflare bot challenge, kept for completeness
4. Branded initials SVG via `_make_initials_svg(player_name)` — 5-color palette (teal/blue/clay/grass/gold), deterministic background from the name hash, Playfair initials. Looks intentional.

**Cache-Control:** `public, max-age=2592000` (30 days). CloudFront and browsers will not refetch on every page view.

**Adding new headshots:** Drop the PNG at `data/processed/headshots/<code>.png` (lowercase code matches the URL fragment in `data/player_headshots.json`), then `aws s3 cp ... s3://tennisiq-data-assets/headshots/`. Both tier 1 and tier 2 fill the request.

---

## 23. PREDICTION DEPTH (Session 11)

**Confidence band** (`_confidence_band`): looks up the trailing-365-day Brier score on the surface bucket from `model_history.json`, derives a ±pp band capped at 15 percentage points. Surfaced as the small "±15pp" string next to the win probability with explanation tooltip.

**Drivers** (`_build_drivers`): top 3 numerical drivers signed in favor of the predicted winner. Pulls from rating gap (Elo×0.11), surface fit (×0.7), H2H, recent form (last 3 matches × 20), and any attribute mismatch ≥ 8 points. Displayed as a stack of `▲ +6.2pp` rows.

**Model accuracy** (`/api/model-accuracy?surface=&window=`): reads `data/processed/model_history.json` produced by `tools/compute_model_accuracy.py`. Pure-Elo back-test on the trailing 365 days — conservative lower bound vs the production stacked ensemble. Refreshed daily by GH Actions.

---

## 24. MOBILE RESPONSIVE (Session 11)

Two breakpoints across all pages:
- **1024px (tablet):** multi-column grids collapse to 1fr, hero stacks vertically, control rows wrap
- **768px (mobile):** nav search hidden, font-size scales down (28-32px headings, 14px body), all grids 1fr, footer stacks center-aligned

Each page (`index.html`, `player.html`, `compare.html`, `tournament.html`, `methodology.html`) has its own page-specific block at the bottom of the inline `<style>` tag. The viewport meta is `width=device-width, initial-scale=1.0, viewport-fit=cover`.

---

## 25. METHODOLOGY PAGE + OG META (Session 11)

`frontend/public/dashboard/methodology.html` — 10-section explanation of the system. Linked from every footer. Update whenever the model changes substantively.

**Open Graph + Twitter cards** on all pages: `og:title`, `og:description`, `og:image`, `twitter:card=summary_large_image`. Image is `og-image.png` (1200x630) generated by `tools/generate_og_image.py` (PIL with system-font fallback chain). Regenerate by running the script; Vercel/CloudFront will serve the new file on next deploy.

**OG validator:** paste the homepage URL into <https://www.opengraph.xyz/> to confirm meta tags are detected.

---

## 26. DATA PIPELINE INTEGRITY (Session 12)

The daily refresh chain must be working for any user-visible freshness signal to be honest. Failure modes diagnosed in Session 12:

**Root cause 1: phantom git submodule.** `data/sackmann/tennis_MatchChartingProject` was registered in git's index as mode 160000 but no `.gitmodules` existed to define its URL. Every GH Actions checkout step with `submodules: true` failed at "No url found for submodule path". 8+ daily runs failed at checkout.

**Fix:** `git rm --cached data/sackmann/tennis_MatchChartingProject` and added the directory to `.gitignore`. The MCP raw files aren't read at runtime; the API uses `parsed_points.parquet` which is on S3. Set `submodules: false` in the action.

**Root cause 2: missing scraper.** The action invoked `scripts/scrape_atp_results.py` which did not exist. Replaced with `tools/refresh_supplement_data.py` which scrapes tennis-data.co.uk's per-year xlsx files (`http://www.tennis-data.co.uk/2026/2026.xlsx`), normalizes to the supplement schema, dedupes and merges.

**Daily refresh chain (in order, runs at 08:00 UTC):**
1. Pull latest Sackmann data (skipped if not a git repo locally — Sackmann is direct files now)
2. `tools/refresh_supplement_data.py` — fetches ~3,500 new rows per run during active season
3. `tools/refresh_live_tournament.py` — rebuilds `live_tournament.json` from supplement
4. `tools/compute_percentiles.py` — back-aggregates 17 stats (~3 min)
5. `tools/compute_model_accuracy.py` — backtests trailing 365 days (~1 min)
6. Commit + push, S3 sync, CloudFront invalidate

**How to verify it's working:**
```bash
gh run list --workflow=daily-update.yml --limit 5
# All recent runs should show 'success'
curl -s https://su7vqmgkbd.us-east-1.awsapprunner.com/api/system-status | jq '.data'
# matches_through_age_days should be <= 7
```

**Per-player last_match_date patching.** Glicko's pickled state is from the last full retrain (March 24, 2026). To keep retirement detection current without retraining the heavy ML pipeline, `predict_engine.py` now patches `glicko.ratings[player]['all'].last_match_date` in-memory from the supplement on every engine load. This is documented in Session 12's commit B and is what makes Sinner show "last_match: 2026-05-03" despite the pickle being 6 weeks old.

---

## 27. SYSTEM STATUS DASHBOARD (Session 12)

`/api/system-status` returns a comprehensive health rollup: `overall` (healthy/degraded/stale), plus four sub-objects:

- `data` — match age, glicko age, supplement scrape age, percentile compute time, model history compute time
- `model` — Brier scores by surface, accuracy on last 100 by surface, sample size in backtest
- `coverage` — active player count, headshot coverage %, percentile-qualified player count
- `infrastructure` — API uptime, S3 reachability, last GH action status + time

Overall is computed from 3 critical signals: `matches_age <= 7 days`, `model_history fresh`, `S3 data assets reachable`. All three pass = healthy. Two pass = degraded. Less = stale.

**`/status.html`** renders this as a public dashboard with color-coded freshness rows and a live overall badge. Linked from every footer and from a small 8px health dot in the navigation. The dot's color reflects `overall` and tooltip explains.

**Editing the health threshold.** In `src/api/main.py` the `_is_fresh()` helper takes hours; the `overall` rollup uses 7 days for matches and 48 hours for model_history. Adjust if thresholds need to be tightened for a more demanding production posture.

---

## 28. HEADSHOT COVERAGE (Session 12)

Multi-source scraper at `tools/expand_headshot_coverage.py`. Currently uses Wikipedia REST API as the only source (ATP CDN blocked by Cloudflare; ITF and Tennis Explorer not yet wired).

**Wikimedia User-Agent policy.** Generic browser User-Agent strings get aggressively rate-limited (HTTP 429). The scraper sets a meaningful UA with contact info per <https://meta.wikimedia.org/wiki/User-Agent_policy>. 2-second sleep between successful pulls.

**Code synthesis.** When ATP doesn't have a canonical 4-character code for a player (or we don't know it), the scraper synthesizes one from the player's name hash. The proxy regex is `^[a-zA-Z0-9]{2,10}$` so synthesized codes pass.

**Output:** PNG files at `data/processed/headshots/<code>.png` (lowercase). Sync to S3 with:
```bash
aws s3 sync data/processed/headshots/ s3://tennisiq-data-assets/headshots/ --include "*.png"
aws cloudfront create-invalidation --distribution-id E3V9RBJ247GXR1 --paths "/api/player-image/*"
```

**Coverage verification:** `/api/system-status` exposes `coverage.headshot_coverage_pct`. Active player count (last_match within 14 months, 20+ matches) is the denominator.

**Quality control.** The proxy serves files only if size > 500 bytes. Wikipedia thumbnails average 90-120KB. Files in the 1-5KB range are usually placeholder/rate-limit responses; the scraper rejects these via PIL re-encode validation (must succeed) and `_save_png` requires the result is > 5KB.

---

## 29. GLICKO RETRAIN (Session 13)

`tools/retrain_glicko.py` — standalone Glicko-2 rebuild that does not touch the ML feature matrix or ensemble models. Imports `Glicko2RatingSystem` from `modules/glicko2.py` directly. Loads main-tour Sackmann CSVs (excluding qual/futures/doubles/amateur/supplement) plus the supplemental CSV mapped through the same `_build_supplemental_name_map` logic predict_engine uses. Single chronological pass, snapshot then record_result for every match. Runs in ~10 seconds on M3 Pro for ~200K matches.

**When to run:** any time the supplement has caught significant new matches and you want predictions to reflect them. Backup is automatic only if you copy first; the script will overwrite `data/processed/glicko2_state.pkl` without prompting:
```bash
cp data/processed/glicko2_state.pkl data/processed/glicko2_state.pkl.bak
python3 tools/retrain_glicko.py
```

**What it does NOT do:** retrain the XGBoost / LightGBM stacked ensemble. The feature matrix used to train those models was generated before the retrain and still embeds the prior Glicko trajectory. The ensemble continues to predict using the old features at inference time, but the `rating_all` field exposed via `/predict/player/{name}` and used in the homepage carousel ordering does pick up the new ratings. Full pipeline retrain still requires `scripts/build_edge_features_v2.py` + `scripts/ensemble_trainer.py`.

**Sanity check:** `python3 -c "import pickle; s=pickle.load(open('data/processed/glicko2_state.pkl','rb')); r=s.ratings; print(sorted(((n, d['all'].mu) for n,d in r.items() if 'all' in d), key=lambda x: x[1], reverse=True)[:5])"` should always show Sinner, Alcaraz, Djokovic, then a mix of Zverev/Soderling/Federer in the next two slots. If your top-5 doesn't look like that, restore from backup and investigate.

---

## 30. ACTIVE PLAYER PIPELINE (Session 14)

`tools/compute_active_players.py` reads `data/processed/live_tournament.json` and computes the set of players currently alive in the live ATP tournament(s). A player is "alive" if they appear as a winner but never as a loser in the latest scraped results. Output: `data/processed/active_players.json` with `all_active_players`, per-tournament `active_players` (canonical names), and `active_players_short` (the tennis-data.co.uk format).

Names are mapped through `predict_engine._build_supplemental_name_map`. Daily action runs this after `refresh_live_tournament.py` finishes. The endpoint `/api/active-players` exposes the result.

**Edge case:** when the live tournament has 0 or 1 surviving players (current state with seed data), the homepage falls back to "Top of the ATP" with the static FP_STATIC list. Any time count >= 4, the carousel switches to "Currently in the Draw."

---

## 31. HISTORICAL TRENDS DASHBOARD (Session 14)

`tools/precompute_historical_trends.py` aggregates Sackmann across 8 metrics by year and surface. Run nightly by the daily action. Output: `data/processed/historical_trends.json`.

Metrics that work:
- matches_per_year (1968+)
- avg_aces_per_match (1991+, serve stats added)
- avg_double_faults_per_match (1991+)
- avg_match_minutes (1991+)
- avg_first_serve_pct (1991+)
- avg_break_points_saved_pct (1991+)
- ace_rate_proxy_court_speed (1991+, labeled is_proxy=true so the frontend shows "proxy for surface speed, not the ITF Court Pace Index")
- avg_rally_length (Match Charting Project, sparser, 2000+)

Each metric has `by_surface: { all, Hard, Clay, Grass, Carpet }`. Min sample threshold: 50 matches per year-surface bucket. Annotations array carries 8 era-defining events (tiebreak intro, yellow ball, ATP Tour formed, Wimbledon slowed, Hawk-Eye, Plexicushion, serve clock, COVID).

Frontend page: `frontend/public/dashboard/trends.html`. Chart.js 4.4.0 line chart with annotation plugin, pill-button metric/surface selectors, year-range inputs, export-to-PNG. Linked from global nav across all pages.

---

## 32. STAT-OF-THE-DAY ENGINE (Session 14)

Three-stage pipeline, all deterministic, no LLM:

**Stage 1 — `tools/mine_stat_candidates.py`** produces 30 candidates across 6 categories. Each candidate has a `category`, `facts` dict (only the fields the templates reference), and `novelty_score` in [0,1]. Categories:
- `active_streak`: current N-match win streaks (N >= 8). Uses Sackmann + supplement combined. Last match must be within 90 days.
- `surface_specialist`: largest cross-surface Glicko mu gaps (>= 200 points) among active top-50.
- `age_anomaly`: 36+ year-old wins in past 365 days (Sackmann only — supplement has no winner_age).
- `h2h_breakthrough`: first-ever H2H win after 3+ losses, in past 60 days.
- `tournament_pattern`: career match-win totals at a single tournament, milestone reached in past 30 days.
- `rating_jump`: current Glicko mu vs `peak_elo.json`, gap >= 80 points either direction. Substitute for the weekly snapshot history we don't currently store.

**Stage 2 — `tools/stat_templates.py`** holds one function per category that takes the facts dict and returns `(headline, body)`. ESPN/538 voice. Templates only reference fields in the facts dict — they never invent numbers. Body is at most three sentences. No em dashes, no emojis.

**Stage 3 — `tools/generate_stat_of_the_day.py`** scores each candidate as `novelty * diversity_penalty * repeat_penalty`:
- 0.2x penalty if same category as yesterday
- 0.5x penalty if same category in past 7 days
- 0.3x penalty if same player + category in past 14 days

Picks the highest-scoring candidate, applies the template, writes `data/processed/stat_of_the_day.json`, appends to `data/processed/stat_history.json` (capped at 100 entries).

Endpoint: `/api/stat-of-the-day`. Homepage section: white card with teal left border above the matchups grid. Section auto-hides on render=false.

**Future:** an LLM editorial layer (Anthropic API) could rewrite the templated body for variety while the templates ensure the numbers stay grounded. Twitter/X auto-posting is similarly a future addition. Both are deferred until the deterministic version reveals gaps that warrant them.

---

*This document is the single source of truth for TennisIQ. Read it before writing any code. Update it when things change.*
