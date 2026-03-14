# TennisIQ — Overnight Pipeline Report
**Generated:** 2026-03-12 23:56

## Phase 1: MCP Shot Parsing
- **Points parsed:** 1,222,054
- **Unique matches:** 7,160
- **Avg rally length:** 4.5 shots
- **Points with valid shot data:** 1,178,274

## Phase 2: Player Pattern Profiles
- **Players profiled:** 980
- **Profile features per player:** 28
- **Top 10 most-charted players:**
  - Roger Federer: 689 matches
  - Novak Djokovic: 540 matches
  - Rafael Nadal: 416 matches
  - Hubert Hurkacz: 304 matches
  - Daniil Medvedev: 264 matches
  - Jannik Sinner: 260 matches
  - Andy Murray: 256 matches
  - Pete Sampras: 209 matches
  - Andre Agassi: 207 matches
  - Carlos Alcaraz: 206 matches

## Phase 3: Universal Match Features

## Phase 4-5: Expanded Model Training

## Phase 6: XGBoost Hyperparameter Tuning

## What Was Added
- Shot sequence parser decoding serve direction, shot type, direction, depth, outcomes
- Per-player pattern profiles: serve tendencies, pressure behavior, rally length analysis, aggression index, shot n-gram diversity
- Universal features: surface, tournament level, rank difference, H2H record, recent form
- Expanded XGBoost model trained on full feature set
- Autonomous hyperparameter tuning via Bedrock Haiku agent loop

## Not Yet Added (Future Work)
- Court speed (courtspeed.com)
- Weather data by tournament lat/long
- Ball type
- Shot speed / spin rate / bounce height (no data source yet)
- Live tournament data ingestion
- Matchup-specific pattern analysis (player A vs player B shot tendencies)
- Score-state conditioned predictions (win prob shift when down 0-4 in set)
- Best-of-3 vs best-of-5 behavioral differences
- Set-level context (2nd set of Slam vs 2nd set of 250)

## Files Created
- `data/processed/parsed_points.parquet` — structured MCP points
- `data/processed/player_profiles.parquet` — player pattern profiles
- `data/processed/universal_features.parquet` — enriched match data
- `data/processed/expanded_training.pkl` — training matrix
- `models/hard/expanded_win_prob_model.pkl` — baseline expanded model
- `models/hard/best_expanded_win_prob_model.pkl` — best tuned model
- `experiments/expanded_baseline.json` — baseline results
- `experiments/overnight_xgb_tuning.json` — tuning results