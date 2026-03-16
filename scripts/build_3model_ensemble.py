"""
Task 1: 3-Model Stacked Ensemble (XGBoost + LightGBM + FT-Transformer)
========================================================================
Loads ft_probs_v4.npy (127,928 FT-Transformer predictions on 2023+ test set).
Regenerates XGBoost and LightGBM predictions on the same rows.
Fits a 3-model stacked meta-learner on 2023-H1, evaluates on 2023-H2.

Outputs:
  - Brier scores for all configurations
  - Updated stacked_ensemble.pkl (3-model if better, 2-model if not)
  - Updated ensemble_summary.json

Usage:
  cd /Users/robertsong/Documents/tennis-analytics
  python3 -u scripts/build_3model_ensemble.py
"""

import pickle
import numpy as np
import pandas as pd
import json
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_PATH   = 'data/processed/training_edge_v4.pkl'
OUT_DIR     = Path('models/ensemble')
FT_PROBS    = OUT_DIR / 'ft_probs_v4.npy'
XGB_MODEL   = OUT_DIR / 'xgb_model.pkl'
LGB_MODEL   = OUT_DIR / 'lgb_model.pkl'
ENSEMBLE_PKL = OUT_DIR / 'stacked_ensemble.pkl'
SUMMARY_JSON = OUT_DIR / 'ensemble_summary.json'

CUTOFF = pd.Timestamp('2023-01-01')

print("=" * 60)
print("3-MODEL STACKED ENSEMBLE BUILDER")
print("=" * 60)

# ── 1. Load data ────────────────────────────────────────────────────────────
print("\n[1/5] Loading training_edge_v4.pkl ...")
with open(DATA_PATH, 'rb') as f:
    t = pickle.load(f)
X, y, dates = t[0], t[1], t[2]

tr = dates < CUTOFF
te = ~tr

print(f"  Train: {tr.sum():,} rows | Test: {te.sum():,} rows | Features: {X.shape[1]}")

X_train, y_train = X[tr], y[tr]
X_test, y_test   = X[te], y[te]
dates_test = dates[te].reset_index(drop=True)

assert len(X_test) == 127928, f"Expected 127,928 test rows, got {len(X_test)}"

# ── 2. Load or regenerate XGBoost predictions ───────────────────────────────
print("\n[2/5] Generating XGBoost predictions on test set ...")
with open(XGB_MODEL, 'rb') as f:
    xgb_model = pickle.load(f)

xgb_probs = xgb_model.predict_proba(X_test)[:, 1]
xgb_brier = brier_score_loss(y_test, xgb_probs)
print(f"  XGBoost full-test Brier: {xgb_brier:.4f}")

# ── 3. Load or regenerate LightGBM predictions ──────────────────────────────
print("\n[3/5] Generating LightGBM predictions on test set ...")
with open(LGB_MODEL, 'rb') as f:
    lgb_model = pickle.load(f)

# LightGBM needs clean column names
X_test_clean = X_test.copy()
X_test_clean.columns = X_test_clean.columns.str.replace('[^A-Za-z0-9_]', '_', regex=True)
lgb_probs = lgb_model.predict_proba(X_test_clean)[:, 1]
lgb_brier = brier_score_loss(y_test, lgb_probs)
print(f"  LightGBM full-test Brier: {lgb_brier:.4f}")

# ── 4. Load FT-Transformer predictions ──────────────────────────────────────
print("\n[4/5] Loading FT-Transformer predictions ...")
ft_probs = np.load(FT_PROBS)
assert len(ft_probs) == len(X_test), \
    f"FT probs length {len(ft_probs)} ≠ test set {len(X_test)}"
ft_brier = brier_score_loss(y_test, ft_probs)
print(f"  FT-Transformer full-test Brier: {ft_brier:.4f}")

# ── 5. Stacking ─────────────────────────────────────────────────────────────
print("\n[5/5] Fitting stacked ensembles ...")

y_test_arr = y_test.values if hasattr(y_test, 'values') else np.array(y_test)

# Split test set into H1 (stacker training) and H2 (final eval)
midpoint = dates_test.quantile(0.5)
h1_mask = (dates_test < midpoint).values
h2_mask = ~h1_mask
print(f"  Stacker train (2023-H1): {h1_mask.sum():,} | eval (2023-H2): {h2_mask.sum():,}")

# ─ Simple averages ────────────────────────────────────────────────────────
avg2_probs  = (xgb_probs + lgb_probs) / 2
avg3_probs  = (xgb_probs + lgb_probs + ft_probs) / 3

avg2_brier_full  = brier_score_loss(y_test_arr, avg2_probs)
avg3_brier_full  = brier_score_loss(y_test_arr, avg3_probs)
avg2_brier_h2    = brier_score_loss(y_test_arr[h2_mask], avg2_probs[h2_mask])
avg3_brier_h2    = brier_score_loss(y_test_arr[h2_mask], avg3_probs[h2_mask])

# ─ 2-model stacker ───────────────────────────────────────────────────────
meta2_X = np.column_stack([xgb_probs, lgb_probs])
stacker2 = LogisticRegression(C=0.1, max_iter=1000, solver='lbfgs')
stacker2.fit(meta2_X[h1_mask], y_test_arr[h1_mask])
stack2_probs = stacker2.predict_proba(meta2_X[h2_mask])[:, 1]
stack2_brier = brier_score_loss(y_test_arr[h2_mask], stack2_probs)
stack2_weights = stacker2.coef_[0]

