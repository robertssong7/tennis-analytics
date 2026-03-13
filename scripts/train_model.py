"""
TennisIQ — Phase 5: Model Training
scripts/train_model.py

Trains:
  1. XGBoost win probability model (hard court, pre-2023 matches)
  2. Dummy next-shot model (placeholder until MCP charted data is loaded)

Saves to:
  models/hard/win_prob_model.pkl
  models/hard/next_shot_model.pkl

Usage:
    python scripts/train_model.py
"""

import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.dummy import DummyClassifier
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import cross_val_score, StratifiedKFold
from xgboost import XGBClassifier

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TRAINING_CUTOFF = "2022-12-31"
SURFACE = "hard"
MODEL_DIR = Path("models/hard")

# evaluate.py builds X with exactly these column names in this order
FEATURE_COLS = [
    "winner_elo", "loser_elo",
    "winner_elo_hard", "loser_elo_hard",
    "elo_diff", "elo_diff_hard",
]

SHOT_CLASSES = [
    "forehand_cross", "forehand_line",
    "backhand_cross", "backhand_line",
    "slice", "approach_volley",
]


# ─────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────

def load_training_matches(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                m.match_id,
                m.match_date,
                pw.elo_display  AS w_elo,
                pl.elo_display  AS l_elo,
                pw.elo_hard     AS w_elo_hard,
                pl.elo_hard     AS l_elo_hard
            FROM matches m
            JOIN players pw ON m.winner_id = pw.player_id
            JOIN players pl ON m.loser_id  = pl.player_id
            WHERE m.match_date <= %s
              AND LOWER(m.surface) = %s
              AND m.winner_id IS NOT NULL
              AND m.loser_id  IS NOT NULL
              AND pw.elo_display IS NOT NULL
              AND pl.elo_display IS NOT NULL
              AND pw.elo_hard    IS NOT NULL
              AND pl.elo_hard    IS NOT NULL
        """, (TRAINING_CUTOFF, SURFACE))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=cols)
    logger.info("Loaded %d training matches (hard court, ≤ %s)", len(df), TRAINING_CUTOFF)
    return df


# ─────────────────────────────────────────────────────────────
# Feature construction
# ─────────────────────────────────────────────────────────────

def build_symmetric_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build a balanced training set where every match appears twice:
      - Winner perspective  → label 1  (higher elo_diff should → higher P)
      - Loser perspective   → label 0  (same match, roles swapped)

    Column names match evaluate.py's feature matrix exactly so XGBoost
    stores the right feature names for inference.
    """
    # Winner perspective (label = 1)
    pos = pd.DataFrame({
        "winner_elo":      df["w_elo"].values,
        "loser_elo":       df["l_elo"].values,
        "winner_elo_hard": df["w_elo_hard"].values,
        "loser_elo_hard":  df["l_elo_hard"].values,
    })
    pos["elo_diff"]      = pos["winner_elo"] - pos["loser_elo"]
    pos["elo_diff_hard"] = pos["winner_elo_hard"] - pos["loser_elo_hard"]
    pos["y"] = 1

    # Loser perspective (label = 0): swap player A ↔ B
    neg = pd.DataFrame({
        "winner_elo":      df["l_elo"].values,
        "loser_elo":       df["w_elo"].values,
        "winner_elo_hard": df["l_elo_hard"].values,
        "loser_elo_hard":  df["w_elo_hard"].values,
    })
    neg["elo_diff"]      = neg["winner_elo"] - neg["loser_elo"]
    neg["elo_diff_hard"] = neg["winner_elo_hard"] - neg["loser_elo_hard"]
    neg["y"] = 0

    train = pd.concat([pos, neg], ignore_index=True).fillna(0)
    X = train[FEATURE_COLS]
    y = train["y"]

    logger.info("Training set: %d samples (%d positive, %d negative)",
                len(y), int(y.sum()), int((y == 0).sum()))
    return X, y


# ─────────────────────────────────────────────────────────────
# Win probability model
# ─────────────────────────────────────────────────────────────

def train_win_prob_model(X: pd.DataFrame, y: pd.Series):
    base = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=10,
        gamma=1.0,
        reg_lambda=2.0,
        eval_metric="logloss",
        random_state=42,
    )

    # 5-fold CV Brier score on the raw (uncalibrated) model
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_brier = cross_val_score(base, X, y, cv=cv, scoring="neg_brier_score")
    logger.info("5-fold CV Brier (uncalibrated): %.4f ± %.4f",
                -cv_brier.mean(), cv_brier.std())

    # Calibrate with Platt scaling (sigmoid). Isotonic regression overfits the
    # calibration mapping at sparse tail values, creating extreme frac_pos errors
    # on out-of-sample elite matchups. Sigmoid provides smoother, more stable
    # calibration across the full elo_diff range.
    model = CalibratedClassifierCV(base, cv=5, method="sigmoid")
    model.fit(X, y)

    # In-sample Brier after calibration
    probs = model.predict_proba(X)[:, 1]
    in_sample_brier = brier_score_loss(y, probs)
    logger.info("In-sample Brier (calibrated): %.4f", in_sample_brier)

    # Calibration curve check
    frac_pos, mean_pred = calibration_curve(y, probs, n_bins=10)
    cal_err = float(np.mean(np.abs(frac_pos - mean_pred)))
    logger.info("In-sample calibration error: %.4f", cal_err)

    return model


# ─────────────────────────────────────────────────────────────
# Next-shot model (placeholder — no charted data yet)
# ─────────────────────────────────────────────────────────────

def create_next_shot_placeholder() -> DummyClassifier:
    """
    Placeholder until MCP charted points are loaded.
    Returns integer class indices (0–5) matching SHOT_CLASSES.
    evaluate.py handles empty points gracefully so this is never called
    in practice, but it must be loadable and have a predict() interface.
    """
    model = DummyClassifier(strategy="stratified", random_state=42)
    # Seed with a minimal balanced dataset so all 6 classes are known
    X_seed = np.tile(np.arange(len(SHOT_CLASSES)).reshape(-1, 1), (1, 2))  # shape (6, 2)
    y_seed = np.arange(len(SHOT_CLASSES))
    model.fit(X_seed, y_seed)
    logger.info("Next-shot model: placeholder DummyClassifier (no charted data)")
    return model


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in .env")
        sys.exit(1)

    conn = psycopg2.connect(db_url, connect_timeout=30)
    df = load_training_matches(conn)
    conn.close()

    if len(df) < 100:
        logger.error("Too few training matches (%d) — check database", len(df))
        sys.exit(1)

    X, y = build_symmetric_dataset(df)

    logger.info("Training win probability model...")
    win_model = train_win_prob_model(X, y)

    logger.info("Creating next-shot placeholder model...")
    ns_model = create_next_shot_placeholder()

    # Save
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    wp_path = MODEL_DIR / "win_prob_model.pkl"
    ns_path = MODEL_DIR / "next_shot_model.pkl"

    with open(wp_path, "wb") as f:
        pickle.dump(win_model, f)
    logger.info("Saved %s", wp_path)

    with open(ns_path, "wb") as f:
        pickle.dump(ns_model, f)
    logger.info("Saved %s", ns_path)

    logger.info("Phase 5 complete.")


if __name__ == "__main__":
    main()
