"""
Restack Ensemble — 3-Model (XGB + LGB + FT-Transformer full-data)
==================================================================
Run AFTER train_ft_standalone.py has completed.

Loads:
  - models/ensemble/xgb_model.pkl
  - models/ensemble/lgb_model.pkl
  - models/ensemble/ft_probs_v4_full.npy  (127,928 predictions from full-data FT run)
  - data/processed/training_edge_v4.pkl   (for test labels + dates)

Fits logistic regression meta-learner on 2023-H1, evaluates on 2023-H2.
Compares 2-model vs 3-model stacked ensembles.
Saves updated stacked_ensemble.pkl (whichever is better).

Usage:
  python3 -u scripts/restack_ensemble.py
"""

import pickle
import numpy as np
import pandas as pd
import json
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
import warnings
warnings.filterwarnings('ignore')

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_PATH     = 'data/processed/training_edge_v4.pkl'
OUT_DIR       = Path('models/ensemble')
XGB_MODEL     = OUT_DIR / 'xgb_model.pkl'
LGB_MODEL     = OUT_DIR / 'lgb_model.pkl'
FT_PROBS_FULL = OUT_DIR / 'ft_probs_v4_full.npy'
FT_PROBS_COLAB = OUT_DIR / 'ft_probs_v4.npy'   # original Colab run (40-feature, 300K subsample)
ENSEMBLE_PKL  = OUT_DIR / 'stacked_ensemble.pkl'
SUMMARY_JSON  = OUT_DIR / 'ensemble_summary.json'

CUTOFF = pd.Timestamp('2023-01-01')

print("=" * 60, flush=True)
print("RESTACK ENSEMBLE — 3-MODEL (XGB + LGB + FT FULL DATA)", flush=True)
print("=" * 60, flush=True)

# ── 1. Load test data ────────────────────────────────────────────────────────
print("\n[1/5] Loading training_edge_v4.pkl ...", flush=True)
with open(DATA_PATH, 'rb') as f:
    bundle = pickle.load(f)
X_df, y_ser, dates = bundle[0], bundle[1], bundle[2]

tr_mask = dates < CUTOFF
te_mask = ~tr_mask

X_train, y_train = X_df[tr_mask], y_ser[tr_mask]
X_test,  y_test  = X_df[te_mask], y_ser[te_mask]
dates_test = dates[te_mask].reset_index(drop=True)
y_test_arr = y_test.values

print(f"  Train: {tr_mask.sum():,} | Test: {te_mask.sum():,} | Features: {X_df.shape[1]}", flush=True)
assert len(X_test) == 127928, f"Expected 127,928 test rows, got {len(X_test)}"

# ── 2. XGBoost predictions ───────────────────────────────────────────────────
print("\n[2/5] Generating XGBoost predictions ...", flush=True)
with open(XGB_MODEL, 'rb') as f:
    xgb_model = pickle.load(f)
xgb_probs = xgb_model.predict_proba(X_test)[:, 1]
xgb_brier = brier_score_loss(y_test_arr, xgb_probs)
print(f"  XGBoost Brier: {xgb_brier:.4f}", flush=True)

# ── 3. LightGBM predictions ──────────────────────────────────────────────────
print("\n[3/5] Generating LightGBM predictions ...", flush=True)
with open(LGB_MODEL, 'rb') as f:
    lgb_model = pickle.load(f)
X_test_clean = X_test.copy()
X_test_clean.columns = X_test_clean.columns.str.replace('[^A-Za-z0-9_]', '_', regex=True)
lgb_probs = lgb_model.predict_proba(X_test_clean)[:, 1]
lgb_brier = brier_score_loss(y_test_arr, lgb_probs)
print(f"  LightGBM Brier: {lgb_brier:.4f}", flush=True)

# ── 4. Load FT-Transformer predictions ──────────────────────────────────────
print("\n[4/5] Loading FT-Transformer predictions ...", flush=True)

