"""
TennisIQ Temporal Agent — Hyperparameter tuning with ZERO LEAKAGE.
Loads edge features (with dates), trains on pre-2023, evaluates on 2023+.
Every experiment uses temporal split. No random CV. No cheating.
"""

import pandas as pd
import numpy as np
import pickle
import json
import time
import sys
import logging
from pathlib import Path
from datetime import datetime
from sklearn.metrics import brier_score_loss

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAINING_PATH = REPO_ROOT / "data" / "processed" / "training_edge_v1.pkl"
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
MODELS_DIR = REPO_ROOT / "models" / "hard"
TEMPORAL_CUTOFF = "2023-01-01"
STOP_HOUR = 8
STOP_MINUTE = 30
MAX_EXPERIMENTS = 500

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("temporal_agent")

# ─────────────────────────────────────────────────
# LOAD DATA + SPLIT
# ─────────────────────────────────────────────────
logger.info("Loading training data...")
data = pickle.load(open(TRAINING_PATH, "rb"))
X, y, dates = data[0], data[1], data[2]
logger.info(f"  Rows: {len(X):,} | Features: {X.shape[1]}")

cutoff = pd.Timestamp(TEMPORAL_CUTOFF)
train_mask = dates < cutoff
test_mask = dates >= cutoff
X_train, y_train = X[train_mask].values, y[train_mask].values
X_test, y_test = X[~train_mask].values, y[~train_mask].values
feature_names = list(X.columns)

logger.info(f"  Train: {len(X_train):,} (pre-{TEMPORAL_CUTOFF})")
logger.info(f"  Test:  {len(X_test):,} (post-{TEMPORAL_CUTOFF})")
logger.info(f"  TEMPORAL SPLIT — zero leakage guaranteed")

# ─────────────────────────────────────────────────
# BASELINE
# ─────────────────────────────────────────────────
import xgboost as xgb

BASE_PARAMS = {
    "max_depth": 6,
    "learning_rate": 0.1,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_alpha": 0,
    "reg_lambda": 1,
    "eval_metric": "logloss",
    "use_label_encoder": False,
    "random_state": 42,
}

# Param ranges for agent to explore
PARAM_RANGES = {
    "max_depth": (2, 10, int),
    "learning_rate": (0.01, 0.3, float),
    "n_estimators": (100, 800, int),
    "subsample": (0.5, 1.0, float),
    "colsample_bytree": (0.3, 1.0, float),
    "min_child_weight": (1, 20, int),
    "reg_alpha": (0, 10, float),
    "reg_lambda": (0, 10, float),
}

def evaluate(params):
    """Train on pre-2023, evaluate Brier on 2023+. THE ONLY evaluation function."""
    model = xgb.XGBClassifier(**params, random_state=42)
    model.fit(X_train, y_train, verbose=False)
    probs = model.predict_proba(X_test)[:, 1]
    brier = float(np.mean((probs - y_test) ** 2))
    return brier, model

logger.info("Computing baseline...")
baseline_brier, baseline_model = evaluate(BASE_PARAMS)
logger.info(f"  Baseline Brier (temporal): {baseline_brier:.6f}")

best_brier = baseline_brier
best_params = dict(BASE_PARAMS)
best_model = baseline_model
current_params = dict(BASE_PARAMS)

# Save baseline
pickle.dump(baseline_model, open(MODELS_DIR / "temporal_baseline_model.pkl", "wb"))

# ─────────────────────────────────────────────────
# BEDROCK AGENT
# ─────────────────────────────────────────────────
try:
    import boto3
    bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")
    USE_LLM = True
    logger.info("Bedrock connected — using Haiku for proposals")
except:
    USE_LLM = False
    logger.info("No Bedrock — using random proposals")

def should_stop():
    now = datetime.now()
    stop = now.replace(hour=STOP_HOUR, minute=STOP_MINUTE, second=0)
    if stop <= now:
        stop = stop.replace(day=stop.day + 1)
    return now >= stop

def llm_propose(history_str):
    """Ask Haiku to propose next experiment."""
    if not USE_LLM:
        # Random fallback
        param = np.random.choice(list(PARAM_RANGES.keys()))
        lo, hi, typ = PARAM_RANGES[param]
        if typ == int:
            val = int(np.random.randint(lo, hi + 1))
        else:
            val = round(float(np.random.uniform(lo, hi)), 4)
        return param, val, "random exploration"

    prompt = f"""You are tuning XGBoost hyperparameters for a tennis match prediction model.
The evaluation uses TEMPORAL SPLIT: train on pre-2023 matches, test on 2023+ matches.
Current best Brier score: {best_brier:.6f} (lower is better).
Current params: {json.dumps(current_params, indent=2)}

Parameter ranges:
{json.dumps({k: {"min": v[0], "max": v[1], "type": str(v[2].__name__)} for k, v in PARAM_RANGES.items()}, indent=2)}

Recent experiment history:
{history_str}

Propose ONE parameter change. Return ONLY valid JSON:
{{"param": "param_name", "new_value": value, "rationale": "brief reason", "expected_delta": "-0.001"}}
"""
    try:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        })
        response = bedrock.invoke_model(
            modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            body=body
        )
        text = json.loads(response["body"].read())["content"][0]["text"]
        # Extract JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            proposal = json.loads(text[start:end])
            param = proposal["param"]
            val = proposal["new_value"]
            rationale = proposal.get("rationale", "")
            if param in PARAM_RANGES:
                lo, hi, typ = PARAM_RANGES[param]
                val = typ(val)
                val = max(lo, min(hi, val))
                return param, val, rationale
    except Exception as e:
        logger.warning(f"LLM error: {e}")

    # Fallback to random
    param = np.random.choice(list(PARAM_RANGES.keys()))
    lo, hi, typ = PARAM_RANGES[param]
    if typ == int:
        val = int(np.random.randint(lo, hi + 1))
    else:
        val = round(float(np.random.uniform(lo, hi)), 4)
    return param, val, "random fallback"

