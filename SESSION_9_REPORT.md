# Session 9 Report — AI-Powered Features

**Date:** 2026-05-06
**Commits:** f6862e49, d4041852, 02490b94, eddc0a2a

---

## Features Delivered

### 1. Upset Risk Score (Phase C)
- **Backend:** `upset_risk` object added to `POST /api/match-insight` response
  - 0-100 scale computed from: base underdog probability, Elo gap, H2H, form differential, surface advantage, attribute edges
  - Labels: Heavy favorite (<30), Low but possible (30-49), Moderate upset risk (50-74), High upset potential (75+)
  - Includes narrative detail string with specific attribute callout
- **Frontend (compare.html):** Colored circle badge (green/gold/amber/red) after prediction bar
- **Frontend (index.html):** "Upset XX" pill badge on match insight cards (top-right corner)
- **Verified:** Sinner vs Alcaraz returns score=80, "High upset potential"

### 2. Surface DNA Profile (Phase D)
- **Backend:** `GET /player/{name}/surface-dna`
  - Per-surface identity: thrives/comfortable/neutral/vulnerable
  - Attribute-aware narratives (clay: endurance/groundstroke, grass: serve/volley, hard: mental/clutch)
  - DNA classification: All-Court, Clay/Grass/Hard Court Specialist
  - Summary narrative with spread analysis
- **Frontend (player.html):** 3-card grid between Player Attributes and Play Patterns
  - Surface-colored top border, rating with +/- diff from overall, narrative text
  - Updates on surface toggle
- **Verified:** Nadal = "Clay Specialist", Djokovic = "All-Court", Sinner = "Hard Court Specialist"

### 3. Match Narrative Generator (Phase E)
- **Backend:** `POST /api/match-narrative`
  - 4-paragraph ESPN-style analysis: Setup, Key Matchup Dynamic, Surface Factor, Prediction
  - Template-based, no external API needed
  - Handles favorites, underdogs, attribute mismatches, surface edges, form streaks
- **Frontend (compare.html):** "Generate Match Analysis" button below charted attributes
  - Renders paragraphs in white card with "Match Analysis" header
  - "Regenerate Analysis" on subsequent clicks
- **Verified:** Djokovic vs Shelton returns 4 paragraphs with analyst-quality prose

---

## Production Bugs Fixed

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| surface-dna 500 on prod | `get_player_card(matched)` missing surface arg | Added explicit `"hard"` arg + try/except wrapper |
| `float() argument must be ... not 'NoneType'` | Attributes dict contains `None` values on prod (not locally) | `_attr()` helper defaults None to 50.0; filtered None surface values |

---

## Architecture Notes

- All three features live in `src/api/main.py` (no changes to modules/, scripts/, config.js, requirements.txt)
- `predict_engine.py` was modified by the user (retirement threshold, attribute proxies) — not reverted
- No new dependencies added
- Frontend uses API_URL from config.js for all fetch calls

---

## Endpoints Added

| Method | Path | Description |
|--------|------|-------------|
| GET | `/player/{name}/surface-dna` | Surface DNA profile with per-surface identity |
| POST | `/api/match-narrative` | 4-paragraph analyst-style match commentary |
| (modified) | `/api/match-insight` | Added `upset_risk` to existing response |

---

## Production URLs

- **API:** https://su7vqmgkbd.us-east-1.awsapprunner.com
- **Frontend:** https://tennisiq-one.vercel.app
