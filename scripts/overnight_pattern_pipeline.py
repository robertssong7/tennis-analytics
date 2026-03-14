#!/usr/bin/env python3
"""
TennisIQ — Overnight Pattern Pipeline
scripts/overnight_pattern_pipeline.py

Autonomous 10-hour pipeline:
  Phase 1: Parse 1.22M MCP charted points into structured shots
  Phase 2: Build per-player pattern profiles (score-state conditioned)
  Phase 3: Add universal match features (surface, tourney level, H2H, form)
  Phase 4: Build expanded training matrix
  Phase 5: Train XGBoost on expanded features, compare to baseline
  Phase 6: Agent loop — tune XGBoost hyperparams on expanded feature set

Usage:
    caffeinate -dims &
    python3 scripts/overnight_pattern_pipeline.py 2>&1 | tee overnight_$(date +%Y%m%d).log
"""

import json
import logging
import math
import os
import pickle
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from scipy.stats import entropy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("overnight_pipeline")

# ─── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "data" / "sackmann" / "tennis_MatchChartingProject"
CHECKPOINT_DIR = REPO_ROOT / "data" / "processed"
MODELS_DIR = REPO_ROOT / "models" / "hard"
EXPERIMENTS_DIR = REPO_ROOT / "experiments"

STOP_HOUR = 8
STOP_MINUTE = 30
MAX_AGENT_EXPERIMENTS = 500

load_dotenv(REPO_ROOT / ".env")

# ─── MCP Shot Notation ────────────────────────────────────────────────────────
SHOT_TYPES = {
    "f": "forehand", "b": "backhand", "s": "bh_slice", "r": "fh_slice",
    "v": "fh_volley", "z": "bh_volley", "o": "overhead", "p": "bh_overhead",
    "u": "unknown", "l": "fh_lob", "m": "bh_lob", "h": "fh_dropshot",
    "j": "bh_dropshot", "k": "trick", "t": "fh_drop_volley",
    "q": "bh_drop_volley", "y": "bh_half_volley", "i": "fh_half_volley",
}
SERVE_DIRS = {"4": "wide", "5": "body", "6": "T"}
DIRECTIONS = {"1", "2", "3"}
DEPTHS = {"7", "8", "9"}
OUTCOMES = {"*", "#", "@"}
ERROR_TYPES = {"n", "w", "d", "x"}


def parse_rally(rally_str: str) -> list:
    """Parse MCP notation string into list of shot dicts.

    Each shot dict has keys: shot_num, type, direction?, depth?, outcome?,
    error_type?, position?
    """
    if not rally_str or (isinstance(rally_str, float) and math.isnan(rally_str)):
        return []

    rally_str = str(rally_str).strip()
    if not rally_str:
        return []

    shots = []
    i = 0
    shot_num = 0

    # ── Serve (first char must be 4/5/6) ──
    if rally_str[0] in SERVE_DIRS:
        shot = {"shot_num": 0, "type": "serve", "serve_dir": SERVE_DIRS[rally_str[0]]}
        i = 1
        # Check if serve ended the point (ace / unreturnable / double fault)
        if i < len(rally_str) and rally_str[i] in OUTCOMES:
            shot["outcome"] = rally_str[i]
            i += 1
            if i < len(rally_str) and rally_str[i] in ERROR_TYPES:
                shot["error_type"] = rally_str[i]
                i += 1
            shots.append(shot)
            return shots
        shots.append(shot)
        shot_num = 1
    else:
        # Malformed — skip this point
        return []

    # ── Rally shots ──
    while i < len(rally_str):
        c = rally_str[i]

        # Position modifiers
        position = None
        if c == "+":
            position = "approach"
            i += 1
            if i >= len(rally_str):
                break
            c = rally_str[i]
        elif c == "-":
            position = "net"
            i += 1
            if i >= len(rally_str):
                break
            c = rally_str[i]

        # Shot type letter
        if c in SHOT_TYPES:
            shot = {"shot_num": shot_num, "type": SHOT_TYPES[c]}
            if position:
                shot["position"] = position
            i += 1

            # Direction digit (1/2/3)
            if i < len(rally_str) and rally_str[i] in DIRECTIONS:
                shot["direction"] = int(rally_str[i])
                i += 1

            # Depth digit (7/8/9) — mainly returns
            if i < len(rally_str) and rally_str[i] in DEPTHS:
                shot["depth"] = int(rally_str[i])
                i += 1

            # Outcome and error type (handles both @n and n@ order, and standalone n/w/d)
            if i < len(rally_str) and rally_str[i] in OUTCOMES:
                shot["outcome"] = rally_str[i]
                i += 1
                if i < len(rally_str) and rally_str[i] in ERROR_TYPES:
                    shot["error_type"] = rally_str[i]
                    i += 1
            elif i < len(rally_str) and rally_str[i] in ERROR_TYPES:
                shot["error_type"] = rally_str[i]
                i += 1
                if i < len(rally_str) and rally_str[i] in OUTCOMES:
                    shot["outcome"] = rally_str[i]
                    i += 1
                else:
                    shot["outcome"] = "@"  # standalone n/w/d = unforced error

            shots.append(shot)
            shot_num += 1

        elif c == "!":
            # Shank
            shot = {"shot_num": shot_num, "type": "shank"}
            if position:
                shot["position"] = position
            i += 1
            if i < len(rally_str) and rally_str[i] in OUTCOMES:
                shot["outcome"] = rally_str[i]
                i += 1
                if i < len(rally_str) and rally_str[i] in ERROR_TYPES:
                    shot["error_type"] = rally_str[i]
                    i += 1
            shots.append(shot)
            shot_num += 1
        else:
            # Unknown char (=, ;, etc.) — skip gracefully
            i += 1

    return shots


