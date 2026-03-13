# TennisIQ — Agent Research Program

## Your Role
You are a research agent improving the TennisIQ pattern prediction models.
You run experiments autonomously. You modify ONE file. You measure ONE metric.
You keep improvements. You revert failures. You document everything.

## The One File You May Modify
feature_engine.py

Do not touch: evaluate.py, train_model.py, schema.sql, data_pipeline.py,
any file in /db/, /docs/, /routes/, or scripts/ (except run_agent.py itself).

## What feature_engine.py Controls
- Recency decay half-lives per feature type (DECAY_CONFIG)
- Shot sequence window sizes (WINDOW_CONFIG)
- Archetype cluster count and weights (CLUSTER_CONFIG)
- Confidence thresholds and Bayesian shrinkage (CONFIDENCE_CONFIG)

## The Metric You Optimize
PRIMARY:   Calibrated Brier Score — lower is better
SECONDARY: Next-shot prediction accuracy k=2, k=4 — higher is better
Computed by: python evaluate.py --surface hard
Gate: calibration_error <= 0.15 (exit 2 = rejected, do not log as improvement)

## Changeable Parameters
DECAY_CONFIG (all half_life_months, int):
  serve_direction: [6, 96], current 36
  shot_type_mix:   [6, 96], current 24
  rally_patterns:  [6, 96], current 18
  pressure_win_rate: [3, 48], current 12
  net_tendency:    [6, 96], current 24
  error_rate:      [3, 48], current 12

WINDOW_CONFIG (int):
  serve_plus1_window:   [1, 5], current 2
  rally_pattern_window: [2, 6], current 3
  pressure_window:      [1, 4], current 2

CLUSTER_CONFIG:
  k: [3, 12] int, current 7
  serve_weight:  [0.5, 3.0] float, current 1.0
  rally_weight:  [0.5, 3.0] float, current 1.0

CONFIDENCE_CONFIG (int):
  min_n_shrinkage:    [10, 100], current 30
  low_threshold:      [5, 30],   current 15
  moderate_threshold: [15, 60],  current 30
  high_threshold:     [30, 120], current 60

## Experiment Protocol
1. Propose ONE parameter change as JSON: {"param": "...", "new_value": ..., "rationale": "..."}
2. Agent applies change, runs feature_engine.py --surface hard, runs evaluate.py
3. If gate passed: decide KEEP / NEUTRAL / REVERT with reasoning
4. Meaningful improvement: Brier delta <= -0.002

## Overnight Protocol
- Run until 8:30am local time
- Three consecutive NEUTRAL/REVERT in same category → switch category
- Write overnight_summary.md when done