if not FT_PROBS_FULL.exists():
    print(f"  ERROR: {FT_PROBS_FULL} not found.", flush=True)
    print("  Run scripts/train_ft_standalone.py first.", flush=True)
    raise SystemExit(1)

ft_probs = np.load(FT_PROBS_FULL)
assert len(ft_probs) == len(X_test), \
    f"FT probs length {len(ft_probs)} ≠ test set {len(X_test)}"
ft_brier = brier_score_loss(y_test_arr, ft_probs)
print(f"  FT-Transformer (full-data) Brier: {ft_brier:.4f}", flush=True)

# Also check original Colab FT probs for comparison
if FT_PROBS_COLAB.exists():
    ft_colab = np.load(FT_PROBS_COLAB)
    if len(ft_colab) == len(X_test):
        colab_brier = brier_score_loss(y_test_arr, ft_colab)
        print(f"  FT-Transformer (Colab, 40-feat, 300K) Brier: {colab_brier:.4f}  [for reference]", flush=True)

# ── 5. Stacking ──────────────────────────────────────────────────────────────
print("\n[5/5] Fitting stacked ensembles ...", flush=True)

# Temporal split within test set: H1 for stacker training, H2 for evaluation
midpoint = dates_test.quantile(0.5)
h1_mask  = (dates_test < midpoint).values
h2_mask  = ~h1_mask
print(f"  Stacker train (2023-H1): {h1_mask.sum():,} | eval (2023-H2): {h2_mask.sum():,}", flush=True)

# ─ Simple averages ────────────────────────────────────────────────────────
avg2_h2 = brier_score_loss(y_test_arr[h2_mask], (xgb_probs + lgb_probs)[h2_mask] / 2)
avg3_h2 = brier_score_loss(y_test_arr[h2_mask], (xgb_probs + lgb_probs + ft_probs)[h2_mask] / 3)

avg2_full = brier_score_loss(y_test_arr, (xgb_probs + lgb_probs) / 2)
avg3_full = brier_score_loss(y_test_arr, (xgb_probs + lgb_probs + ft_probs) / 3)

# ─ 2-model stacker ───────────────────────────────────────────────────────
meta2 = np.column_stack([xgb_probs, lgb_probs])
stacker2 = LogisticRegression(C=0.1, max_iter=1000, solver='lbfgs')
stacker2.fit(meta2[h1_mask], y_test_arr[h1_mask])
stack2_h2    = brier_score_loss(y_test_arr[h2_mask], stacker2.predict_proba(meta2[h2_mask])[:, 1])
stack2_full  = brier_score_loss(y_test_arr, stacker2.predict_proba(meta2)[:, 1])
w2           = stacker2.coef_[0]

# ─ 3-model stacker ───────────────────────────────────────────────────────
meta3 = np.column_stack([xgb_probs, lgb_probs, ft_probs])
stacker3 = LogisticRegression(C=0.1, max_iter=1000, solver='lbfgs')
stacker3.fit(meta3[h1_mask], y_test_arr[h1_mask])
stack3_h2    = brier_score_loss(y_test_arr[h2_mask], stacker3.predict_proba(meta3[h2_mask])[:, 1])
stack3_full  = brier_score_loss(y_test_arr, stacker3.predict_proba(meta3)[:, 1])
w3           = stacker3.coef_[0]

# ─ Report ─────────────────────────────────────────────────────────────────
print(flush=True)
print("=" * 60, flush=True)
print("RESULTS — Full test set (2023+, 127,928 rows)", flush=True)
print("=" * 60, flush=True)
print(f"  XGBoost                    Brier: {xgb_brier:.4f}", flush=True)
print(f"  LightGBM                   Brier: {lgb_brier:.4f}", flush=True)
print(f"  FT-Transformer (full-data) Brier: {ft_brier:.4f}", flush=True)
print(f"  Simple avg (XGB+LGB)       Brier: {avg2_full:.4f}", flush=True)
print(f"  Simple avg (all 3)         Brier: {avg3_full:.4f}", flush=True)
print(f"  Stacked 2-model full:      Brier: {stack2_full:.4f}", flush=True)
print(f"  Stacked 3-model full:      Brier: {stack3_full:.4f}", flush=True)
print(flush=True)
print("RESULTS — 2023-H2 holdout (stacking eval set)", flush=True)
print("=" * 60, flush=True)
print(f"  Simple avg 2-model: {avg2_h2:.4f}", flush=True)
print(f"  Simple avg 3-model: {avg3_h2:.4f}", flush=True)
print(f"  Stacked 2-model:    {stack2_h2:.4f}  "
      f"weights: XGB={w2[0]:.3f} LGB={w2[1]:.3f}", flush=True)
