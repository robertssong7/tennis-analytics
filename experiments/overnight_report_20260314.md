# TennisIQ — Overnight Pipeline Report
**Generated:** 2026-03-14 08:30

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
- **Total matches with universal features:** 941,649
- **Surface distribution:** {'Clay': 440199, 'Hard': 394408, 'Grass': 53267, 'Carpet': 49146}

## Phase 4-5: Expanded Model Training
- **Training samples:** 1,883,298
- **Total features:** 78
- **New Brier score (5-fold CV):** 0.2123 (+-0.0002)
- **Original baseline Brier:** 0.2544
- **Improvement from new features:** +0.0421

### Top 15 Feature Importances
  1. **form_diff**: 0.1382
  2. **rank_diff**: 0.1228
  3. **pressure_gap**: 0.0847
  4. **h2h_rank_interact**: 0.0670
  5. **p2_serve_vs_p1_return**: 0.0613
  6. **fatigue_diff**: 0.0509
  7. **h2h_form_interact**: 0.0411
  8. **p1_serve_vs_p2_return**: 0.0376
  9. **p2_intensity**: 0.0300
  10. **p2_recent_form**: 0.0286
  11. **p1_intensity**: 0.0270
  12. **p2_win_rate_short_rally**: 0.0213
  13. **p1_recent_form**: 0.0213
  14. **p2_h2h_pct**: 0.0135
  15. **p1_win_rate_far_ahead**: 0.0133

## Phase 6: XGBoost Hyperparameter Tuning
- **Experiments run:** 363
- **KEEP / REVERT / NEUTRAL:** 1 / 0 / 362
- **Best Brier:** 0.2115
- **Total improvement vs original 0.2544 baseline:** +0.0429

### Best Parameters Found
  - colsample_bytree: 0.8
  - gamma: 0.0
  - learning_rate: 0.1
  - max_depth: 6
  - min_child_weight: 5
  - n_estimators: 300
  - reg_alpha: 0.0
  - reg_lambda: 1.0
  - subsample: 0.8

### Experiments That Improved the Model
  - Exp 4: max_depth = 6 -> Brier 0.2115 (delta -0.0008)

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