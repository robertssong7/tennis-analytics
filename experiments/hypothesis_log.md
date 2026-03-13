# TennisIQ — Hypothesis Log

Running memory across agent sessions. Most recent at top.

---

## 2026-03-12 — Baseline established

**Model**: XGBoost (n_estimators=300, max_depth=4) + sigmoid Platt scaling
**Features (16)**: 6 Elo + 5 p1 profile + 5 p2 profile
**Baseline Brier**: 0.2544 | Cal error: 0.1142

**What has been tried**:
- pressure_win_rate half_life 12→9mo: REVERT (no signal, pre-profile-join era)
- serve_direction half_life 36→24mo: NEUTRAL
- rally_pattern_window 3→4: NEUTRAL
- min_n_shrinkage 30→20: NEUTRAL
- k 7→5: NEUTRAL

**Architecture note**: Profile features (serve_wide_pct, first_serve_won,
bp_save_pct, return_win_rate, rally_win_rate) are now joined into the model.
Only 25502 of 83741 winner matches have charted profiles (LEFT JOIN, rest=0).
More charted data would directly improve signal. Until then, experiments
that change feature_engine.py parameters affect only those ~30% of matches.

**Hypotheses to test next**:
- Longer half-life for serve_direction (36→48) — serve patterns are stable
- Shorter half-life for pressure_win_rate (12→8) — clutch performance evolves fast
- Higher min_n_shrinkage (30→50) — stronger prior on low-sample players
- Raise low_threshold (15→20) — exclude more low-sample players from profiles
- Increase rally_pattern_window (3→5) — longer n-grams for baseline
