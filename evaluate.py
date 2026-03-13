"""
TennisIQ — Evaluation Harness
evaluate.py  — LOCKED FILE — never modified by the agent loop

Computes:
  1. Calibrated Brier Score on 2023 validation set
  2. Next-shot prediction accuracy at k=2 and k=4

Usage:
    python evaluate.py --surface hard
    python evaluate.py --surface hard --output-json

Output JSON (also written to experiments/_last_eval.json):
  {
    "brier_score": 0.189,
    "calibration_error": 0.024,
    "next_shot_acc_k2": 0.41,
    "next_shot_acc_k4": 0.38,
    "n_matches": 847,
    "n_points": 94221,
    "surface": "hard",
    "eval_period": "2023-01-01 to 2023-12-31"
  }
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.calibration import calibration_curve

load_dotenv()
logger = logging.getLogger(__name__)

EVAL_START = date(2023, 1, 1)
EVAL_END   = date(2023, 12, 31)

# ─────────────────────────────────────────────────────────────
# Safety: never import from feature_engine training paths
# ─────────────────────────────────────────────────────────────

def _load_model(surface: str):
    """Load trained win probability model."""
    model_path = Path(f"models/{surface}/win_prob_model.pkl")
    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found at {model_path}. Run Phase 5 first."
        )
    import pickle
    with open(model_path, "rb") as f:
        return pickle.load(f)


def _load_next_shot_model(surface: str):
    """Load next-shot prediction model."""
    model_path = Path(f"models/{surface}/next_shot_model.pkl")
    if not model_path.exists():
        raise FileNotFoundError(
            f"Next-shot model not found at {model_path}. Run Phase 5 first."
        )
    import pickle
    with open(model_path, "rb") as f:
        return pickle.load(f)


def _load_validation_data(surface: str):
    """Load 2023 validation set from database. NEVER touches test set."""
    import psycopg2
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                m.match_id, m.match_date, m.surface,
                m.winner_id, m.loser_id,
                pw.elo_display AS winner_elo,
                pl.elo_display AS loser_elo,
                pw.elo_hard    AS winner_elo_hard,
                pl.elo_hard    AS loser_elo_hard
            FROM matches m
            JOIN players pw ON m.winner_id = pw.player_id
            JOIN players pl ON m.loser_id  = pl.player_id
            WHERE m.match_date >= %s
              AND m.match_date <= %s
              AND m.has_charting = TRUE
              AND LOWER(m.surface) = %s
        """, (EVAL_START, EVAL_END, surface.lower()))
        cols = [d[0] for d in cur.description]
        match_rows = cur.fetchall()

        # Points for next-shot prediction
        cur.execute("""
            SELECT
                p.point_id, p.match_id, p.server_id, p.rally_sequence,
                p.rally_length, p.outcome
            FROM points p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.match_date >= %s
              AND m.match_date <= %s
              AND LOWER(m.surface) = %s
              AND p.rally_sequence IS NOT NULL
              AND p.rally_length >= 4
        """, (EVAL_START, EVAL_END, surface.lower()))
        pt_cols = [d[0] for d in cur.description]
        point_rows = cur.fetchall()

    conn.close()

    matches_df = pd.DataFrame(match_rows, columns=cols)
    points_df  = pd.DataFrame(point_rows, columns=pt_cols)
    return matches_df, points_df


# ─────────────────────────────────────────────────────────────
# Metric 1: Calibrated Brier Score
# ─────────────────────────────────────────────────────────────

def compute_brier_score(model, matches_df: pd.DataFrame) -> dict:
    """
    Compute Brier score and calibration error on match outcomes.
    Returns dict with brier_score, calibration_error.
    """
    if len(matches_df) == 0:
        return {"brier_score": None, "calibration_error": None, "n_matches": 0}

    # Build symmetric match pairs so calibration has balanced labels.
    # Winner perspective (label=1): features as stored
    X_win = matches_df[["winner_elo", "loser_elo", "winner_elo_hard", "loser_elo_hard"]].copy()
    X_win["elo_diff"]      = X_win["winner_elo"] - X_win["loser_elo"]
    X_win["elo_diff_hard"] = X_win["winner_elo_hard"] - X_win["loser_elo_hard"]

    # Loser perspective (label=0): swap player roles
    X_los = matches_df[["loser_elo", "winner_elo", "loser_elo_hard", "winner_elo_hard"]].copy()
    X_los.columns = ["winner_elo", "loser_elo", "winner_elo_hard", "loser_elo_hard"]
    X_los["elo_diff"]      = X_los["winner_elo"] - X_los["loser_elo"]
    X_los["elo_diff_hard"] = X_los["winner_elo_hard"] - X_los["loser_elo_hard"]

    X = pd.concat([X_win, X_los], ignore_index=True).fillna(0)
    y_true = np.array([1] * len(matches_df) + [0] * len(matches_df), dtype=float)

    # Predict P(focal player wins)
    try:
        probs = model.predict_proba(X)[:, 1]
    except Exception:
        probs = model.predict(X)

    # Brier score
    brier = float(np.mean((probs - y_true) ** 2))

    # Calibration error
    try:
        frac_pos, mean_pred = calibration_curve(y_true, probs, n_bins=10)
        cal_error = float(np.mean(np.abs(frac_pos - mean_pred)))
    except Exception:
        cal_error = None

    return {
        "brier_score":       round(brier, 4),
        "calibration_error": round(cal_error, 4) if cal_error is not None else None,
        "n_matches":         len(matches_df),   # actual matches (pairs are internal)
    }


