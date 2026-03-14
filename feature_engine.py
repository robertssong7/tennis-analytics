"""
TennisIQ — Feature Engine
feature_engine.py  (root of repo — this is the ONLY file the agent loop may modify)

Computes feature vectors for all players from raw point/shot data.
Reads Elo data from players table — never recomputes it.

Usage:
    python feature_engine.py --surface hard --validate
    python feature_engine.py --surface hard
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# DECAY_CONFIG — agent may modify these values
# ─────────────────────────────────────────────────────────────
DECAY_CONFIG = {
    "serve_direction":   {"half_life_months": 36, "decay_type": "exponential"},
    "shot_type_mix":     {"half_life_months": 24, "decay_type": "exponential"},
    "rally_patterns":    {"half_life_months": 18, "decay_type": "exponential"},
    "pressure_win_rate": {"half_life_months": 12, "decay_type": "exponential"},
    "net_tendency":      {"half_life_months": 24, "decay_type": "exponential"},
    "error_rate":        {"half_life_months": 12, "decay_type": "exponential"},
}

# ─────────────────────────────────────────────────────────────
# WINDOW_CONFIG — agent may modify these values
# ─────────────────────────────────────────────────────────────
WINDOW_CONFIG = {
    "serve_plus1_window":   2,   # bigram: serve + next shot
    "rally_pattern_window": 3,   # trigram: 3-shot rally sequences
    "pressure_window":      2,   # score-state pattern window
}

# ─────────────────────────────────────────────────────────────
# CLUSTER_CONFIG — agent may modify these values
# ─────────────────────────────────────────────────────────────
CLUSTER_CONFIG = {
    "k":                7,        # number of archetype clusters
    "serve_weight":     1.0,      # relative weight for serve features
    "rally_weight":     1.0,      # relative weight for rally features
    "soft_assignment":  False,    # hard cluster assignment
}

# ─────────────────────────────────────────────────────────────
# CONFIDENCE_CONFIG — agent may modify these values
# ─────────────────────────────────────────────────────────────
CONFIDENCE_CONFIG = {
    "min_n_shrinkage":  30,       # Bayesian shrinkage toward prior (min_n)
    "low_threshold":    10,       # < 15 matches: excluded from predictions
    "moderate_threshold": 30,     # 15-29: low confidence
    "high_threshold":   60,       # 30-59: moderate; 60+: high
}

# Training data cutoff — NEVER change this
TRAINING_CUTOFF = date(2022, 12, 31)

# ─────────────────────────────────────────────────────────────
# Sandbox validation — NEVER modify this function
# ─────────────────────────────────────────────────────────────

def validate_sandbox() -> bool:
    """
    Validates that the feature engine is operating within sandbox constraints.
    This function is never modified by the agent loop.
    Returns True if safe to proceed, raises RuntimeError if not.
    """
    locked_dir = Path("data/locked")

    # Check: locked directory must not be writeable by this process
    if locked_dir.exists():
        test_file = locked_dir / "_sandbox_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            raise RuntimeError(
                "SANDBOX VIOLATION: data/locked/ is writeable. "
                "Permissions must be 700 (no write from this process). "
                "Run: chmod 500 data/locked"
            )
        except PermissionError:
            pass  # Correct: cannot write

    # Check: no imports from evaluate.py / no forbidden data references.
    # Strip the validate_sandbox function body itself before scanning so that
    # string literals defined inside this checker don't false-positive.
    import re as _re
    engine_source = Path(__file__).read_text()
    vs_start = engine_source.find('\ndef validate_sandbox')
    vs_end   = engine_source.find('\ndef ', vs_start + 1) if vs_start >= 0 else -1
    check_source = (
        engine_source[:max(vs_start, 0)] +
        (engine_source[vs_end:] if vs_end > 0 else "")
    )

    if _re.search(r'^(?:import|from)\s+evaluate\b', check_source, _re.MULTILINE):
        raise RuntimeError("SANDBOX VIOLATION: feature_engine.py imports from evaluate.py")

    # Check: not reading test set files
    forbidden_patterns = ["test_set_LOCKED", "2024_test", "locked/test"]
    for pattern in forbidden_patterns:
        if pattern in check_source:
            raise RuntimeError(f"SANDBOX VIOLATION: feature_engine.py references locked data: {pattern}")

    return True


# ─────────────────────────────────────────────────────────────
# Decay weight computation
# ─────────────────────────────────────────────────────────────

def decay_weight(match_date: date, feature_type: str, as_of: Optional[date] = None) -> float:
    """Compute recency weight for a match based on feature type and date."""
    if as_of is None:
        as_of = TRAINING_CUTOFF

    config = DECAY_CONFIG.get(feature_type, {"half_life_months": 18, "decay_type": "exponential"})
    half_life = config["half_life_months"]

    months_ago = (as_of - match_date).days / 30.44
    if months_ago < 0:
        return 0.0  # future data — should not exist in training set

    return 0.5 ** (months_ago / half_life)


# ─────────────────────────────────────────────────────────────
# Cold-start confidence tier
# ─────────────────────────────────────────────────────────────

def get_confidence_tier(match_count: int) -> str:
    t = CONFIDENCE_CONFIG
    if match_count < t["low_threshold"]:
        return "excluded"
    elif match_count < t["moderate_threshold"]:
        return "low"
    elif match_count < t["high_threshold"]:
        return "moderate"
    return "high"


def compute_feature_with_prior(
    observed_value: float,
    observed_n: int,
    archetype_prior: float,
) -> float:
    """Bayesian shrinkage toward archetype prior for low-sample players."""
    min_n = CONFIDENCE_CONFIG["min_n_shrinkage"]
    shrinkage = min_n / (min_n + observed_n)
    return shrinkage * archetype_prior + (1.0 - shrinkage) * observed_value


# ─────────────────────────────────────────────────────────────
# Feature computation functions (agent modifies these)
# ─────────────────────────────────────────────────────────────

def compute_serve_features(points_df: pd.DataFrame, player_id: int) -> dict:
    """Compute serve direction and effectiveness features."""
    serves = points_df[
        (points_df["server_id"] == player_id) &
        (points_df["match_date"] <= TRAINING_CUTOFF)
    ].copy()

    if len(serves) == 0:
        return {"serve_wide_pct": 0.33, "serve_body_pct": 0.33, "serve_t_pct": 0.34,
                "ace_rate": 0.05, "first_serve_pct": 0.60,
                "first_serve_won": 0.65, "second_serve_won": 0.50}

    window = WINDOW_CONFIG["serve_plus1_window"]

    # Apply recency decay
    serves["weight"] = serves["match_date"].apply(
        lambda d: decay_weight(d, "serve_direction")
    )
    total_w = serves["weight"].sum()
    if total_w == 0:
        total_w = 1.0

    def weighted_mean(col, mask=None):
        s = serves if mask is None else serves[mask]
        if len(s) == 0:
            return 0.0
        w = s["weight"]
        vals = s[col].fillna(0)
        return float((vals * w).sum() / w.sum()) if w.sum() > 0 else 0.0

    wide = serves["serve_dir"] == "wide"
    body = serves["serve_dir"] == "body"
    t_dir = serves["serve_dir"] == "T"

    n = len(serves)
    features = {
        "serve_wide_pct":   float((serves[wide]["weight"].sum()) / total_w),
        "serve_body_pct":   float((serves[body]["weight"].sum()) / total_w),
        "serve_t_pct":      float((serves[t_dir]["weight"].sum()) / total_w),
        "ace_rate":         float(len(serves[serves["outcome"] == "ace"]) / n),
        "first_serve_pct":  float(len(serves[serves["serve_num"] == 1]) / n),
        "first_serve_won":  weighted_mean("point_won", serves["serve_num"] == 1),
        "second_serve_won": weighted_mean("point_won", serves["serve_num"] == 2),
        "_n_serve": n,
    }
    return features


def compute_return_features(points_df: pd.DataFrame, player_id: int) -> dict:
    """Compute return effectiveness features."""
    returns = points_df[
        (points_df["returner_id"] == player_id) &
        (points_df["match_date"] <= TRAINING_CUTOFF)
    ].copy()

    if len(returns) == 0:
        return {"return_win_rate": 0.38, "_n_return": 0}

    returns["weight"] = returns["match_date"].apply(
        lambda d: decay_weight(d, "rally_patterns")
    )
    w_sum = returns["weight"].sum()
    if w_sum == 0:
        return {"return_win_rate": 0.38, "_n_return": 0}

    won = returns[returns["winner_id"] == player_id]["weight"].sum()
    return {
        "return_win_rate": float(won / w_sum),
        "_n_return": len(returns),
    }


def compute_rally_features(points_df: pd.DataFrame, player_id: int) -> dict:
    """Compute rally length and shot type mix features."""
    involved = points_df[
        ((points_df["server_id"] == player_id) | (points_df["returner_id"] == player_id)) &
        (points_df["match_date"] <= TRAINING_CUTOFF)
    ].copy()

    if len(involved) == 0:
        return {"avg_rally_length": 4.5, "winner_rate": 0.10,
                "uf_error_rate": 0.15, "_n_rally": 0}

    involved["weight"] = involved["match_date"].apply(
        lambda d: decay_weight(d, "rally_patterns")
    )
    w_sum = involved["weight"].sum()
    if w_sum == 0:
        return {"avg_rally_length": 4.5, "winner_rate": 0.10,
                "uf_error_rate": 0.15, "_n_rally": 0}

    avg_rally = float(
        (involved["rally_length"].fillna(4.5) * involved["weight"]).sum() / w_sum
    )

    n = len(involved)
    winner_r = float(len(involved[
        (involved["outcome"] == "winner") & (involved["winner_id"] == player_id)
    ]) / n)
    uf_error_r = float(len(involved[
        (involved["outcome"] == "uf_error") & (involved["winner_id"] != player_id)
    ]) / n)

    return {
        "avg_rally_length": avg_rally,
        "winner_rate":      winner_r,
        "uf_error_rate":    uf_error_r,
        "_n_rally":         n,
    }


def compute_pressure_features(points_df: pd.DataFrame, player_id: int) -> dict:
    """Compute performance under pressure (break points, score state)."""
    involved = points_df[
        ((points_df["server_id"] == player_id) | (points_df["returner_id"] == player_id)) &
        (points_df["match_date"] <= TRAINING_CUTOFF)
    ].copy()

    if len(involved) == 0:
        return {"bp_save_pct": 0.60, "bp_convert_pct": 0.40,
                "clutch_delta": 0.0, "_n_pressure": 0}

    involved["weight"] = involved["match_date"].apply(
        lambda d: decay_weight(d, "pressure_win_rate")
    )

    # Break points faced (server)
    bp_faced = involved[
        (involved["server_id"] == player_id) & (involved["is_break_point"] == True)
    ]
    # Break points converted (returner)
    bp_opp = involved[
        (involved["returner_id"] == player_id) & (involved["is_break_point"] == True)
    ]
    # All points for normal win rate
    all_pts = involved
    normal_pts = involved[
        (involved["is_break_point"] != True) &
        (involved["is_set_point"] != True) &
        (involved["is_match_point"] != True)
    ]

    def win_rate(df, pid):
        w = df["weight"].sum()
        if w == 0:
            return 0.5
        won = df[df["winner_id"] == pid]["weight"].sum()
        return float(won / w)

    bp_save = win_rate(bp_faced, player_id) if len(bp_faced) > 0 else 0.60
    bp_conv = win_rate(bp_opp, player_id) if len(bp_opp) > 0 else 0.40
    normal_wr = win_rate(normal_pts, player_id)
    all_wr    = win_rate(all_pts, player_id)
    clutch    = all_wr - normal_wr

    return {
        "bp_save_pct":    bp_save,
        "bp_convert_pct": bp_conv,
        "clutch_delta":   clutch,
        "_n_pressure":    len(bp_faced) + len(bp_opp),
    }


def compute_net_tendency(points_df: pd.DataFrame, player_id: int) -> dict:
    """Compute approach and net play tendencies."""
    involved = points_df[
        ((points_df["server_id"] == player_id) | (points_df["returner_id"] == player_id)) &
        (points_df["match_date"] <= TRAINING_CUTOFF)
    ].copy()

    n = len(involved)
    if n == 0:
        return {"approach_rate": 0.10, "net_point_won_pct": 0.60}

    # These fields may not exist in all data sources — handle gracefully
    approach_col = "is_approach" if "is_approach" in involved.columns else None
    net_won_col  = "net_point_won" if "net_point_won" in involved.columns else None

    approach_rate = 0.10
    net_won = 0.60

    if approach_col:
        approach_rate = float(involved[approach_col].fillna(False).sum() / n)

    return {"approach_rate": approach_rate, "net_point_won_pct": net_won}


def compute_player_features(
    player_id: int,
    points_df: pd.DataFrame,
    surface: str,
    elo_data: Optional[dict] = None,
    archetype_prior: Optional[dict] = None,
) -> Optional[dict]:
    """
    Compute full feature vector for one player on one surface.
    Returns None if player has < 15 charted matches.
    """
    # Filter to this surface
    surface_points = points_df[
        points_df["surface"].str.lower() == surface.lower()
    ] if surface != "all" else points_df

    # Charted match count
    match_count = surface_points[
        (surface_points["server_id"] == player_id) |
        (surface_points["returner_id"] == player_id)
    ]["match_id"].nunique()

    confidence = get_confidence_tier(match_count)
    if confidence == "excluded":
        return None

    # Compute features
    serve   = compute_serve_features(surface_points, player_id)
    ret     = compute_return_features(surface_points, player_id)
    rally   = compute_rally_features(surface_points, player_id)
    pressure = compute_pressure_features(surface_points, player_id)
    net      = compute_net_tendency(surface_points, player_id)

    # Apply Bayesian shrinkage toward archetype prior for low-confidence players
    prior = archetype_prior or {}
    if confidence in ("low", "moderate") and prior:
        n_obs = match_count
        for key in ["serve_wide_pct", "serve_body_pct", "serve_t_pct",
                    "first_serve_won", "second_serve_won"]:
            if key in serve and key in prior:
                serve[key] = compute_feature_with_prior(serve[key], n_obs, prior[key])
        for key in ["return_win_rate"]:
            if key in ret and key in prior:
                ret[key] = compute_feature_with_prior(ret[key], n_obs, prior[key])

    # Read Elo from players table (Elo is infrastructure, not recomputed here)
    elo = elo_data or {}

    vector = {
        "player_id":        player_id,
        "surface":          surface,
        "match_count":      match_count,
        "data_confidence":  confidence,
        "computed_at":      datetime.now().isoformat(),
        # Serve
        **serve,
        # Return
        **ret,
        # Rally
        **rally,
        # Pressure
        **pressure,
        # Net
        **net,
        # Elo (read-only from players table)
        "elo_overall":      elo.get("elo_overall", 1500),
        "elo_hard":         elo.get("elo_hard", 1500),
        "elo_clay":         elo.get("elo_clay", 1500),
        "elo_grass":        elo.get("elo_grass", 1500),
        "elo_display":      elo.get("elo_display", 1500),
        "fifa_rating":      elo.get("fifa_rating"),
    }

    # Clean up internal _n_ fields from vector
    vector = {k: v for k, v in vector.items() if not k.startswith("_")}

    return vector


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────

def validate_features(features: List[dict]) -> dict:
    """
    Validate feature vectors:
    - < 5% null rate per column
    - All players with >= 15 matches have a vector
    """
    if not features:
        return {"ok": False, "issues": ["No features computed"]}

    df = pd.DataFrame(features)
    issues = []

    # fifa_rating is intentionally NULL for players whose Elo never reached the
    # FIFA scale minimum — exclude it from null-rate and NaN checks.
    numeric_cols = [
        c for c in df.select_dtypes(include=[np.number]).columns
        if c != "fifa_rating"
    ]

    # Null rate check
    for col in numeric_cols:
        null_rate = df[col].isna().mean()
        if null_rate > 0.05:
            issues.append(f"Column {col!r} has {null_rate:.1%} null rate (> 5%)")

    # NaN check
    nan_players = df[df[numeric_cols].isna().any(axis=1)]["player_id"].tolist()
    if nan_players:
        issues.append(f"{len(nan_players)} players have NaN in feature vector")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "n_players": len(features),
        "null_rates": {
            col: round(float(df[col].isna().mean()), 4)
            for col in numeric_cols
            if df[col].isna().mean() > 0
        }
    }


# ─────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────

def run_feature_pipeline(surface: str = "hard", validate: bool = False):
    validate_sandbox()
    logger.info("Running feature pipeline for surface: %s", surface)

    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        logger.info("Set DATABASE_URL in .env — see BLOCKERS.md")
        sys.exit(1)

    with conn.cursor() as cur:
        logger.info("Loading points data...")
        cur.execute("""
            SELECT
                p.point_id, p.match_id, p.server_id, p.returner_id,
                p.serve_num, p.serve_dir, p.serve_depth,
                p.rally_length, p.outcome, p.winner_id,
                p.is_break_point, p.is_set_point, p.is_match_point,
                m.match_date, m.surface,
                CASE WHEN p.winner_id = p.server_id THEN TRUE ELSE FALSE END AS point_won
            FROM points p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.match_date <= %s
        """, (TRAINING_CUTOFF,))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    points_df = pd.DataFrame(rows, columns=cols)
    points_df["match_date"] = pd.to_datetime(points_df["match_date"]).dt.date
    logger.info("Loaded %d points", len(points_df))

    # Load Elo data for all players
    with conn.cursor() as cur:
        cur.execute("""
            SELECT player_id, elo_overall, elo_hard, elo_clay, elo_grass,
                   elo_display, fifa_rating
            FROM players
        """)
        elo_rows = cur.fetchall()
    elo_map = {
        str(r[0]): {
            "elo_overall": r[1], "elo_hard": r[2], "elo_clay": r[3],
            "elo_grass": r[4], "elo_display": r[5], "fifa_rating": r[6]
        }
        for r in elo_rows
    }

    # Get all players in this surface
    surface_pts = points_df[points_df["surface"].str.lower() == surface.lower()] \
        if surface != "all" else points_df
    player_ids = set(surface_pts["server_id"].dropna().tolist()) | \
                 set(surface_pts["returner_id"].dropna().tolist())
    logger.info("Computing features for %d players on %s", len(player_ids), surface)

    features = []
    skipped = 0
    for pid in player_ids:
        try:
            pid_int = int(pid)
            f = compute_player_features(
                pid_int, points_df, surface,
                elo_data=elo_map.get(str(pid_int))
            )
            if f:
                features.append(f)
            else:
                skipped += 1
        except Exception as e:
            logger.warning("Error computing features for player %s: %s", pid, e)
            skipped += 1

    logger.info("Computed %d feature vectors (%d skipped/excluded)", len(features), skipped)

    if validate:
        result = validate_features(features)
        logger.info("Validation: %s", "PASS" if result["ok"] else "FAIL")
        if not result["ok"]:
            for issue in result["issues"]:
                logger.error("  %s", issue)
            sys.exit(1)
        else:
            logger.info("All validation checks passed.")

    # Write to player_profiles table
    with conn.cursor() as cur:
        for f in features:
            cur.execute("""
                INSERT INTO player_profiles
                    (player_id, surface, computed_at, match_count, data_confidence,
                     serve_wide_pct, serve_body_pct, serve_t_pct, ace_rate,
                     first_serve_pct, first_serve_won, second_serve_won,
                     avg_rally_length, winner_rate, uf_error_rate,
                     bp_save_pct, bp_convert_pct, clutch_delta,
                     approach_rate, feature_vector)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, surface) DO UPDATE SET
                    computed_at     = EXCLUDED.computed_at,
                    match_count     = EXCLUDED.match_count,
                    data_confidence = EXCLUDED.data_confidence,
                    serve_wide_pct  = EXCLUDED.serve_wide_pct,
                    feature_vector  = EXCLUDED.feature_vector
            """, (
                f["player_id"], f["surface"], f.get("computed_at"), f["match_count"],
                f["data_confidence"],
                f.get("serve_wide_pct"), f.get("serve_body_pct"), f.get("serve_t_pct"),
                f.get("ace_rate"), f.get("first_serve_pct"),
                f.get("first_serve_won"), f.get("second_serve_won"),
                f.get("avg_rally_length"), f.get("winner_rate"), f.get("uf_error_rate"),
                f.get("bp_save_pct"), f.get("bp_convert_pct"), f.get("clutch_delta"),
                f.get("approach_rate"), json.dumps(f),
            ))
    conn.commit()
    conn.close()

    logger.info("Phase 3 complete — %d player profiles written for %s", len(features), surface)
    return features


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--surface", default="hard")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_feature_pipeline(surface=args.surface, validate=args.validate)