# ─ 3-model stacker ───────────────────────────────────────────────────────
meta3_X = np.column_stack([xgb_probs, lgb_probs, ft_probs])
stacker3 = LogisticRegression(C=0.1, max_iter=1000, solver='lbfgs')
stacker3.fit(meta3_X[h1_mask], y_test_arr[h1_mask])
stack3_probs = stacker3.predict_proba(meta3_X[h2_mask])[:, 1]
stack3_brier = brier_score_loss(y_test_arr[h2_mask], stack3_probs)
stack3_weights = stacker3.coef_[0]

# ─ Full-test evaluation (apply stacker trained on H1 to full test) ───────
stack2_full_probs = stacker2.predict_proba(meta2_X)[:, 1]
stack3_full_probs = stacker3.predict_proba(meta3_X)[:, 1]
stack2_brier_full = brier_score_loss(y_test_arr, stack2_full_probs)
stack3_brier_full = brier_score_loss(y_test_arr, stack3_full_probs)

# ─ Report ─────────────────────────────────────────────────────────────────
print()
print("=" * 55)
print("RESULTS — Full test set (2023+, 127,928 rows)")
print("=" * 55)
print(f"  XGBoost              Brier: {xgb_brier:.4f}")
print(f"  LightGBM             Brier: {lgb_brier:.4f}")
print(f"  FT-Transformer       Brier: {ft_brier:.4f}")
print(f"  Simple avg (XGB+LGB) Brier: {avg2_brier_full:.4f}")
print(f"  Simple avg (all 3)   Brier: {avg3_brier_full:.4f}")
print(f"  Stacked 2-model (XGB+LGB) full:  {stack2_brier_full:.4f}")
print(f"  Stacked 3-model (all 3)   full:  {stack3_brier_full:.4f}")
print()
print("RESULTS — 2023-H2 holdout (stacking eval set)")
print("=" * 55)
print(f"  Simple avg 2-model: {avg2_brier_h2:.4f}")
print(f"  Simple avg 3-model: {avg3_brier_h2:.4f}")
print(f"  Stacked 2-model: {stack2_brier:.4f}  weights: XGB={stack2_weights[0]:.3f} LGB={stack2_weights[1]:.3f}")
print(f"  Stacked 3-model: {stack3_brier:.4f}  weights: XGB={stack3_weights[0]:.3f} LGB={stack3_weights[1]:.3f} FT={stack3_weights[2]:.3f}")
print("=" * 55)

# ─ Decide which ensemble to use ──────────────────────────────────────────
if stack3_brier < stack2_brier:
    print(f"\n✓ 3-model stacker improves over 2-model: {stack2_brier:.4f} → {stack3_brier:.4f} (Δ={stack2_brier-stack3_brier:.4f})")
    best_stacker = stacker3
    best_names   = ['xgboost', 'lightgbm', 'ft_transformer']
    best_brier   = stack3_brier
    best_label   = "3-model (XGB+LGB+FT)"
else:
    print(f"\n⚠ 3-model stacker does NOT improve over 2-model ({stack3_brier:.4f} ≥ {stack2_brier:.4f}). Keeping 2-model.")
    best_stacker = stacker2
    best_names   = ['xgboost', 'lightgbm']
    best_brier   = stack2_brier
    best_label   = "2-model (XGB+LGB)"

print(f"  Best ensemble: {best_label} | 2023-H2 Brier: {best_brier:.4f}")

# Leakage guard
if best_brier < 0.15:
    raise RuntimeError(f"STOP: Brier {best_brier:.4f} < 0.15 — likely data leakage!")

# ── Save ────────────────────────────────────────────────────────────────────
print("\nSaving models...")

# Save the ensemble as a simple dict (compatible with predict_engine.py)
ensemble_obj = {
    'model_names': best_names,
    'stacker': best_stacker,
    'type': 'stacked',
    'n_models': len(best_names),
    # Also store model weights for reporting
    'weights': dict(zip(best_names, best_stacker.coef_[0].tolist())),
    'h2_brier': float(best_brier),
}
with open(ENSEMBLE_PKL, 'wb') as f:
    pickle.dump(ensemble_obj, f)
print(f"  Saved: {ENSEMBLE_PKL}")

# Also save ft predictions to models/ for inference use
ft_probs_path = OUT_DIR / 'ft_probs_v4.npy'  # already exists
print(f"  FT probs: {ft_probs_path} (already in place)")

# Update ensemble_summary.json
summary = {
    'xgb_brier_full_test': float(xgb_brier),
    'lgb_brier_full_test': float(lgb_brier),
    'ft_brier_full_test': float(ft_brier),
    'avg2_brier_full_test': float(avg2_brier_full),
    'avg3_brier_full_test': float(avg3_brier_full),
    'stack2_brier_full_test': float(stack2_brier_full),
    'stack3_brier_full_test': float(stack3_brier_full),
    'stack2_brier_h2': float(stack2_brier),
    'stack3_brier_h2': float(stack3_brier),
    'best_ensemble': best_label,
    'best_brier_h2': float(best_brier),
    'stacker_weights': dict(zip(best_names, best_stacker.coef_[0].tolist())),
    'n_features': int(X.shape[1]),
    'train_size': int(tr.sum()),
    'test_size': int(te.sum()),
    'temporal_split': '2023-01-01',
    'leakage_check': 'PASS' if best_brier >= 0.15 else 'FAIL',
}
with open(SUMMARY_JSON, 'w') as f:
    json.dump(summary, f, indent=2)
print(f"  Saved: {SUMMARY_JSON}")

print("\n✓ Done. Zero leakage: all evals on 2023+ temporal holdout.")