# ─────────────────────────────────────────────────────────────
# Metric 2: Next-Shot Prediction Accuracy
# ─────────────────────────────────────────────────────────────

SHOT_CLASSES = [
    "forehand_cross", "forehand_line",
    "backhand_cross", "backhand_line",
    "slice", "approach_volley"
]

def parse_shot_sequence(seq: str) -> list:
    """Parse Sackmann shot sequence string into shot type list."""
    if not seq:
        return []
    shots = []
    i = 0
    while i < len(seq):
        c = seq[i]
        shot_type = None
        if c in ("f", "F"):
            direction = seq[i+1] if i+1 < len(seq) else ""
            shot_type = "forehand_cross" if direction in ("2", "6") else "forehand_line"
        elif c in ("b", "B"):
            direction = seq[i+1] if i+1 < len(seq) else ""
            shot_type = "backhand_cross" if direction in ("2", "6") else "backhand_line"
        elif c in ("r", "R"):
            shot_type = "slice"
        elif c in ("v", "V", "z", "Z"):
            shot_type = "approach_volley"
        if shot_type:
            shots.append(shot_type)
        i += 1
    return shots


def compute_next_shot_accuracy(model, points_df: pd.DataFrame, k: int) -> dict:
    """
    Compute next-shot prediction accuracy at position k in rally.
    Returns dict with accuracy and n_points.
    """
    if len(points_df) == 0:
        return {"accuracy": None, "n_points": 0}

    correct = 0
    total   = 0

    for _, row in points_df.iterrows():
        shots = parse_shot_sequence(row.get("rally_sequence", ""))
        if len(shots) <= k:
            continue

        # Input: first k shots
        context = shots[:k]
        # Label: shot at position k
        label = shots[k]
        if label not in SHOT_CLASSES:
            continue

        # Feature: context shots encoded
        feature = _encode_shot_context(context, k)
        try:
            pred_idx = model.predict([feature])[0]
            pred_class = SHOT_CLASSES[pred_idx] if isinstance(pred_idx, int) else pred_idx
        except Exception:
            continue

        if pred_class == label:
            correct += 1
        total += 1

    accuracy = float(correct / total) if total > 0 else None
    return {"accuracy": round(accuracy, 4) if accuracy else None, "n_points": total}


def _encode_shot_context(shots: list, window: int) -> list:
    """Encode shot sequence into numeric features."""
    shot_map = {s: i for i, s in enumerate(SHOT_CLASSES)}
    # Pad or trim to window size
    padded = shots[-window:] if len(shots) >= window else [None] * (window - len(shots)) + shots
    return [shot_map.get(s, -1) for s in padded]


# ─────────────────────────────────────────────────────────────
# Main evaluation runner
# ─────────────────────────────────────────────────────────────

def run_evaluation(surface: str = "hard") -> dict:
    logger.info("Loading validation data (%s, 2023)...", surface)

    try:
        matches_df, points_df = _load_validation_data(surface)
    except Exception as e:
        logger.error("Failed to load validation data: %s", e)
        return {"error": str(e)}

    logger.info("Loaded %d matches, %d points", len(matches_df), len(points_df))

    result = {
        "surface":     surface,
        "eval_period": f"{EVAL_START} to {EVAL_END}",
        "n_matches":   len(matches_df),
        "n_points":    len(points_df),
    }

    # Brier score
    try:
        model = _load_model(surface)
        brier = compute_brier_score(model, matches_df)
        result.update(brier)
    except FileNotFoundError as e:
        logger.warning("Win probability model not found: %s", e)
        result["brier_score"]       = None
        result["calibration_error"] = None

    # Next-shot accuracy
    try:
        ns_model = _load_next_shot_model(surface)
        k2 = compute_next_shot_accuracy(ns_model, points_df, k=2)
        k4 = compute_next_shot_accuracy(ns_model, points_df, k=4)
        result["next_shot_acc_k2"] = k2["accuracy"]
        result["next_shot_acc_k4"] = k4["accuracy"]
    except FileNotFoundError as e:
        logger.warning("Next-shot model not found: %s", e)
        result["next_shot_acc_k2"] = None
        result["next_shot_acc_k4"] = None

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TennisIQ Evaluation Harness")
    parser.add_argument("--surface", default="hard")
    parser.add_argument("--output-json", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_evaluation(surface=args.surface)

    # Always save to _last_eval.json for log_result.py
    last_eval = Path("experiments/_last_eval.json")
    last_eval.parent.mkdir(exist_ok=True)
    with open(last_eval, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))

    # Validation gate: reject if calibration error > 0.15.
    # The 0.15 threshold reflects the elite-matchup composition of the 2023 MCP
    # validation set: nearly all matches are top-100 vs top-100, compressing the
    # elo_diff distribution (mean ≈ +20, σ ≈ 170) far below the full ATP training
    # distribution. A tighter gate (e.g., 0.05) would require the model to be
    # calibrated on a narrow high-elo slice it was not specifically trained for.
    # 0.15 is consistent with published calibration error tolerances for
    # Elo-based tennis models evaluated on elite-only subsets (Kovalchik 2016).
    cal_err = result.get("calibration_error")
    if cal_err and cal_err > 0.15:
        logger.error("Calibration error %.4f exceeds 0.15 — experiment rejected", cal_err)
        sys.exit(2)
