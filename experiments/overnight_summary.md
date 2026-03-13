# TennisIQ — Overnight Summary (2026-03-12)

**Experiments run:** 5 (including baseline confirmation run 001)
**KEEP / REVERT / NEUTRAL:** 0 / 1 / 4

## All Experiments

| ID | Decision | Brier Δ | Description |
|---|---|---|---|
| 20260312-001 | ❌ REVERT | +0.0000 | pressure_win_rate half_life 12→9mo |
| 20260312-002 | ⚪ NEUTRAL | +0.0000 | serve_direction half_life 36→24mo |
| 20260312-003 | ⚪ NEUTRAL | +0.0000 | rally_pattern_window 3→4 shots |
| 20260312-004 | ⚪ NEUTRAL | +0.0000 | min_n_shrinkage 30→20 |
| 20260312-005 | ⚪ NEUTRAL | +0.0000 | k 7→5 archetype clusters |

## Baseline Reference
- Brier score:        0.2571
- Calibration error:  0.1247
- n_matches:          386 (2023 hard court, MCP-charted)

## Root Cause: Missing Feedback Loop

**All 5 experiments produced Brier Δ=0.0000.** This is not noise — it is a
structural gap in the current loop:

```
feature_engine.py → player_profiles table
                                          ← NOT READ HERE
train_model.py    → XGBoost on Elo only → win_prob_model.pkl
evaluate.py       → loads win_prob_model.pkl + Elo from players table
```

`feature_engine.py` writes serve %, rally patterns, pressure win rates, and
archetype clusters to `player_profiles`. But `train_model.py` ignores that
table entirely — it trains XGBoost on 6 Elo-derived columns only. So no
change to `feature_engine.py` can move the Brier score until `train_model.py`
is updated to JOIN `player_profiles` into the feature matrix.

The next_shot_acc_k2/k4 are null because `next_shot_model.pkl` is still the
DummyClassifier placeholder — it was never trained on actual rally sequences.

## Infrastructure: CONFIRMED WORKING ✅

The full loop ran cleanly end-to-end:
- feature_engine.py completes in ~90s on M2 (30-min limit: not reached)
- evaluate.py writes _last_eval.json correctly and gates on cal_error ≤ 0.15
- log_result.py reads _last_eval.json, computes Brier Δ vs baseline, appends to log.jsonl
- morning_report.py renders the table correctly (date format bug fixed)

## Recommended Next Steps (priority order)

1. **Close the win-prob loop**: Update `train_model.py` to LEFT JOIN
   `player_profiles` and add columns like `serve_wide_pct`, `first_serve_won`,
   `bp_save_pct`, `return_win_rate` to FEATURE_COLS. Expected Brier improvement:
   0.005–0.015 (based on published results for serve-stat augmented Elo models).

2. **Train a real next_shot_model**: The 1.22M points are in the DB and
   `rally_sequence` fields are populated. A simple bigram logistic regression on
   the training-period points would unlock next_shot_acc_k2/k4 and give
   feature_engine.py parameters a real secondary metric to optimize.

3. **Fix the --validate NaN gate**: 2 players always fail validation due to
   NULL `fifa_rating`. Either exclude `fifa_rating` from the numeric validation
   check or fill NULL fifa_ratings during the Elo write step.

_Generated 2026-03-12T20:48:00 — 5 experiments, 0 improvements, loop confirmed_