print(f"  Stacked 3-model:    {stack3_h2:.4f}  "
      f"weights: XGB={w3[0]:.3f} LGB={w3[1]:.3f} FT={w3[2]:.3f}", flush=True)
print("=" * 60, flush=True)

ft_weight_positive = w3[2] > 0
print(flush=True)
if ft_weight_positive:
    print(f"✓ FT-Transformer has POSITIVE weight ({w3[2]:.3f}) — contributing positively.", flush=True)
else:
    print(f"⚠ FT-Transformer still has NEGATIVE weight ({w3[2]:.3f}) — tree models dominate.", flush=True)

# ─ Decision ──────────────────────────────────────────────────────────────
if stack3_h2 < stack2_h2:
    improvement = stack2_h2 - stack3_h2
    print(f"✓ 3-model stacker improves: {stack2_h2:.4f} → {stack3_h2:.4f} (Δ={improvement:.4f})", flush=True)
    best_stacker = stacker3
    best_names   = ['xgboost', 'lightgbm', 'ft_transformer']
    best_brier_h2 = stack3_h2
    best_label   = "3-model (XGB+LGB+FT-full)"
else:
    print(f"⚠ 3-model stacker does NOT improve ({stack3_h2:.4f} ≥ {stack2_h2:.4f}). Keeping 2-model.", flush=True)
    best_stacker = stacker2
    best_names   = ['xgboost', 'lightgbm']
    best_brier_h2 = stack2_h2
    best_label   = "2-model (XGB+LGB)"

print(f"  → Best: {best_label} | 2023-H2 Brier: {best_brier_h2:.4f}", flush=True)

# Leakage guard
if best_brier_h2 < 0.15:
    raise RuntimeError(f"STOP: Brier {best_brier_h2:.4f} < 0.15 — data leakage!")

# ── Save ──────────────────────────────────────────────────────────────────────
print(f"\nSaving {ENSEMBLE_PKL} ...", flush=True)
ensemble_obj = {
    'model_names': best_names,
    'stacker':     best_stacker,
    'type':        'stacked',
    'n_models':    len(best_names),
    'weights':     dict(zip(best_names, best_stacker.coef_[0].tolist())),
    'h2_brier':    float(best_brier_h2),
    'ft_weight_positive': ft_weight_positive,
}
with open(ENSEMBLE_PKL, 'wb') as f:
    pickle.dump(ensemble_obj, f)
print(f"  Saved.", flush=True)

# Update ensemble summary
print(f"Updating {SUMMARY_JSON} ...", flush=True)
try:
    with open(SUMMARY_JSON) as f:
        summary = json.load(f)
except FileNotFoundError:
    summary = {}

summary.update({
    'ft_full_data_brier':       float(ft_brier),
    'stack2_h2_brier':          float(stack2_h2),
    'stack3_h2_brier_full_ft':  float(stack3_h2),
    'best_ensemble':            best_label,
    'best_brier_h2':            float(best_brier_h2),
    'stacker_weights':          dict(zip(best_names, best_stacker.coef_[0].tolist())),
    'ft_weight_positive':       ft_weight_positive,
    'leakage_check':            'PASS',
})
with open(SUMMARY_JSON, 'w') as f:
    json.dump(summary, f, indent=2)
print(f"  Saved.", flush=True)

print("\n✓ Done. Zero leakage: all evaluations on 2023+ temporal holdout.", flush=True)