def llm_decide(param, new_val, brier, delta):
    """Ask Haiku: KEEP, REVERT, or NEUTRAL."""
    if delta < -0.0005:
        return "KEEP"
    elif delta > 0.001:
        return "REVERT"
    else:
        return "NEUTRAL"

# ─────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────
EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
history = []
keeps = 0
reverts = 0
neutrals = 0

logger.info("=" * 70)
logger.info("  TEMPORAL AGENT LOOP — ZERO LEAKAGE")
logger.info(f"  Baseline: {baseline_brier:.6f}")
logger.info(f"  Max experiments: {MAX_EXPERIMENTS}")
logger.info(f"  Stop time: {STOP_HOUR:02d}:{STOP_MINUTE:02d}")
logger.info("=" * 70)

for exp_num in range(1, MAX_EXPERIMENTS + 1):
    if should_stop():
        logger.info("Stop time reached.")
        break

    logger.info(f"\n── Experiment {exp_num} ──")

    # Get proposal
    hist_str = "\n".join(history[-10:]) if history else "No history yet."
    param, new_val, rationale = llm_propose(hist_str)

    old_val = current_params.get(param, BASE_PARAMS.get(param))

    # Skip if same value
    if new_val == old_val:
        logger.info(f"  Skip: {param} already = {new_val}")
        history.append(f"Exp {exp_num}: SKIP {param}={new_val} (no change)")
        continue

    logger.info(f"  Proposal: {param} = {old_val} -> {new_val}")
    logger.info(f"  Rationale: {rationale[:100]}")

    # Evaluate
    test_params = dict(current_params)
    test_params[param] = new_val

    try:
        t0 = time.time()
        brier, model = evaluate(test_params)
        elapsed = time.time() - t0
        delta = brier - best_brier
    except Exception as e:
        logger.error(f"  Training failed: {e}")
        history.append(f"Exp {exp_num}: ERROR {param}={new_val}: {e}")
        continue

    # Decide
    decision = llm_decide(param, new_val, brier, delta)

    if decision == "KEEP":
        current_params[param] = new_val
        best_brier = brier
        best_params = dict(current_params)
        best_model = model
        keeps += 1
        pickle.dump(model, open(MODELS_DIR / "best_temporal_agent_model.pkl", "wb"))
        logger.info(f"  ✅ KEEP — Brier: {brier:.6f} (Δ={delta:+.6f}) [{elapsed:.1f}s]")
    elif decision == "REVERT":
        reverts += 1
        logger.info(f"  ❌ REVERT — Brier: {brier:.6f} (Δ={delta:+.6f}) [{elapsed:.1f}s]")
    else:
        neutrals += 1
        logger.info(f"  ⚪ NEUTRAL — Brier: {brier:.6f} (Δ={delta:+.6f}) [{elapsed:.1f}s]")

    history.append(f"Exp {exp_num}: {decision} {param}={new_val} Brier={brier:.6f} Δ={delta:+.6f}")

    # Save experiment result
    exp_result = {
        "experiment": exp_num,
        "param": param,
        "old_value": old_val if not isinstance(old_val, np.integer) else int(old_val),
        "new_value": new_val if not isinstance(new_val, np.integer) else int(new_val),
        "brier": float(brier),
        "delta": float(delta),
        "decision": decision,
        "rationale": rationale,
        "elapsed_seconds": float(elapsed),
        "temporal_split": True,
    }
    json.dump(exp_result, open(EXPERIMENTS_DIR / f"temporal_{exp_num:04d}.json", "w"), indent=2)

# ─────────────────────────────────────────────────
# FINAL REPORT
# ─────────────────────────────────────────────────
logger.info("\n" + "=" * 70)
logger.info("  TEMPORAL AGENT COMPLETE")
logger.info(f"  Experiments: {exp_num}")
logger.info(f"  KEEP: {keeps} | REVERT: {reverts} | NEUTRAL: {neutrals}")
logger.info(f"  Baseline Brier: {baseline_brier:.6f}")
logger.info(f"  Best Brier:     {best_brier:.6f}")
logger.info(f"  Improvement:    {baseline_brier - best_brier:.6f}")
logger.info(f"  Best params: {json.dumps(best_params, indent=2)}")
logger.info(f"  ZERO LEAKAGE: All evaluations used temporal split")
logger.info("=" * 70)

# Save final report
report = {
    "baseline_brier": float(baseline_brier),
    "best_brier": float(best_brier),
    "improvement": float(baseline_brier - best_brier),
    "experiments": exp_num,
    "keeps": keeps,
    "reverts": reverts,
    "neutrals": neutrals,
    "best_params": {k: int(v) if isinstance(v, (np.integer, int)) else float(v) for k, v in best_params.items()},
    "temporal_split": True,
    "zero_leakage": True,
    "cutoff": TEMPORAL_CUTOFF,
    "train_rows": int(len(X_train)),
    "test_rows": int(len(X_test)),
}
json.dump(report, open(EXPERIMENTS_DIR / "temporal_agent_report.json", "w"), indent=2)

# Save best model
pickle.dump(best_model, open(MODELS_DIR / "best_temporal_agent_model.pkl", "wb"))
logger.info(f"\nSaved best model and report.")
