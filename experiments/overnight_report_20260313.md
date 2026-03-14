# TennisIQ — Overnight Pipeline Report
**Generated:** 2026-03-13 23:46

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
- **Total features:** 47
- **New Brier score (5-fold CV):** 0.2127 (+-0.0002)
- **Original baseline Brier:** 0.2544
- **Improvement from new features:** +0.0417

### Top 15 Feature Importances
  1. **rank_diff**: 0.1467
  2. **p2_recent_form**: 0.0835
  3. **p1_recent_form**: 0.0822
  4. **p2_win_rate_short_rally**: 0.0692
  5. **p1_win_rate_short_rally**: 0.0577
  6. **p2_h2h_pct**: 0.0424
  7. **p1_win_rate_far_ahead**: 0.0407
  8. **p2_win_rate_far_behind**: 0.0353
  9. **p1_h2h_pct**: 0.0344
  10. **p2_win_rate_far_ahead**: 0.0304
  11. **p2_pattern_diversity_3gram**: 0.0223
  12. **p1_aggression_index**: 0.0223
  13. **p2_avg_rally_len_serving**: 0.0218
  14. **p2_aggression_index**: 0.0213
  15. **p1_win_rate_far_behind**: 0.0186

## Phase 6: XGBoost Hyperparameter Tuning
- **Experiments run:** 495
- **KEEP / REVERT / NEUTRAL:** 2 / 492 / 1
- **Best Brier:** 0.2065
- **Total improvement vs original 0.2544 baseline:** +0.0479

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
  - Exp 3: max_depth = 5 -> Brier 0.2071 (delta -0.0010)
  - Exp 9: max_depth = 6 -> Brier 0.2065 (delta -0.0016)

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