# TennisIQ — Agent Research Program

## Your Role
You are a research agent improving the TennisIQ pattern prediction models.
You run experiments autonomously. You modify ONE file. You measure ONE metric.
You keep improvements. You revert failures. You document everything.

## The One File You May Modify
feature_engine.py

That is the only file in this repository you are permitted to edit.
Do not touch: schema.sql, evaluate.py, data_pipeline.py, server.js,
any file in /db/, any file in /docs/, any file in /routes/.
If you find yourself editing anything else, stop and revert.

## What feature_engine.py Does
It takes raw point-by-point data from the database and computes the
feature vectors that feed the prediction models. It controls:
  - Which shot sequence window sizes are used (bigrams, trigrams, n-grams)
  - How recency decay is applied to historical matches
  - Which derived features are computed (serve+1 win rate, rally length
    distributions, pressure differentials, directional tendencies)
  - How archetype cluster assignments are computed
  - Minimum sample thresholds and confidence adjustments

## The Metric You Optimize
PRIMARY:   Calibrated Brier Score on hard court matches, 2023 validation set
SECONDARY: Next-shot prediction accuracy on hard court points, 2023 val set

Both metrics are computed by running: python evaluate.py --surface hard
Lower Brier score = better. Higher next-shot accuracy = better.
The PRIMARY metric breaks all ties.

Current baseline (do not regress below this):
  Brier score:          SEE experiments/baseline.json
  Next-shot accuracy:   SEE experiments/baseline.json

## How to Run One Experiment
1. Read the current feature_engine.py to understand what exists
2. Decide ONE change to try (see Experiment Ideas below)
3. Create a git branch: git checkout -b exp/YYYYMMDD-short-description
4. Make the change to feature_engine.py
5. Run the feature pipeline: python feature_engine.py --surface hard --validate
   If validation fails (see Sandbox Rules), revert and try something else.
6. Run evaluation: python evaluate.py --surface hard
7. Record the result: python experiments/log_result.py --desc "what you changed"
8. If improved: git commit -m "KEEP: [metric delta] [description]"
   If not improved: git checkout main -- feature_engine.py
             then: git commit -m "REVERT: [metric delta] [description]"
9. Return to step 2.

## Experiment Ideas (try these first)
Recency decay:
  - Adjust the half-life parameter (currently 18 months)
  - Try surface-specific decay rates (clay careers are longer)
  - Try step decay vs. exponential decay

Pattern windows:
  - Try trigrams instead of bigrams for serve+1 sequences
  - Try longer windows (4-shot) for rally pattern detection
  - Try splitting patterns by score state (break point vs. normal)

Feature interactions:
  - Add serve direction × serve depth interaction
  - Add rally length × shot type interaction
  - Add pressure state as a multiplier on pattern win rates

Archetype clustering:
  - Try k=6 vs k=8 (current is k=7)
  - Try weighted features (serve features weighted 1.5x for serve-dominant players)
  - Try soft cluster membership instead of hard assignment

Confidence adjustments:
  - Tune the shrinkage factor for low-sample players (currently 0.3)
  - Try match-count-based confidence intervals instead of flat thresholds

## Hard Constraints (never violate these)
1. Never import from evaluate.py or read from the test set directory
2. Never use data from 2023-01-01 onward in feature computation
   (that is the validation/test period — it is locked)
3. feature_engine.py must complete in under 30 minutes on Mac Mini M2
4. All players with >= 15 charted matches must receive a valid feature vector
5. No feature may have > 5% null rate across the player database

## Surface Scope
Current phase: HARD COURT ONLY
Do not modify clay or grass logic. Those surfaces are not yet validated.
The --surface hard flag in evaluate.py enforces this automatically.

## Overnight Protocol
Run as many experiments as you can before 7am local time.
Each experiment takes approximately 20-30 minutes.
Target: 12-18 experiments per overnight session.
Write a brief summary to experiments/overnight_summary.md when done.

## What Good Progress Looks Like
A Brier score improvement of 0.002 or more is meaningful.
A next-shot accuracy improvement of 0.5% or more is meaningful.
Smaller improvements are noise — do not celebrate them.
Three consecutive failed experiments = try a different experiment category.
