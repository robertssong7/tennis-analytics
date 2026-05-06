# TennisIQ — Complete Technical Handoff v3.1

**Date:** May 6, 2026 (updated from March 31, 2026)
**Author:** Robert Song + development session context (Sessions 1-9)
**Status:** Live in production, actively iterating
**Live URLs:**
- Frontend: https://tennisiq-one.vercel.app (Vercel)
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

**Session 9 (planned):** Upset Risk Score, Surface DNA Profile, Match Narrative Generator.

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
1. **parsed_points.parquet missing on prod** — Needs Git LFS. Blocks: pattern endpoint data, attribute bars on player/compare, scenario analysis, similar player computation. ~200MB file.
2. **Nadal shows Gold not Legendary** — Retired player peak rating not implemented. The Glicko rating decayed post-retirement. Should show peak Elo with "Peak: [year]" label.
3. **Vercel security posture** — April 2026 Vercel breach disclosed. See Section 16. Consider migrating frontend to AWS S3 + CloudFront.

### P1 — Important
4. **Tournament feed stuck at R32** — live_tournament.json is static, updated only when scraper runs. tennis-data.co.uk has a lag. Need a better real-time data source or more frequent scraping.
5. **Dark horse predictions need draw data** — Currently rating-based projections only. Can't do bracket-path analysis without actual tournament draws.
6. **start.sh pip install adds 2-3 min cold start** — Move to Docker-based App Runner or find a way to persist packages.
7. **Toughest matchup logic needs improvement** — Current logic is basic. Should consider: surface-specific H2H, recent form weighting, stylistic matchup analysis.

### P2 — Nice to Have
8. **React migration** — Single-file HTML pages are hard to maintain. Proposed components: PlayerCard, AttributeGrid, ConditionsPanel, ScenarioCards, MatchupList, etc.
9. **Prediction accuracy tracking** — Log predictions, measure calibration over time.
10. **Mobile optimization** — No responsive design pass done yet.
11. **WTA data** — Sackmann has tennis_wta repo, same format.

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

---

*This document is the single source of truth for TennisIQ. Read it before writing any code. Update it when things change.*
