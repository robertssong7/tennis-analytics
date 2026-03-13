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

# evaluate.py builds X with exactly these column names in this order.
# Elo features come from the players table; profile features come from
# player_profiles (written by feature_engine.py).  p1 = focal player,
# p2 = opponent.  Both perspectives are present in the symmetric dataset.
FEATURE_COLS = [
    # Elo
    "winner_elo", "loser_elo", "winner_elo_hard", "loser_elo_hard",
    "elo_diff", "elo_diff_hard",
    # Focal-player profile
    "p1_serve_wide_pct", "p1_first_serve_won", "p1_bp_save_pct",
    "p1_return_win_rate", "p1_rally_win_rate",
    # Opponent profile
    "p2_serve_wide_pct", "p2_first_serve_won", "p2_bp_save_pct",
    "p2_return_win_rate", "p2_rally_win_rate",
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
    """Load matches with Elo + player_profiles features.
    LEFT JOIN on profiles so all Elo-valid matches are kept;
    missing profile values fill to 0 in build_symmetric_dataset.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                m.match_id,
                m.match_date,
                pw.elo_display  AS w_elo,
                pl.elo_display  AS l_elo,
                pw.elo_hard     AS w_elo_hard,
                pl.elo_hard     AS l_elo_hard,
                -- Winner profile (may be NULL for players with < 15 charted matches)
                ppw.serve_wide_pct  AS w_serve_wide_pct,
                ppw.first_serve_won AS w_first_serve_won,
                ppw.bp_save_pct     AS w_bp_save_pct,
                (ppw.feature_vector->>'return_win_rate')::float AS w_return_win_rate,
                ppw.winner_rate     AS w_rally_win_rate,
                -- Loser profile
                ppl.serve_wide_pct  AS l_serve_wide_pct,
                ppl.first_serve_won AS l_first_serve_won,
                ppl.bp_save_pct     AS l_bp_save_pct,
                (ppl.feature_vector->>'return_win_rate')::float AS l_return_win_rate,
                ppl.winner_rate     AS l_rally_win_rate
            FROM matches m
            JOIN players pw ON m.winner_id = pw.player_id
            JOIN players pl ON m.loser_id  = pl.player_id
            LEFT JOIN player_profiles ppw
                   ON ppw.player_id = m.winner_id AND ppw.surface = %s
            LEFT JOIN player_profiles ppl
                   ON ppl.player_id = m.loser_id  AND ppl.surface = %s
            WHERE m.match_date <= %s
              AND LOWER(m.surface) = %s
              AND m.winner_id IS NOT NULL
              AND m.loser_id  IS NOT NULL
              AND pw.elo_display IS NOT NULL
              AND pl.elo_display IS NOT NULL
              AND pw.elo_hard    IS NOT NULL
              AND pl.elo_hard    IS NOT NULL
        """, (SURFACE, SURFACE, TRAINING_CUTOFF, SURFACE))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=cols)
    n_with_profile = df["w_serve_wide_pct"].notna().sum()
    logger.info("Loaded %d training matches (hard court, ≤ %s); %d winner profiles, %d loser profiles",
                len(df), TRAINING_CUTOFF, n_with_profile, df["l_serve_wide_pct"].notna().sum())
    return df


# ─────────────────────────────────────────────────────────────
# Feature construction
# ─────────────────────────────────────────────────────────────

def build_symmetric_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build a balanced training set where every match appears twice:
      - Winner perspective  (p1=winner, p2=loser)  → label 1
      - Loser perspective   (p1=loser,  p2=winner) → label 0

    Column names match evaluate.py's feature matrix exactly so XGBoost
    stores the right feature names for inference.  Profile features fill to
    0 for the ~97% of matches without charted data (LEFT JOIN).
    """
    def _make_half(focal_pfx, opp_pfx, label):
        d = pd.DataFrame({
            "winner_elo":         df[f"{focal_pfx}_elo"].values,
            "loser_elo":          df[f"{opp_pfx}_elo"].values,
            "winner_elo_hard":    df[f"{focal_pfx}_elo_hard"].values,
            "loser_elo_hard":     df[f"{opp_pfx}_elo_hard"].values,
            # focal profile
            "p1_serve_wide_pct":  df[f"{focal_pfx}_serve_wide_pct"].values,
            "p1_first_serve_won": df[f"{focal_pfx}_first_serve_won"].values,
            "p1_bp_save_pct":     df[f"{focal_pfx}_bp_save_pct"].values,
            "p1_return_win_rate": df[f"{focal_pfx}_return_win_rate"].values,
            "p1_rally_win_rate":  df[f"{focal_pfx}_rally_win_rate"].values,
            # opponent profile
            "p2_serve_wide_pct":  df[f"{opp_pfx}_serve_wide_pct"].values,
            "p2_first_serve_won": df[f"{opp_pfx}_first_serve_won"].values,
            "p2_bp_save_pct":     df[f"{opp_pfx}_bp_save_pct"].values,
            "p2_return_win_rate": df[f"{opp_pfx}_return_win_rate"].values,
            "p2_rally_win_rate":  df[f"{opp_pfx}_rally_win_rate"].values,
        })
        d["elo_diff"]      = d["winner_elo"] - d["loser_elo"]
        d["elo_diff_hard"] = d["winner_elo_hard"] - d["loser_elo_hard"]
        d["y"] = label
        return d

    pos = _make_half("w", "l", 1)   # winner as focal player
    neg = _make_half("l", "w", 0)   # loser  as focal player

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