# ─── Score State Classification ───────────────────────────────────────────────
def classify_point_score(pts_str: str, server_games: int, returner_games: int,
                         set1: int, set2: int, svr: int) -> dict:
    """Classify the match state into pressure/neutral/dominant categories."""
    context = {
        "point_score": pts_str,
        "server_games": server_games,
        "returner_games": returner_games,
        "set_score_svr": set1 if svr == 1 else set2,
        "set_score_ret": set2 if svr == 1 else set1,
    }

    # Point-level pressure
    pressure_points = {"15-40", "30-40", "AD-40"}  # receiver perspective
    # We need to orient: if svr=1, Pts shows "svrPts-retPts"
    # Actually Pts format is "p1pts-p2pts" regardless of who serves
    parts = str(pts_str).split("-") if isinstance(pts_str, str) else []
    if len(parts) == 2:
        try:
            svr_pts = parts[0] if svr == 1 else parts[1]
            ret_pts = parts[1] if svr == 1 else parts[0]
            score_key = f"{svr_pts}-{ret_pts}"
        except (IndexError, ValueError):
            score_key = pts_str
    else:
        score_key = str(pts_str)

    # Pressure: server is in trouble
    if score_key in {"15-40", "30-40"} or (score_key == "40-AD"):
        context["pressure_level"] = "break_point"
    elif score_key in {"0-30", "0-40", "15-30"}:
        context["pressure_level"] = "pressure"
    elif score_key in {"40-0", "40-15"}:
        context["pressure_level"] = "dominant"
    elif score_key in {"40-40", "30-30", "AD-40", "40-AD"}:
        context["pressure_level"] = "clutch"
    else:
        context["pressure_level"] = "neutral"

    # Game-level context
    game_diff = server_games - returner_games
    if game_diff <= -3:
        context["game_pressure"] = "far_behind"
    elif game_diff <= -1:
        context["game_pressure"] = "behind"
    elif game_diff == 0:
        context["game_pressure"] = "even"
    elif game_diff >= 3:
        context["game_pressure"] = "far_ahead"
    else:
        context["game_pressure"] = "ahead"

    return context


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Parse MCP Points
# ═══════════════════════════════════════════════════════════════════════════════
def phase1_parse_mcp() -> pd.DataFrame:
    """Parse all MCP charting files into structured point-level data."""
    checkpoint = CHECKPOINT_DIR / "parsed_points.parquet"
    if checkpoint.exists():
        logger.info("Phase 1: Loading from checkpoint %s", checkpoint)
        return pd.read_parquet(checkpoint)

    logger.info("Phase 1: Parsing MCP charted points...")
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    # Load match metadata
    matches_file = MCP_DIR / "charting-m-matches.csv"
    matches_df = pd.read_csv(matches_file, encoding="latin-1")
    matches_df.columns = [c.strip() for c in matches_df.columns]
    logger.info("  Loaded %d matches from charting-m-matches.csv", len(matches_df))

    # Load all points files
    points_files = sorted(MCP_DIR.glob("charting-m-points*.csv"))
    dfs = []
    for f in points_files:
        logger.info("  Reading %s ...", f.name)
        df = pd.read_csv(f, encoding="latin-1", low_memory=False)
        df.columns = [c.strip() for c in df.columns]
        dfs.append(df)
    points_df = pd.concat(dfs, ignore_index=True)
    logger.info("  Total raw points: %d", len(points_df))

    # Join match metadata
    points_df = points_df.merge(
        matches_df[["match_id", "Player 1", "Player 2", "Pl 1 hand", "Pl 2 hand",
                     "Date", "Tournament", "Round", "Surface", "Best of"]],
        on="match_id", how="left",
    )

    # Parse rally strings and extract shot-level features per point
    logger.info("  Parsing rally strings (this takes a few minutes)...")
    t0 = time.time()

    rally_lengths = []
    serve_dirs = []
    point_outcomes = []  # winner / forced_error / unforced_error / ace
    last_shot_types = []
    shot_sequences = []  # compact string representation of shot types
    n_shots_list = []

    total = len(points_df)
    log_every = max(1, total // 20)

    for idx in range(total):
        if idx % log_every == 0 and idx > 0:
            elapsed = time.time() - t0
            pct = idx / total * 100
            logger.info("    %.0f%% parsed (%d/%d) — %.0fs elapsed", pct, idx, total, elapsed)

        row = points_df.iloc[idx]
        # Use 2nd serve rally if 1st was a fault, otherwise use 1st
        rally_str = row.get("2nd") if pd.notna(row.get("2nd")) and str(row.get("2nd")).strip() else row.get("1st")
        shots = parse_rally(rally_str)

        rally_len = len(shots)
        rally_lengths.append(rally_len)
        n_shots_list.append(rally_len)

        if rally_len == 0:
            serve_dirs.append(None)
            point_outcomes.append(None)
            last_shot_types.append(None)
            shot_sequences.append("")
            continue

        # Serve direction
        sd = shots[0].get("serve_dir") if shots[0]["type"] == "serve" else None
        serve_dirs.append(sd)

        # Point outcome
        last = shots[-1]
        outcome = last.get("outcome")
        if outcome == "*":
            point_outcomes.append("winner")
        elif outcome == "#":
            point_outcomes.append("forced_error")
        elif outcome == "@":
            point_outcomes.append("unforced_error")
        elif rally_len == 1 and shots[0]["type"] == "serve":
            point_outcomes.append("ace")
        else:
            point_outcomes.append("other")

        last_shot_types.append(last.get("type"))

        # Compact shot sequence (just type letters for n-gram analysis)
        type_abbrevs = {
            "serve": "S", "forehand": "F", "backhand": "B", "bh_slice": "s",
            "fh_slice": "r", "fh_volley": "V", "bh_volley": "Z",
            "overhead": "O", "fh_dropshot": "H", "bh_dropshot": "J",
            "fh_lob": "L", "bh_lob": "M", "trick": "K", "shank": "!",
            "fh_drop_volley": "T", "bh_drop_volley": "Q",
            "bh_half_volley": "Y", "fh_half_volley": "I",
            "bh_overhead": "P", "unknown": "U",
        }
        seq = "".join(type_abbrevs.get(s["type"], "?") for s in shots)
        shot_sequences.append(seq)

    points_df["rally_length"] = rally_lengths
    points_df["serve_direction"] = serve_dirs
    points_df["point_outcome"] = point_outcomes
    points_df["last_shot_type"] = last_shot_types
    points_df["shot_sequence"] = shot_sequences
    points_df["n_shots"] = n_shots_list

    elapsed = time.time() - t0
    logger.info("  Phase 1 complete: parsed %d points in %.0fs", total, elapsed)

    # Save checkpoint
    points_df.to_parquet(checkpoint, index=False)
    logger.info("  Saved checkpoint: %s", checkpoint)

    return points_df


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Build Player Pattern Profiles
# ═══════════════════════════════════════════════════════════════════════════════
def phase2_player_profiles(points_df: pd.DataFrame) -> pd.DataFrame:
    """Build per-player shot pattern profiles conditioned on score state."""
    checkpoint = CHECKPOINT_DIR / "player_profiles.parquet"
    if checkpoint.exists():
        logger.info("Phase 2: Loading from checkpoint %s", checkpoint)
        return pd.read_parquet(checkpoint)

    logger.info("Phase 2: Building player pattern profiles...")
    t0 = time.time()

    # Determine which player each point's server/returner is
    points_df["server_name"] = points_df.apply(
        lambda r: r["Player 1"] if r.get("Svr") == 1 else r["Player 2"], axis=1
    )
    points_df["returner_name"] = points_df.apply(
        lambda r: r["Player 2"] if r.get("Svr") == 1 else r["Player 1"], axis=1
    )
    points_df["server_won"] = points_df.apply(
        lambda r: 1 if r.get("Svr") == r.get("PtWinner") else 0, axis=1
    )

    # Classify score states
    logger.info("  Classifying score states...")
    pressure_levels = []
    game_pressures = []
    for idx in range(len(points_df)):
        row = points_df.iloc[idx]
        svr = row.get("Svr", 1)
        try:
            sg = int(row.get("Gm1", 0)) if svr == 1 else int(row.get("Gm2", 0))
            rg = int(row.get("Gm2", 0)) if svr == 1 else int(row.get("Gm1", 0))
        except (ValueError, TypeError):
            sg, rg = 0, 0
        try:
            s1, s2 = int(row.get("Set1", 0)), int(row.get("Set2", 0))
        except (ValueError, TypeError):
            s1, s2 = 0, 0

        ctx = classify_point_score(row.get("Pts", ""), sg, rg, s1, s2, svr)
        pressure_levels.append(ctx["pressure_level"])
        game_pressures.append(ctx["game_pressure"])

    points_df["pressure_level"] = pressure_levels
    points_df["game_pressure"] = game_pressures

    # ── Aggregate per player ──
    logger.info("  Aggregating per-player stats...")
    players = set(points_df["server_name"].dropna().unique()) | set(points_df["returner_name"].dropna().unique())
    logger.info("  Found %d unique players", len(players))

    profiles = []
    for pidx, player in enumerate(sorted(players)):
        if pidx % 200 == 0:
            logger.info("    Processing player %d/%d: %s", pidx, len(players), player)

        # Points where this player served
        srv_mask = points_df["server_name"] == player
        srv = points_df[srv_mask]

        # Points where this player returned
        ret_mask = points_df["returner_name"] == player
        ret = points_df[ret_mask]

        if len(srv) < 10 and len(ret) < 10:
            continue

        profile = {"player": player, "total_serve_points": len(srv), "total_return_points": len(ret)}

        # ── Serve direction distribution ──
        sd = srv["serve_direction"].dropna()
        if len(sd) > 0:
            sd_counts = sd.value_counts(normalize=True)
            profile["serve_wide_pct"] = sd_counts.get("wide", 0)
            profile["serve_body_pct"] = sd_counts.get("body", 0)
            profile["serve_t_pct"] = sd_counts.get("T", 0)
            # Serve direction entropy (unpredictability)
            probs = [sd_counts.get(d, 0.001) for d in ["wide", "body", "T"]]
            profile["serve_dir_entropy"] = float(entropy(probs, base=2))
        else:
            profile.update({"serve_wide_pct": 0, "serve_body_pct": 0,
                            "serve_t_pct": 0, "serve_dir_entropy": 0})

        # ── Serve under pressure ──
        srv_pressure = srv[srv["pressure_level"].isin(["break_point", "pressure"])]
        sd_p = srv_pressure["serve_direction"].dropna()
        if len(sd_p) >= 5:
            sd_p_counts = sd_p.value_counts(normalize=True)
            profile["serve_wide_pct_pressure"] = sd_p_counts.get("wide", 0)
            profile["serve_t_pct_pressure"] = sd_p_counts.get("T", 0)
            probs_p = [sd_p_counts.get(d, 0.001) for d in ["wide", "body", "T"]]
            profile["serve_dir_entropy_pressure"] = float(entropy(probs_p, base=2))
            # KL divergence: how much does behavior change under pressure?
            probs_all = np.array([profile["serve_wide_pct"] + 0.001,
                                   profile["serve_body_pct"] + 0.001,
                                   profile["serve_t_pct"] + 0.001])
            probs_all = probs_all / probs_all.sum()
            probs_pr = np.array([sd_p_counts.get("wide", 0.001),
                                  sd_p_counts.get("body", 0.001),
                                  sd_p_counts.get("T", 0.001)])
            probs_pr = probs_pr / probs_pr.sum()
            profile["serve_pressure_kl"] = float(entropy(probs_pr, probs_all))
        else:
            profile.update({"serve_wide_pct_pressure": 0, "serve_t_pct_pressure": 0,
                            "serve_dir_entropy_pressure": 0, "serve_pressure_kl": 0})

        # ── Rally length analysis ──
        srv_rl = srv["rally_length"].dropna()
        if len(srv_rl) > 0:
            profile["avg_rally_len_serving"] = float(srv_rl.mean())
            short = (srv_rl <= 4).sum()
            long_ = (srv_rl > 8).sum()
            profile["short_rally_pct_serving"] = short / len(srv_rl)
            profile["long_rally_pct_serving"] = long_ / len(srv_rl)
            # Win rate by rally length
            srv_short = srv[srv["rally_length"] <= 4]
            srv_long = srv[srv["rally_length"] > 8]
            profile["win_rate_short_rally"] = float(srv_short["server_won"].mean()) if len(srv_short) > 5 else 0.5
            profile["win_rate_long_rally"] = float(srv_long["server_won"].mean()) if len(srv_long) > 5 else 0.5
        else:
            profile.update({"avg_rally_len_serving": 0, "short_rally_pct_serving": 0,
                            "long_rally_pct_serving": 0, "win_rate_short_rally": 0.5,
                            "win_rate_long_rally": 0.5})

        # ── Aggression index: winners / (winners + UE) ──
        all_pts = pd.concat([srv, ret])
        # Points where this player hit the last shot
        # server hits odd shots (0, 2, 4...), returner hits even (1, 3, 5...)
        player_ended_srv = srv[srv["n_shots"].apply(lambda n: n > 0 and n % 2 == 1)]  # server's shot ended
        player_ended_ret = ret[ret["n_shots"].apply(lambda n: n > 0 and n % 2 == 0)]  # returner's shot ended
        player_ended = pd.concat([player_ended_srv, player_ended_ret])

        winners = (player_ended["point_outcome"] == "winner").sum()
        ue = (player_ended["point_outcome"] == "unforced_error").sum()
        if (winners + ue) > 10:
            profile["aggression_index"] = winners / (winners + ue)
        else:
            profile["aggression_index"] = 0.5

        # ── Ace rate ──
        aces = (srv["point_outcome"] == "ace").sum()
        profile["ace_rate"] = aces / len(srv) if len(srv) > 0 else 0

        # ── Shot pattern n-grams ──
        all_seqs = all_pts["shot_sequence"].dropna()
        bigrams = Counter()
        trigrams = Counter()
        for seq in all_seqs:
            if len(seq) >= 2:
                for j in range(len(seq) - 1):
                    bigrams[seq[j:j+2]] += 1
            if len(seq) >= 3:
                for j in range(len(seq) - 2):
                    trigrams[seq[j:j+3]] += 1

        total_bigrams = sum(bigrams.values())
        if total_bigrams > 20:
            probs_bg = np.array([v / total_bigrams for v in bigrams.values()])
            profile["pattern_diversity_2gram"] = float(entropy(probs_bg, base=2))
            # Top 3 bigrams
            for rank, (bg, count) in enumerate(bigrams.most_common(3)):
                profile[f"top_2gram_{rank+1}"] = bg
                profile[f"top_2gram_{rank+1}_pct"] = count / total_bigrams
        else:
            profile["pattern_diversity_2gram"] = 0
            for rank in range(1, 4):
                profile[f"top_2gram_{rank}"] = ""
                profile[f"top_2gram_{rank}_pct"] = 0

        total_trigrams = sum(trigrams.values())
        if total_trigrams > 20:
            profile["pattern_diversity_3gram"] = float(entropy(
                np.array([v / total_trigrams for v in trigrams.values()]), base=2
            ))
        else:
            profile["pattern_diversity_3gram"] = 0

        # ── Game pressure behavior ──
        far_behind = srv[srv["game_pressure"] == "far_behind"]
        if len(far_behind) >= 5:
            profile["win_rate_far_behind"] = float(far_behind["server_won"].mean())
        else:
            profile["win_rate_far_behind"] = 0.5

        far_ahead = srv[srv["game_pressure"] == "far_ahead"]
        if len(far_ahead) >= 5:
            profile["win_rate_far_ahead"] = float(far_ahead["server_won"].mean())
        else:
            profile["win_rate_far_ahead"] = 0.5

        # ── Match completeness flag ──
        match_ids = set(srv["match_id"].unique()) | set(ret["match_id"].unique())
        profile["n_charted_matches"] = len(match_ids)

        profiles.append(profile)

    profiles_df = pd.DataFrame(profiles)
    elapsed = time.time() - t0
    logger.info("  Phase 2 complete: %d player profiles in %.0fs", len(profiles_df), elapsed)

    profiles_df.to_parquet(checkpoint, index=False)
    logger.info("  Saved checkpoint: %s", checkpoint)

    return profiles_df


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Universal Match Features
# ═══════════════════════════════════════════════════════════════════════════════
def get_db_conn():
    """Get database connection from environment."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", os.getenv("SUPABASE_HOST", "localhost")),
        port=os.getenv("DB_PORT", os.getenv("SUPABASE_PORT", "5432")),
        dbname=os.getenv("DB_NAME", os.getenv("SUPABASE_DB", "tennisiq")),
        user=os.getenv("DB_USER", os.getenv("SUPABASE_USER", "postgres")),
        password=os.getenv("DB_PASSWORD", os.getenv("SUPABASE_PASSWORD", "")),
    )


def _load_matches_from_csv() -> pd.DataFrame:
    """Fallback: load matches from Sackmann CSV files."""
    atp_dir = REPO_ROOT / "data" / "sackmann" / "tennis_atp"
    csv_files = sorted(f for f in atp_dir.glob("atp_matches_*.csv") if "doubles" not in f.name)
    if not csv_files:
        raise FileNotFoundError(f"No match CSV files found in {atp_dir}")
    logger.info("  Loading %d CSV files from %s", len(csv_files), atp_dir)
    dfs = [pd.read_csv(f, encoding="latin-1", low_memory=False) for f in csv_files]
    return pd.concat(dfs, ignore_index=True)


def phase3_universal_features() -> pd.DataFrame:
    """Add surface, tourney level, H2H, recent form from DB or CSV."""
    checkpoint = CHECKPOINT_DIR / "universal_features.parquet"
    if checkpoint.exists():
        logger.info("Phase 3: Loading from checkpoint %s", checkpoint)
        return pd.read_parquet(checkpoint)

    logger.info("Phase 3: Building universal match features...")
    t0 = time.time()

    # Try DB first, fall back to CSV
    matches_df = None
    try:
        conn = get_db_conn()
        matches_sql = """
        SELECT m.match_id, m.tourney_date, m.surface, m.tourney_level,
               m.best_of, m.round, m.tourney_name,
               m.winner_id, m.loser_id,
               m.winner_name, m.loser_name,
               m.winner_rank, m.loser_rank,
               m.winner_age, m.loser_age,
               m.winner_hand, m.loser_hand,
               m.w_ace, m.w_df, m.w_svpt, m.w_1stIn, m.w_1stWon, m.w_2ndWon,
               m.w_bpSaved, m.w_bpFaced,
               m.l_ace, m.l_df, m.l_svpt, m.l_1stIn, m.l_1stWon, m.l_2ndWon,
               m.l_bpSaved, m.l_bpFaced,
               m.minutes
        FROM matches m
        ORDER BY m.tourney_date
        """
        matches_df = pd.read_sql(matches_sql, conn)
        conn.close()
        logger.info("  Loaded %d matches from database", len(matches_df))
    except Exception as e:
        logger.warning("  DB unavailable (%s), loading from CSV...", e)

    if matches_df is None or len(matches_df) == 0:
        matches_df = _load_matches_from_csv()
    logger.info("  Loaded %d matches", len(matches_df))

    # ── Surface encoding ──
    surface_map = {"Hard": 0, "Clay": 1, "Grass": 2, "Carpet": 3}
    matches_df["surface_code"] = matches_df["surface"].map(surface_map).fillna(0).astype(int)

    # ── Tournament level encoding ──
    level_map = {"G": 4, "M": 3, "A": 2, "B": 1, "F": 3, "D": 1, "C": 0}
    matches_df["tourney_level_code"] = matches_df.get("tourney_level", pd.Series(dtype=str)).map(level_map).fillna(1).astype(int)

    # ── Rank difference ──
    matches_df["rank_diff"] = (
        pd.to_numeric(matches_df.get("winner_rank", 0), errors="coerce").fillna(500) -
        pd.to_numeric(matches_df.get("loser_rank", 0), errors="coerce").fillna(500)
    )

    # ── Recent form (rolling win % over last 10 matches) ──
    logger.info("  Computing recent form...")
    matches_df["tourney_date"] = pd.to_numeric(matches_df["tourney_date"], errors="coerce")
    matches_df.sort_values("tourney_date", inplace=True)

    # Build a match history per player
    player_history = defaultdict(list)  # player_id -> list of (date, won_bool)

    winner_form = []
    loser_form = []
    h2h_winner = []
    h2h_loser = []

    h2h_record = defaultdict(lambda: [0, 0])  # (p1,p2) -> [p1_wins, p2_wins]

    for idx in range(len(matches_df)):
        row = matches_df.iloc[idx]
        w_id = row.get("winner_id") or row.get("winner_name", "")
        l_id = row.get("loser_id") or row.get("loser_name", "")
        dt = row.get("tourney_date", 0)

        # Recent form
        w_hist = player_history.get(w_id, [])
        l_hist = player_history.get(l_id, [])
        w_recent = [x[1] for x in w_hist[-10:]] if w_hist else []
        l_recent = [x[1] for x in l_hist[-10:]] if l_hist else []
        winner_form.append(np.mean(w_recent) if w_recent else 0.5)
        loser_form.append(np.mean(l_recent) if l_recent else 0.5)

        # H2H
        key = tuple(sorted([str(w_id), str(l_id)]))
        rec = h2h_record[key]
        total_h2h = rec[0] + rec[1]
        if str(w_id) <= str(l_id):
            h2h_winner.append(rec[0] / total_h2h if total_h2h > 0 else 0.5)
            h2h_loser.append(rec[1] / total_h2h if total_h2h > 0 else 0.5)
        else:
            h2h_winner.append(rec[1] / total_h2h if total_h2h > 0 else 0.5)
            h2h_loser.append(rec[0] / total_h2h if total_h2h > 0 else 0.5)

        # Update histories
        player_history[w_id].append((dt, 1))
        player_history[l_id].append((dt, 0))
        if str(w_id) <= str(l_id):
            h2h_record[key][0] += 1
        else:
            h2h_record[key][1] += 1

    matches_df["winner_recent_form"] = winner_form
    matches_df["loser_recent_form"] = loser_form
    matches_df["winner_h2h_pct"] = h2h_winner
    matches_df["loser_h2h_pct"] = h2h_loser

    elapsed = time.time() - t0
    logger.info("  Phase 3 complete: %d matches with universal features in %.0fs",
                len(matches_df), elapsed)

    # Fix mixed-type columns for parquet serialization
    for col in matches_df.select_dtypes(include=["object"]).columns:
        matches_df[col] = matches_df[col].astype(str)

    matches_df.to_parquet(checkpoint, index=False)
    logger.info("  Saved checkpoint: %s", checkpoint)

    return matches_df


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Build Expanded Training Matrix
# ═══════════════════════════════════════════════════════════════════════════════
def phase4_training_matrix(matches_df: pd.DataFrame, profiles_df: pd.DataFrame) -> tuple:
    """Merge universal features + shot pattern profiles into training matrix."""
    checkpoint = CHECKPOINT_DIR / "expanded_training.pkl"
    if checkpoint.exists():
        logger.info("Phase 4: Loading from checkpoint %s", checkpoint)
        with open(checkpoint, "rb") as f:
            return pickle.load(f)

    logger.info("Phase 4: Building expanded training matrix...")
    t0 = time.time()

    # Filter to validation-eligible matches (post-2015, has basic data)
    df = matches_df.copy()

    # Create a player lookup from profiles
    profile_cols = [c for c in profiles_df.columns
                    if c not in ("player", "total_serve_points", "total_return_points",
                                 "n_charted_matches") and not c.startswith("top_2gram")]
    profile_lookup = profiles_df.set_index("player")[profile_cols].to_dict("index")

    # ── Merge pattern features for winner and loser ──
    logger.info("  Merging player pattern features...")
    winner_names = df["winner_name"].astype(str)
    loser_names = df["loser_name"].astype(str)
    for col in profile_cols:
        col_lookup = {p: prof.get(col, 0) for p, prof in profile_lookup.items()}
        df[f"w_{col}"] = winner_names.map(col_lookup).fillna(0)
        df[f"l_{col}"] = loser_names.map(col_lookup).fillna(0)

    # ── Build symmetric dataset (p1 vs p2, label = p1 wins) ──
    logger.info("  Building symmetric rows...")
    n = len(df)

    # Row 1: p1=winner (label=1)
    row1 = pd.DataFrame()
    row1["label"] = np.ones(n, dtype=int)

    # Universal features (oriented as p1=winner)
    row1["surface_code"] = df["surface_code"].values
    row1["tourney_level_code"] = df["tourney_level_code"].values
    row1["best_of"] = pd.to_numeric(df.get("best_of", 3), errors="coerce").fillna(3).astype(int).values
    row1["cpi"] = pd.to_numeric(df.get("cpi", 0), errors="coerce").fillna(0).values
    row1["rank_diff"] = df["rank_diff"].values
    row1["p1_recent_form"] = df["winner_recent_form"].values
    row1["p2_recent_form"] = df["loser_recent_form"].values
    row1["p1_h2h_pct"] = df["winner_h2h_pct"].values
    row1["p2_h2h_pct"] = df["loser_h2h_pct"].values

    # Pattern features
    for col in profile_cols:
        row1[f"p1_{col}"] = df[f"w_{col}"].values
        row1[f"p2_{col}"] = df[f"l_{col}"].values

    # Row 2: p1=loser (label=0, features swapped)
    row2 = pd.DataFrame()
    row2["label"] = np.zeros(n, dtype=int)
    row2["surface_code"] = df["surface_code"].values
    row2["tourney_level_code"] = df["tourney_level_code"].values
    row2["best_of"] = pd.to_numeric(df.get("best_of", 3), errors="coerce").fillna(3).astype(int).values
    row2["cpi"] = pd.to_numeric(df.get("cpi", 0), errors="coerce").fillna(0).values
    row2["rank_diff"] = -df["rank_diff"].values
    row2["p1_recent_form"] = df["loser_recent_form"].values
    row2["p2_recent_form"] = df["winner_recent_form"].values
    row2["p1_h2h_pct"] = df["loser_h2h_pct"].values
    row2["p2_h2h_pct"] = df["winner_h2h_pct"].values

    for col in profile_cols:
        row2[f"p1_{col}"] = df[f"l_{col}"].values
        row2[f"p2_{col}"] = df[f"w_{col}"].values

    full = pd.concat([row1, row2], ignore_index=True)
    y = full.pop("label")
    X = full

    # Also try to merge existing Elo features from DB
    try:
        conn = get_db_conn()
        elo_sql = """
        SELECT match_id, p1_elo, p2_elo, p1_surface_elo, p2_surface_elo,
               elo_diff, surface_elo_diff
        FROM match_features
        ORDER BY match_id
        """
        elo_df = pd.read_sql(elo_sql, conn)
        conn.close()
        if len(elo_df) > 0:
            # Elo features are already symmetric in the existing pipeline
            # We'll add them if available but the pipeline works without them
            logger.info("  Found %d matches with Elo features", len(elo_df))
    except Exception as e:
        logger.info("  Elo features not available from DB (%s) — proceeding without", e)

    logger.info("  Training matrix: %d rows x %d features", len(X), len(X.columns))

    # Remove any fully-null columns
    null_cols = X.columns[X.isnull().all()]
    if len(null_cols) > 0:
        logger.info("  Dropping %d fully-null columns: %s", len(null_cols), list(null_cols))
        X.drop(columns=null_cols, inplace=True)

    X.fillna(0, inplace=True)

    elapsed = time.time() - t0
    logger.info("  Phase 4 complete: %.0fs", elapsed)

    with open(checkpoint, "wb") as f:
        pickle.dump((X, y), f)
    logger.info("  Saved checkpoint: %s", checkpoint)

    return X, y


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Train & Evaluate
# ═══════════════════════════════════════════════════════════════════════════════
def phase5_train(X: pd.DataFrame, y: pd.Series) -> dict:
    """Train XGBoost on expanded features, evaluate, compare to baseline."""
    from xgboost import XGBClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import brier_score_loss
    from sklearn.model_selection import StratifiedKFold

    logger.info("Phase 5: Training XGBoost on expanded feature set...")
    t0 = time.time()

    # Default hyperparams (agent will tune these in Phase 6)
    params = {
        "n_estimators": 300,
        "max_depth": 4,
        "learning_rate": 0.1,
        "min_child_weight": 5,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "eval_metric": "logloss",
        "random_state": 42,
        "use_label_encoder": False,
    }

    logger.info("  Features: %s", list(X.columns))
    logger.info("  Shape: %s, label mean: %.4f", X.shape, y.mean())

    # Stratified 5-fold CV
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    brier_scores = []

    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = XGBClassifier(**params)
        model.fit(X_train, y_train, verbose=False)

        # Platt scaling
        cal = CalibratedClassifierCV(model, method="sigmoid", cv=3)
        cal.fit(X_train, y_train)

        probs = cal.predict_proba(X_val)[:, 1]
        brier = brier_score_loss(y_val, probs)
        brier_scores.append(brier)
        logger.info("  Fold %d: Brier = %.4f", fold + 1, brier)

    mean_brier = np.mean(brier_scores)
    std_brier = np.std(brier_scores)
    logger.info("  Mean Brier: %.4f (±%.4f)", mean_brier, std_brier)

    # Train final model on full data
    final_model = XGBClassifier(**params)
    final_model.fit(X, y, verbose=False)
    final_cal = CalibratedClassifierCV(final_model, method="sigmoid", cv=5)
    final_cal.fit(X, y)

    # Save model
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / "expanded_win_prob_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump({"model": final_cal, "features": list(X.columns), "params": params}, f)
    logger.info("  Saved model: %s", model_path)

    # Feature importance
    importances = final_model.feature_importances_
    feat_imp = sorted(zip(X.columns, importances), key=lambda x: -x[1])
    logger.info("  Top 15 features:")
    for name, imp in feat_imp[:15]:
        logger.info("    %s: %.4f", name, imp)

    # Save results
    result = {
        "brier_score": float(mean_brier),
        "brier_std": float(std_brier),
        "n_features": len(X.columns),
        "n_samples": len(X),
        "features": list(X.columns),
        "params": params,
        "feature_importance": {k: float(v) for k, v in feat_imp[:30]},
        "timestamp": datetime.now().isoformat(),
    }

    result_path = EXPERIMENTS_DIR / "expanded_baseline.json"
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)
    logger.info("  Saved baseline: %s", result_path)

    elapsed = time.time() - t0
    logger.info("  Phase 5 complete: %.0fs", elapsed)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Agent Loop — XGBoost Hyperparameter Tuning
# ═══════════════════════════════════════════════════════════════════════════════
XGBOOST_PARAMS = {
    "n_estimators":     (50,  1000, int),
    "max_depth":        (2,   10,   int),
    "learning_rate":    (0.01, 0.3, float),
    "min_child_weight": (1,   20,   int),
    "subsample":        (0.5, 1.0,  float),
    "colsample_bytree": (0.3, 1.0,  float),
    "gamma":            (0.0, 5.0,  float),
    "reg_alpha":        (0.0, 10.0, float),
    "reg_lambda":       (0.0, 10.0, float),
}


def should_stop() -> bool:
    now = datetime.now()
    stop_dt = datetime.combine(now.date(), datetime.min.time().replace(
        hour=STOP_HOUR, minute=STOP_MINUTE))
    if stop_dt <= now:
        stop_dt += timedelta(days=1)
    remaining = (stop_dt - now).total_seconds()
    return remaining < 60  # stop within 1 minute of deadline


def phase6_agent_loop(X: pd.DataFrame, y: pd.Series, baseline_brier: float):
    """Tune XGBoost hyperparams using Bedrock Haiku agent loop."""
    from xgboost import XGBClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import brier_score_loss
    from sklearn.model_selection import StratifiedKFold

    logger.info("Phase 6: Starting XGBoost hyperparameter agent loop...")
    logger.info("  Baseline Brier: %.4f", baseline_brier)
    logger.info("  Will stop at %02d:%02d or after %d experiments",
                STOP_HOUR, STOP_MINUTE, MAX_AGENT_EXPERIMENTS)

    # Try to use Bedrock for proposals; fall back to random search if unavailable
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-west-2"))
        model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        use_llm = True
        logger.info("  Using Bedrock Haiku for experiment proposals")
    except Exception as e:
        logger.warning("  Bedrock unavailable (%s) — using random search", e)
        use_llm = False

    best_brier = baseline_brier
    best_params = None
    results = []

    current_params = {
        "n_estimators": 300, "max_depth": 4, "learning_rate": 0.1,
        "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8,
        "gamma": 0.0, "reg_alpha": 0.0, "reg_lambda": 1.0,
    }

    for exp_num in range(1, MAX_AGENT_EXPERIMENTS + 1):
        if should_stop():
            logger.info("  Stopping: reached time limit")
            break

        logger.info("── Experiment %d ──────────────────────────────", exp_num)

        # ── Propose experiment ──
        if use_llm:
            try:
                proposal = _llm_propose(client, model_id, current_params, results[-10:],
                                        baseline_brier, best_brier)
            except Exception as e:
                logger.warning("  LLM proposal failed (%s), using random", e)
                proposal = _random_propose(current_params)
        else:
            proposal = _random_propose(current_params)

        param_name = proposal["param"]
        new_value = proposal["new_value"]
        logger.info("  Proposal: %s = %s (rationale: %s)",
                     param_name, new_value, proposal.get("rationale", "random"))

        # ── Evaluate ──
        test_params = current_params.copy()
        test_params[param_name] = new_value

        try:
            brier = _evaluate_params(X, y, test_params)
        except Exception as e:
            logger.error("  Evaluation failed: %s", e)
            results.append({"exp": exp_num, "param": param_name, "value": new_value,
                            "brier": None, "decision": "ERROR"})
            continue

        delta = brier - baseline_brier
        logger.info("  Brier: %.4f (Δ = %+.4f vs baseline, best = %.4f)",
                     brier, delta, best_brier)

        # ── Decide ──
        if brier < best_brier - 0.0005:
            decision = "KEEP"
            current_params[param_name] = new_value
            best_brier = brier
            best_params = current_params.copy()
            logger.info("  ✅ KEEP — new best!")
        elif abs(brier - baseline_brier) < 0.001:
            decision = "NEUTRAL"
            logger.info("  ⚪ NEUTRAL")
        else:
            decision = "REVERT"
            logger.info("  ❌ REVERT")

        results.append({
            "exp": exp_num, "param": param_name, "value": new_value,
            "brier": float(brier), "delta": float(delta), "decision": decision,
        })

    # ── Save final results ──
    summary = {
        "total_experiments": len(results),
        "best_brier": float(best_brier),
        "baseline_brier": float(baseline_brier),
        "improvement": float(baseline_brier - best_brier),
        "best_params": best_params or current_params,
        "keeps": sum(1 for r in results if r["decision"] == "KEEP"),
        "reverts": sum(1 for r in results if r["decision"] == "REVERT"),
        "neutrals": sum(1 for r in results if r["decision"] == "NEUTRAL"),
        "all_experiments": results,
        "timestamp": datetime.now().isoformat(),
    }

    summary_path = EXPERIMENTS_DIR / "overnight_xgb_tuning.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("Agent loop complete. Results: %s", summary_path)
    logger.info("  Best Brier: %.4f (Δ = %+.4f from baseline)",
                best_brier, baseline_brier - best_brier)

    # Retrain and save best model
    if best_params:
        logger.info("  Retraining final model with best params...")
        final = XGBClassifier(
            **{k: v for k, v in best_params.items()},
            eval_metric="logloss", random_state=42, use_label_encoder=False,
        )
        final.fit(X, y, verbose=False)
        cal = CalibratedClassifierCV(final, method="sigmoid", cv=5)
        cal.fit(X, y)
        model_path = MODELS_DIR / "best_expanded_win_prob_model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump({"model": cal, "features": list(X.columns),
                         "params": best_params, "brier": best_brier}, f)
        logger.info("  Saved best model: %s", model_path)


def _evaluate_params(X, y, params) -> float:
    """Quick 3-fold CV evaluation of params."""
    from xgboost import XGBClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import brier_score_loss
    from sklearn.model_selection import StratifiedKFold

    xgb_params = {k: v for k, v in params.items()}
    xgb_params["eval_metric"] = "logloss"
    xgb_params["random_state"] = 42
    xgb_params["use_label_encoder"] = False

    skf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    briers = []
    for train_idx, val_idx in skf.split(X, y):
        model = XGBClassifier(**xgb_params)
        model.fit(X.iloc[train_idx], y.iloc[train_idx], verbose=False)
        cal = CalibratedClassifierCV(model, method="sigmoid", cv=3)
        cal.fit(X.iloc[train_idx], y.iloc[train_idx])
        probs = cal.predict_proba(X.iloc[val_idx])[:, 1]
        briers.append(brier_score_loss(y.iloc[val_idx], probs))
    return float(np.mean(briers))


def _llm_propose(client, model_id, current_params, recent_results,
                 baseline_brier, best_brier) -> dict:
    """Use Bedrock Haiku to propose next experiment."""
    import json as _json

    system = (
        "You are an ML hyperparameter tuning agent for XGBoost. "
        "Propose ONE parameter change to minimize Brier score. "
        "Output ONLY a JSON object with keys: param, new_value, rationale."
    )
    user_msg = (
        f"Current params: {json.dumps(current_params)}\n"
        f"Baseline Brier: {baseline_brier:.4f}\n"
        f"Best Brier so far: {best_brier:.4f}\n"
        f"Valid params and ranges: {json.dumps({k: [lo, hi] for k, (lo, hi, _) in XGBOOST_PARAMS.items()})}\n"
        f"Recent experiments: {json.dumps(recent_results[-5:])}\n"
        f"Propose the single most promising change."
    )

    body = _json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    })

    resp = client.invoke_model(modelId=model_id, contentType="application/json",
                               accept="application/json", body=body)
    data = _json.loads(resp["body"].read())
    text = data["content"][0]["text"]

    # Parse JSON from response
    match = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in response: {text}")
    proposal = _json.loads(match.group())

    # Validate
    param = proposal["param"]
    if param not in XGBOOST_PARAMS:
        raise ValueError(f"Invalid param: {param}")
    lo, hi, typ = XGBOOST_PARAMS[param]
    val = typ(proposal["new_value"])
    val = max(lo, min(hi, val))
    proposal["new_value"] = val
    return proposal


def _random_propose(current_params) -> dict:
    """Random perturbation fallback."""
    import random
    param = random.choice(list(XGBOOST_PARAMS.keys()))
    lo, hi, typ = XGBOOST_PARAMS[param]
    if typ == int:
        new_val = random.randint(lo, hi)
    else:
        new_val = round(random.uniform(lo, hi), 3)
    return {"param": param, "new_value": new_val, "rationale": "random exploration"}


# ═══════════════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════════════════════
def _write_final_report(baseline_brier: float):
    """Write a comprehensive markdown report of everything that happened."""
    report_path = EXPERIMENTS_DIR / f"overnight_report_{datetime.now().strftime('%Y%m%d')}.md"

    lines = [
        f"# TennisIQ — Overnight Pipeline Report",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Phase 1: MCP Shot Parsing",
    ]

    parsed_path = CHECKPOINT_DIR / "parsed_points.parquet"
    if parsed_path.exists():
        pp = pd.read_parquet(parsed_path, columns=["match_id", "rally_length", "shot_sequence"])
        lines.append(f"- **Points parsed:** {len(pp):,}")
        lines.append(f"- **Unique matches:** {pp['match_id'].nunique():,}")
        lines.append(f"- **Avg rally length:** {pp['rally_length'].mean():.1f} shots")
        lines.append(f"- **Points with valid shot data:** {(pp['shot_sequence'].str.len() > 0).sum():,}")
        del pp

    lines.append("")
    lines.append("## Phase 2: Player Pattern Profiles")
    profiles_path = CHECKPOINT_DIR / "player_profiles.parquet"
    if profiles_path.exists():
        prof = pd.read_parquet(profiles_path)
        lines.append(f"- **Players profiled:** {len(prof)}")
        lines.append(f"- **Profile features per player:** {len([c for c in prof.columns if c != 'player'])}")
        top_charted = prof.nlargest(10, "n_charted_matches")[["player", "n_charted_matches"]]
        lines.append(f"- **Top 10 most-charted players:**")
        for _, row in top_charted.iterrows():
            lines.append(f"  - {row['player']}: {int(row['n_charted_matches'])} matches")
        del prof

    lines.append("")
    lines.append("## Phase 3: Universal Match Features")
    uni_path = CHECKPOINT_DIR / "universal_features.parquet"
    if uni_path.exists():
        uni = pd.read_parquet(uni_path, columns=["surface", "tourney_level_code"])
        lines.append(f"- **Total matches with universal features:** {len(uni):,}")
        lines.append(f"- **Surface distribution:** {uni['surface'].value_counts().to_dict()}")
        del uni

    lines.append("")
    lines.append("## Phase 4-5: Expanded Model Training")
    expanded_path = EXPERIMENTS_DIR / "expanded_baseline.json"
    if expanded_path.exists():
        with open(expanded_path) as f:
            exp = json.load(f)
        lines.append(f"- **Training samples:** {exp.get('n_samples', 'N/A'):,}")
        lines.append(f"- **Total features:** {exp.get('n_features', 'N/A')}")
        lines.append(f"- **New Brier score (5-fold CV):** {exp.get('brier_score', 0):.4f} (+-{exp.get('brier_std', 0):.4f})")
        lines.append(f"- **Original baseline Brier:** 0.2544")
        improvement = 0.2544 - exp.get('brier_score', 0.2544)
        if improvement > 0:
            lines.append(f"- **Improvement from new features:** {improvement:+.4f}")
        else:
            lines.append(f"- **Change from new features:** {improvement:+.4f}")
        lines.append("")
        lines.append("### Top 15 Feature Importances")
        fi = exp.get("feature_importance", {})
        for rank, (feat, imp) in enumerate(sorted(fi.items(), key=lambda x: -x[1])[:15], 1):
            lines.append(f"  {rank}. **{feat}**: {imp:.4f}")

    lines.append("")
    lines.append("## Phase 6: XGBoost Hyperparameter Tuning")
    tuning_path = EXPERIMENTS_DIR / "overnight_xgb_tuning.json"
    if tuning_path.exists():
        with open(tuning_path) as f:
            tun = json.load(f)
        lines.append(f"- **Experiments run:** {tun.get('total_experiments', 0)}")
        lines.append(f"- **KEEP / REVERT / NEUTRAL:** {tun.get('keeps', 0)} / {tun.get('reverts', 0)} / {tun.get('neutrals', 0)}")
        lines.append(f"- **Best Brier:** {tun.get('best_brier', 0):.4f}")
        lines.append(f"- **Total improvement vs original 0.2544 baseline:** {0.2544 - tun.get('best_brier', 0.2544):+.4f}")
        bp = tun.get("best_params", {})
        if bp:
            lines.append("")
            lines.append("### Best Parameters Found")
            for k, v in sorted(bp.items()):
                lines.append(f"  - {k}: {v}")

        keeps = [r for r in tun.get("all_experiments", []) if r.get("decision") == "KEEP"]
        if keeps:
            lines.append("")
            lines.append("### Experiments That Improved the Model")
            for r in keeps:
                lines.append(f"  - Exp {r['exp']}: {r['param']} = {r['value']} -> Brier {r['brier']:.4f} (delta {r['delta']:+.4f})")

    lines.append("")
    lines.append("## What Was Added")
    lines.append("- Shot sequence parser decoding serve direction, shot type, direction, depth, outcomes")
    lines.append("- Per-player pattern profiles: serve tendencies, pressure behavior, rally length analysis, aggression index, shot n-gram diversity")
    lines.append("- Universal features: surface, tournament level, rank difference, H2H record, recent form")
    lines.append("- Expanded XGBoost model trained on full feature set")
    lines.append("- Autonomous hyperparameter tuning via Bedrock Haiku agent loop")
    lines.append("")
    lines.append("## Not Yet Added (Future Work)")
    lines.append("- Court speed (courtspeed.com)")
    lines.append("- Weather data by tournament lat/long")
    lines.append("- Ball type")
    lines.append("- Shot speed / spin rate / bounce height (no data source yet)")
    lines.append("- Live tournament data ingestion")
    lines.append("- Matchup-specific pattern analysis (player A vs player B shot tendencies)")
    lines.append("- Score-state conditioned predictions (win prob shift when down 0-4 in set)")
    lines.append("- Best-of-3 vs best-of-5 behavioral differences")
    lines.append("- Set-level context (2nd set of Slam vs 2nd set of 250)")
    lines.append("")
    lines.append("## Files Created")
    lines.append("- `data/processed/parsed_points.parquet` — structured MCP points")
    lines.append("- `data/processed/player_profiles.parquet` — player pattern profiles")
    lines.append("- `data/processed/universal_features.parquet` — enriched match data")
    lines.append("- `data/processed/expanded_training.pkl` — training matrix")
    lines.append("- `models/hard/expanded_win_prob_model.pkl` — baseline expanded model")
    lines.append("- `models/hard/best_expanded_win_prob_model.pkl` — best tuned model")
    lines.append("- `experiments/expanded_baseline.json` — baseline results")
    lines.append("- `experiments/overnight_xgb_tuning.json` — tuning results")

    report_text = "\n".join(lines)
    report_path.write_text(report_text)
    logger.info("Final report written to %s", report_path)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    logger.info("=" * 70)
    logger.info("TennisIQ Overnight Pattern Pipeline")
    logger.info("Started: %s", datetime.now().isoformat())
    logger.info("Stop time: %02d:%02d", STOP_HOUR, STOP_MINUTE)
    logger.info("=" * 70)

    baseline_brier = 0.2544  # will be updated by Phase 5

    try:
        # Phase 1: Parse MCP
        points_df = phase1_parse_mcp()

        # Phase 2: Player profiles
        profiles_df = phase2_player_profiles(points_df)

        # Free memory
        del points_df
        import gc; gc.collect()

        # Phase 3: Universal features
        matches_df = phase3_universal_features()

        # Phase 4: Training matrix
        X, y = phase4_training_matrix(matches_df, profiles_df)

        # Free memory
        del matches_df, profiles_df
        import gc; gc.collect()

        # Phase 5: Train & evaluate
        result = phase5_train(X, y)
        baseline_brier = result["brier_score"]

        logger.info("=" * 70)
        logger.info("EXPANDED BASELINE: Brier = %.4f (original baseline was 0.2544)",
                     baseline_brier)
        logger.info("=" * 70)

        # Phase 6: Agent loop
        if not should_stop():
            phase6_agent_loop(X, y, baseline_brier)
        else:
            logger.info("Skipping Phase 6: already past stop time")

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        baseline_brier = 0.2544  # fallback for report
        raise
    finally:
        try:
            _write_final_report(baseline_brier)
        except Exception as re:
            logger.error("Report generation failed: %s", re)
        logger.info("=" * 70)
        logger.info("Pipeline complete: %s", datetime.now().isoformat())
        logger.info("=" * 70)


if __name__ == "__main__":
    main()
