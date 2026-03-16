"""
build_edge_features_v2.py — TennisIQ Feature Engineering (V2)
==============================================================
Rebuilds training_edge_v2.pkl with:
  - Glicko-2 ratings replacing flat-K Elo
  - Rally-adjusted fatigue (grinding_index, ACWR, consecutive days)
  - Player attribute accumulators (8 FIFA card dimensions)
  - All original 109 features preserved (backward compatible)
  - ~20 new Glicko-2 features + ~20 new fatigue features = ~130+ total

This script processes ALL 941K matches chronologically in a single pass.
For each match:
  1. Snapshot all accumulators BEFORE the match (zero leakage)
  2. Build feature rows (winner-as-p1 and loser-as-p1)
  3. Update all accumulators AFTER the match

Output: data/processed/training_edge_v2.pkl
  Tuple: (X DataFrame, y Series, dates Series)

Usage:
  cd ~/Documents/tennis-analytics
  source venv/bin/activate
  python3 scripts/build_edge_features_v2.py

Expected runtime: 15-25 minutes for 941K matches on M3 Pro.
"""

import os
import sys
import time
import pickle
import math
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, Optional, Tuple, List

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.glicko2 import Glicko2RatingSystem, build_glicko_features
from modules.fatigue import FatigueAccumulator, build_fatigue_features
from modules.player_attributes import (
    PlayerAttributeAccumulator,
    update_attributes_from_match,
    AttributeRanker,
)
from modules.charted_features import (
    ChartedAccumulator,
    aggregate_charted_points,
    get_charted_for_match,
    build_charted_features,
)
from modules.weather_v2 import (
    get_is_indoor,
    get_weather_features_v2,
    build_weather_interaction_features,
    compute_actual_match_date,
)


# ============================================================================
# Configuration
# ============================================================================

DATA_DIR = PROJECT_ROOT / "data"
SACKMANN_DIR = DATA_DIR / "sackmann" / "tennis_atp" / "atp_matches_*.csv"
CPI_FILE = DATA_DIR / "court_speed.csv"
PARSED_POINTS = DATA_DIR / "processed" / "parsed_points.parquet"
WEATHER_CACHE = DATA_DIR / "processed" / "weather_cache.parquet"
OUTPUT_FILE = DATA_DIR / "processed" / "training_edge_v4.pkl"
ATTRIB_FILE = DATA_DIR / "processed" / "player_attributes_v2.pkl"

TEMPORAL_CUTOFF = pd.Timestamp("2023-01-01")

SURFACE_MAP = {"Hard": "hard", "Clay": "clay", "Grass": "grass", "Carpet": "hard"}
TOURNEY_LEVEL_MAP = {"G": 4, "M": 3, "A": 2, "D": 2, "C": 1, "S": 1, "F": 0}
SURFACE_CODE_MAP = {"hard": 0, "clay": 1, "grass": 2}


# ============================================================================
# Data loading
# ============================================================================

def load_sackmann_matches() -> pd.DataFrame:
    """Load and clean all Sackmann ATP match CSVs."""
    import glob

    csv_pattern = str(DATA_DIR / "sackmann" / "tennis_atp" / "atp_matches_*.csv")
    files = sorted(glob.glob(csv_pattern))
    if not files:
        raise FileNotFoundError(f"No match CSVs found at {csv_pattern}")

    print(f"Loading {len(files)} Sackmann CSVs...")
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, low_memory=False)
            dfs.append(df)
        except Exception as e:
            print(f"  Warning: skipping {f}: {e}")

    df = pd.concat(dfs, ignore_index=True)
    df.columns = df.columns.str.replace(" ", "_")

    # Parse dates
    df["tourney_date"] = pd.to_datetime(df["tourney_date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["tourney_date"])

    # Clean surface
    df["surface_clean"] = df["surface"].map(SURFACE_MAP).fillna("hard")

    # Sort chronologically (this is critical for the rolling pass)
    df = df.sort_values("tourney_date").reset_index(drop=True)

    # Ensure numeric types for stats columns
    stat_cols = [
        "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
        "w_SvGms", "w_bpSaved", "w_bpFaced",
        "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon",
        "l_SvGms", "l_bpSaved", "l_bpFaced",
        "minutes", "winner_rank", "loser_rank", "best_of",
    ]
    for col in stat_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    print(f"Loaded {len(df):,} matches, {df['tourney_date'].min().date()} to {df['tourney_date'].max().date()}")
    return df


def load_cpi() -> Dict[str, float]:
    """Load Court Pace Index data."""
    cpi = {}
    if CPI_FILE.exists():
        df = pd.read_csv(CPI_FILE)
        df.columns = df.columns.str.replace(" ", "_")
        for _, row in df.iterrows():
            key = str(row.get("tournament", "")).strip().lower()
            val = row.get("cpi", row.get("CPI", None))
            if key and val and not pd.isna(val):
                cpi[key] = float(val)
    print(f"Loaded {len(cpi)} CPI measurements")
    return cpi


# Tournament name → (lat_round, lon_round) for weather lookup
_TOURNEY_COORDS = {
    "australian open":    (-37.82, 144.98),
    "roland garros":      (48.85,   2.25),
    "wimbledon":          (51.43,  -0.21),
    "us open":            (40.73,  -73.85),
    "indian wells":       (33.74, -116.31),
    "miami":              (25.71,  -80.24),
    "monte-carlo":        (43.74,   7.42),
    "monte carlo":        (43.74,   7.42),
    "madrid":             (40.45,  -3.69),
    "rome":               (41.93,  12.45),
    "internazionali":     (41.93,  12.45),
    "italian open":       (41.93,  12.45),
    "canadian open":      (43.73,  -79.38),
    "rogers cup":         (43.73,  -79.38),
    "toronto":            (43.73,  -79.38),
    "montreal":           (45.50,  -73.57),
    "cincinnati":         (39.10,  -84.51),
    "western & southern": (39.10,  -84.51),
    "shanghai":           (31.18,  121.47),
    "paris":              (48.84,   2.38),
    "atp finals":         (45.07,   7.69),
    "nitto atp":          (45.07,   7.69),
    "barclays":           (51.54,  -0.08),
    "barcelona":          (41.36,   2.15),
    "munich":             (48.22,  11.58),
    "halle":              (51.96,   8.55),
    "queens":             (51.49,  -0.21),
    "eastbourne":         (50.77,   0.28),
    "mallorca":           (39.70,   3.02),
    "stuttgart":          (48.78,   9.18),
    "vienna":             (48.21,  16.37),
    "swiss indoors":      (47.56,   7.59),
    "basel":              (47.56,   7.59),
    "stockholm":          (59.33,  18.07),
    "antwerp":            (51.22,   4.40),
    "washington":         (38.90,  -77.04),
    "citi open":          (38.90,  -77.04),
    "hamburg":            (53.58,  10.02),
    "acapulco":           (16.85,  -99.82),
    "rio":                (-22.91, -43.17),
    "buenos aires":       (-34.60, -58.38),
    "santiago":           (-33.46, -70.65),
    "houston":            (29.76,  -95.37),
    "istanbul":           (41.01,  28.98),
    "estoril":            (38.74,  -9.30),
    "marrakech":          (31.63,  -7.98),
    "dubai":              (25.20,  55.27),
    "doha":               (25.29,  51.53),
    "qatar":              (25.29,  51.53),
    "auckland":           (-36.85, 174.76),
    "sydney":             (-33.87, 151.21),
    "brisbane":           (-27.47, 153.03),
    "hobart":             (-42.88, 147.33),
    "adelaide":           (-34.93, 138.60),
    "beijing":            (39.90,  116.41),
    "shenzhen":           (22.54,  114.06),
    "chengdu":            (30.57,  104.07),
    "moselle":            (49.12,   6.18),
    "metz":               (49.12,   6.18),
    "lyon":               (45.76,   4.84),
    "rotterdam":          (51.92,   4.48),
    "marseille":          (43.30,   5.37),
    "montpellier":        (43.61,   3.88),
    "dallas":             (32.78,  -96.80),
    "delray beach":       (26.46,  -80.07),
    "winston-salem":      (36.10,  -80.24),
    "memphis":            (35.15,  -90.05),
    "atlanta":            (33.75,  -84.39),
}


def _get_tourney_coords(tourney_name: str) -> Optional[Tuple[float, float]]:
    """Map tournament name → (lat_r, lon_r) for weather lookup."""
    name_lower = str(tourney_name).lower().strip()
    for key, coords in _TOURNEY_COORDS.items():
        if key in name_lower:
            return coords
    # Partial word match
    for key, coords in _TOURNEY_COORDS.items():
        for word in key.split():
            if len(word) >= 4 and word in name_lower:
                return coords
    return None


def load_weather_cache() -> Optional[Dict]:
    """
    Load weather_cache.parquet into a lookup dict.
    Key: (date_str YYYYMMDD, lat_r, lon_r) → {temp_max, precip, wind_max, humidity}
    """
    if not WEATHER_CACHE.exists():
        print(f"Weather cache not found at {WEATHER_CACHE} — skipping weather features")
        return None

    df = pd.read_parquet(WEATHER_CACHE)
    lookup = {}
    for _, row in df.iterrows():
        key = (str(row["date_str"]), round(row["lat_r"], 2), round(row["lon_r"], 2))
        lookup[key] = {
            "temp_max": row.get("temp_max"),
            "precip": row.get("precip"),
            "wind_max": row.get("wind_max"),
            "humidity": row.get("humidity"),
        }

    print(f"Loaded weather cache: {len(lookup):,} (date, location) entries")
    return lookup


def get_weather_features(weather_lookup: Optional[Dict],
                         tourney_name: str,
                         match_date) -> dict:
    """
    Retrieve weather features for a match. Returns default zeros if no data.
    """
    defaults = {
        "temp_max": 20.0,     # Temperate default
        "precip_mm": 0.0,
        "wind_kmh": 10.0,
        "humidity_pct": 60.0,
        "is_hot": 0,
        "is_windy": 0,
    }

    if weather_lookup is None:
        return defaults

    coords = _get_tourney_coords(tourney_name)
    if coords is None:
        return defaults

    if hasattr(match_date, "strftime"):
        date_str = match_date.strftime("%Y%m%d")
    else:
        date_str = str(match_date).replace("-", "")[:8]

    lat_r, lon_r = coords
    key = (date_str, round(lat_r, 2), round(lon_r, 2))
    w = weather_lookup.get(key)

    if w is None:
        return defaults

    temp = float(w["temp_max"]) if w["temp_max"] is not None else 20.0
    precip = float(w["precip"]) if w["precip"] is not None else 0.0
    wind = float(w["wind_max"]) if w["wind_max"] is not None else 10.0
    humid = float(w["humidity"]) if w["humidity"] is not None else 60.0

    return {
        "temp_max": temp,
        "precip_mm": precip,
        "wind_kmh": wind,
        "humidity_pct": humid,
        "is_hot": int(temp > 28.0),
        "is_windy": int(wind > 25.0),
    }


# ============================================================================
# Legacy rolling accumulators (preserved from v1 for backward compatibility)
# ============================================================================

class LegacyAccumulator:
    """
    Tracks the original v1 rolling stats that aren't covered by
    Glicko-2 or the fatigue module. Keeps feature backward compatibility.
    """

    def __init__(self):
        # Form: recent win rates
        self.form_results: Dict[str, List[int]] = defaultdict(list)  # 1=win, 0=loss

        # H2H
        self.h2h_wins: Dict[Tuple[str, str], int] = defaultdict(int)
        self.h2h_matches: Dict[Tuple[str, str], int] = defaultdict(int)

        # Serve stats (rolling means)
        self.serve_stats: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # Surface form
        self.surface_form: Dict[Tuple[str, str], List[int]] = defaultdict(list)

        # Top 50 record
        self.top50_wins: Dict[str, int] = defaultdict(int)
        self.top50_matches: Dict[str, int] = defaultdict(int)

    def snapshot(self, player: str, opponent: str, surface: str) -> dict:
        """Snapshot legacy features BEFORE match."""
        feats = {}

        # Form
        results = self.form_results[player]
        for window in [3, 5, 15, 50]:
            recent = results[-window:] if results else []
            feats[f"form_{window}"] = np.mean(recent) if recent else 0.5

        # Surface form
        surf_results = self.surface_form[(player, surface)]
        feats["surface_form"] = np.mean(surf_results[-15:]) if surf_results else 0.5

        # H2H
        h2h_key = tuple(sorted([player, opponent]))
        h2h_total = self.h2h_matches[h2h_key]
        if h2h_total > 0:
            my_wins = self.h2h_wins[(player, opponent)]
            feats["h2h_pct"] = my_wins / h2h_total
        else:
            feats["h2h_pct"] = 0.5

        # Serve rolling stats
        serve = self.serve_stats[player]
        for stat in [
            "ace_rate", "df_rate", "first_serve_pct", "first_serve_won_pct",
            "second_serve_won_pct", "bp_save_pct",
        ]:
            vals = serve[stat][-50:]  # Rolling 50 matches
            feats[stat] = np.mean(vals) if vals else 0.5

        # Return stats (opponent's serve stats inverted)
        # These get computed per-match, not rolling

        # Top 50 record
        total = self.top50_matches[player]
        feats["win_rate_vs_top50"] = (
            self.top50_wins[player] / total if total > 0 else 0.5
        )

        return feats

    def update(self, winner: str, loser: str, surface: str, match: dict):
        """Update accumulators AFTER match."""
        # Form
        self.form_results[winner].append(1)
        self.form_results[loser].append(0)

        # Surface form
        self.surface_form[(winner, surface)].append(1)
        self.surface_form[(loser, surface)].append(0)

        # H2H
        h2h_key = tuple(sorted([winner, loser]))
        self.h2h_matches[h2h_key] += 1
        self.h2h_wins[(winner, loser)] += 1

        # Serve stats
        for prefix, player in [("w_", winner), ("l_", loser)]:
            svpt = match.get(f"{prefix}svpt", 0)
            if not svpt or pd.isna(svpt) or svpt <= 0:
                continue
            svpt = float(svpt)

            aces = float(match.get(f"{prefix}ace", 0) or 0)
            dfs = float(match.get(f"{prefix}df", 0) or 0)
            first_in = float(match.get(f"{prefix}1stIn", 0) or 0)
            first_won = float(match.get(f"{prefix}1stWon", 0) or 0)
            second_won = float(match.get(f"{prefix}2ndWon", 0) or 0)
            bp_faced = float(match.get(f"{prefix}bpFaced", 0) or 0)
            bp_saved = float(match.get(f"{prefix}bpSaved", 0) or 0)

            self.serve_stats[player]["ace_rate"].append(aces / svpt)
            self.serve_stats[player]["df_rate"].append(dfs / svpt)
            if svpt > 0:
                self.serve_stats[player]["first_serve_pct"].append(first_in / svpt)
            if first_in > 0:
                self.serve_stats[player]["first_serve_won_pct"].append(first_won / first_in)
            second_played = svpt - first_in
            if second_played > 0:
                self.serve_stats[player]["second_serve_won_pct"].append(second_won / second_played)
            if bp_faced > 0:
                self.serve_stats[player]["bp_save_pct"].append(bp_saved / bp_faced)

        # Top 50
        w_rank = match.get("winner_rank", 999)
        l_rank = match.get("loser_rank", 999)
        if not pd.isna(l_rank) and l_rank <= 50:
            self.top50_matches[winner] += 1
            self.top50_wins[winner] += 1
        if not pd.isna(w_rank) and w_rank <= 50:
            self.top50_matches[loser] += 1


# ============================================================================
# Main chronological pass
# ============================================================================

def build_features():
    """
    Single chronological pass over all 941K matches.
    Builds training matrix with ~155 features per match.
    """
    start_time = time.time()

    # Load data
    matches = load_sackmann_matches()
    cpi_data = load_cpi()
    weather_lookup = load_weather_cache()

    # Load and aggregate charted point data
    charted_lookup = {}
    if PARSED_POINTS.exists():
        try:
            points_df = pd.read_parquet(PARSED_POINTS)
            charted_lookup = aggregate_charted_points(points_df)
            del points_df  # Free memory — no longer needed after aggregation
        except Exception as e:
            print(f"Warning: could not load charted data: {e}")
    else:
        print(f"Warning: {PARSED_POINTS} not found — charted features will use defaults")

    # Initialize accumulators
    glicko = Glicko2RatingSystem()
    fatigue = FatigueAccumulator()
    legacy = LegacyAccumulator()
    charted_acc = ChartedAccumulator(window=30)
    attributes: Dict[str, PlayerAttributeAccumulator] = {}

    # Helper for name normalization (matches what aggregate_charted_points uses)
    def _norm(name: str) -> str:
        return str(name).replace('_', ' ').strip().lower()

    # Output containers
    feature_rows = []
    labels = []
    dates_list = []

    skipped = 0
    processed = 0

    # ================================================================
    # VALIDATION: Show 5 example matches with actual_match_date derivation
    # ================================================================
    print(f"\n{'='*60}")
    print("WEATHER V2 VALIDATION — Round → actual_match_date")
    print(f"{'='*60}")
    sample_indices = []
    target_rounds = ["R128", "R64", "R32", "QF", "F"]
    for tgt_round in target_rounds:
        mask = matches["round"] == tgt_round
        if mask.any():
            sample_indices.append(matches[mask].index[0])
    for si in sample_indices[:5]:
        sr = matches.loc[si]
        t_date = sr["tourney_date"].date()
        t_level = str(sr.get("tourney_level", "A"))
        rnd = str(sr.get("round", "R32"))
        ds_raw = sr.get("draw_size", None)
        try:
            ds = int(ds_raw) if ds_raw and not pd.isna(ds_raw) else None
        except Exception:
            ds = None
        amd = compute_actual_match_date(t_level, t_date, rnd, ds)
        t_name = str(sr.get("tourney_name", "?"))
        is_ind = get_is_indoor(t_name.lower())
        print(
            f"  {t_name[:25]:25s} | level={t_level} | round={rnd:5s} | "
            f"tourney_date={t_date} → actual={amd} | indoor={is_ind}"
        )
    print(f"{'='*60}")

    print(f"\nProcessing {len(matches):,} matches chronologically...")
    print(f"{'='*60}")

    for idx, row in matches.iterrows():
        # Progress
        if idx > 0 and idx % 100_000 == 0:
            elapsed = time.time() - start_time
            rate = idx / elapsed
            remaining = (len(matches) - idx) / rate
            print(
                f"  {idx:>8,} / {len(matches):,} | "
                f"{processed:,} features | "
                f"{elapsed:.0f}s elapsed | "
                f"~{remaining:.0f}s remaining"
            )

        # Extract match info
        winner = row.get("winner_name")
        loser = row.get("loser_name")
        if not winner or not loser or pd.isna(winner) or pd.isna(loser):
            skipped += 1
            continue

        match_date_ts = row["tourney_date"]
        tourney_date = match_date_ts.date() if hasattr(match_date_ts, "date") else match_date_ts

        surface = row.get("surface_clean", "hard")
        tourney_level = row.get("tourney_level", "A")
        best_of_raw = row.get("best_of", 3)
        try:
            best_of = int(best_of_raw)
        except (ValueError, TypeError):
            best_of = 3

        round_code = str(row.get("round", "R32")).strip()
        draw_size_raw = row.get("draw_size", None)
        try:
            draw_size = int(draw_size_raw) if draw_size_raw and not pd.isna(draw_size_raw) else None
        except (ValueError, TypeError):
            draw_size = None

        # Derive actual match date from round code (not tournament start)
        actual_match_date = compute_actual_match_date(
            tourney_level, tourney_date, round_code, draw_size
        )
        match_date = actual_match_date  # Use for accumulators too (date of this match)

        score_str = str(row.get("score", ""))
        minutes = row.get("minutes")
        w_rank = row.get("winner_rank", 999)
        l_rank = row.get("loser_rank", 999)
        if pd.isna(w_rank):
            w_rank = 999
        if pd.isna(l_rank):
            l_rank = 999

        # Initialize attribute accumulators if needed
        for p in [winner, loser]:
            if p not in attributes:
                attributes[p] = PlayerAttributeAccumulator(name=p)

        # ================================================================
        # STEP 1: SNAPSHOT BEFORE UPDATE (zero leakage)
        # ================================================================

        # Glicko-2 snapshots
        w_glicko = glicko.snapshot(winner, surface, match_date)
        l_glicko = glicko.snapshot(loser, surface, match_date)

        # Fatigue snapshots
        w_fatigue = fatigue.snapshot(winner, match_date)
        l_fatigue = fatigue.snapshot(loser, match_date)

        # Legacy rolling stats
        w_legacy = legacy.snapshot(winner, loser, surface)
        l_legacy = legacy.snapshot(loser, winner, surface)

        # Charted features snapshots
        w_charted_snap = charted_acc.snapshot(winner)
        l_charted_snap = charted_acc.snapshot(loser)

        # ================================================================
        # STEP 2: BUILD FEATURE ROWS
        # ================================================================

        # Match context features (static, no leakage risk)
        context = {
            "surface_code": SURFACE_CODE_MAP.get(surface, 0),
            "tourney_level_code": TOURNEY_LEVEL_MAP.get(tourney_level, 1),
            "best_of": best_of,
            "ball_type": 0,  # Placeholder — enrich from tournament data if available
        }

        # CPI (raw from court_speed.csv — 0 if not measured)
        tourney_name = str(row.get("tourney_name", "")).strip().lower()
        raw_cpi = float(cpi_data.get(tourney_name, 0) or 0)

        # Indoor/outdoor classification
        is_indoor = get_is_indoor(tourney_name)

        # Compute weather features v2 (uses actual_match_date, not tourney_date)
        year = actual_match_date.year
        weather_feats = get_weather_features_v2(
            weather_lookup,
            tourney_name,
            tourney_level,
            actual_match_date,
            round_code,
            surface,
            is_indoor,
            year,
            real_cpi=raw_cpi,
        )
        context.update(weather_feats)
        # Keep legacy cpi column pointing to imputed value for backward compat
        context["cpi"] = weather_feats["imputed_cpi"]

        # Rank diff
        # rank_diff computed per-perspective below

        # Build Glicko features
        glicko_feats_wp1 = build_glicko_features(w_glicko, l_glicko)
        glicko_feats_lp1 = build_glicko_features(l_glicko, w_glicko)

        # Build fatigue features
        fatigue_feats_wp1 = build_fatigue_features(w_fatigue, l_fatigue)
        fatigue_feats_lp1 = build_fatigue_features(l_fatigue, w_fatigue)

        # Build charted features (two perspectives)
        charted_feats_wp1 = build_charted_features(w_charted_snap, l_charted_snap)
        charted_feats_lp1 = build_charted_features(l_charted_snap, w_charted_snap)

        # Build legacy features (two perspectives)
        def legacy_row(p1_legacy, p2_legacy):
            feats = {}
            for k, v in p1_legacy.items():
                feats[f"p1_{k}"] = v
            for k, v in p2_legacy.items():
                feats[f"p2_{k}"] = v
            # Diffs
            for k in p1_legacy:
                feats[f"{k}_diff"] = p1_legacy[k] - p2_legacy[k]
            return feats

        legacy_wp1 = legacy_row(w_legacy, l_legacy)
        legacy_lp1 = legacy_row(l_legacy, w_legacy)

        # Weather interaction features (using pre-match snapshots — zero leakage)
        wx_wp1 = build_weather_interaction_features(
            weather_feats,
            p1_fatigue_snap=w_fatigue, p2_fatigue_snap=l_fatigue,
            p1_charted_snap=w_charted_snap, p2_charted_snap=l_charted_snap,
            p1_legacy_snap=w_legacy, p2_legacy_snap=l_legacy,
        )
        wx_lp1 = build_weather_interaction_features(
            weather_feats,
            p1_fatigue_snap=l_fatigue, p2_fatigue_snap=w_fatigue,
            p1_charted_snap=l_charted_snap, p2_charted_snap=w_charted_snap,
            p1_legacy_snap=l_legacy, p2_legacy_snap=w_legacy,
        )

        # Combine all features into rows
        # Row 1: winner as p1 (label = 1)
        row_wp1 = {}
        row_wp1.update(context)
        row_wp1["rank_diff"] = float(l_rank) - float(w_rank)
        row_wp1.update(glicko_feats_wp1)
        row_wp1.update(fatigue_feats_wp1)
        row_wp1.update(charted_feats_wp1)
        row_wp1.update(legacy_wp1)
        row_wp1.update(wx_wp1)

        # Row 2: loser as p1 (label = 0)
        row_lp1 = {}
        row_lp1.update(context)
        row_lp1["rank_diff"] = float(w_rank) - float(l_rank)
        row_lp1.update(glicko_feats_lp1)
        row_lp1.update(fatigue_feats_lp1)
        row_lp1.update(charted_feats_lp1)
        row_lp1.update(legacy_lp1)
        row_lp1.update(wx_lp1)

        feature_rows.append(row_wp1)
        labels.append(1)
        dates_list.append(match_date_ts)

        feature_rows.append(row_lp1)
        labels.append(0)
        dates_list.append(match_date_ts)

        processed += 2

        # ================================================================
        # STEP 3: UPDATE ACCUMULATORS AFTER SNAPSHOT
        # ================================================================

        # Glicko-2
        glicko.record_result(winner, loser, surface, match_date)

        # Fatigue — update opponent profiles first, then record match
        match_dict = row.to_dict()

        # Update grinding profiles for both players
        w_svpt = row.get("w_svpt", 0)
        l_svpt = row.get("l_svpt", 0)
        total_points = (float(w_svpt) if not pd.isna(w_svpt) else 0) + (
            float(l_svpt) if not pd.isna(l_svpt) else 0
        )
        total_games = float(row.get("w_SvGms", 0) or 0) + float(row.get("l_SvGms", 0) or 0)

        for prefix, player in [("w_", winner), ("l_", loser)]:
            fatigue.update_opponent_profile(
                player,
                {
                    "total_points": total_points / 2,  # Approximate per-player
                    "total_games": total_games / 2,
                    "winners": float(match_dict.get(f"{prefix}winners", 0) or 0),
                    "ue": float(match_dict.get(f"{prefix}ue", 0) or 0),
                    "return_points": float(
                        match_dict.get(f"{'l_' if prefix == 'w_' else 'w_'}svpt", 0) or 0
                    ),
                },
            )

        # Record matches for both players
        for player, opponent, opp_rank in [
            (winner, loser, l_rank),
            (loser, winner, w_rank),
        ]:
            fatigue.record_match(
                player=player,
                match_date=match_date,
                minutes=float(minutes) if minutes and not pd.isna(minutes) else 0,
                opponent=opponent,
                opponent_rank=int(opp_rank),
                score_str=score_str,
                best_of=best_of,
                surface=surface,
            )

        # Legacy accumulators
        legacy.update(winner, loser, surface, match_dict)

        # Charted accumulator — update only if this match has point-level data
        charted_match = get_charted_for_match(
            charted_lookup, match_date, winner, loser
        )
        if charted_match:
            w_norm = _norm(winner)
            l_norm = _norm(loser)
            w_charted_stats = charted_match.get(w_norm)
            l_charted_stats = charted_match.get(l_norm)
            if w_charted_stats:
                charted_acc.update(winner, w_charted_stats)
                # Feed avg_rally_length to fatigue grinding profile
                if w_charted_stats.get('avg_rally_length'):
                    fatigue.update_opponent_profile(
                        winner, {'avg_rally_length': w_charted_stats['avg_rally_length']}
                    )
            if l_charted_stats:
                charted_acc.update(loser, l_charted_stats)
                if l_charted_stats.get('avg_rally_length'):
                    fatigue.update_opponent_profile(
                        loser, {'avg_rally_length': l_charted_stats['avg_rally_length']}
                    )

        # Player attributes — also pass charted data if available
        for is_winner, player in [(True, winner), (False, loser)]:
            fatigue_snap = w_fatigue if player == winner else l_fatigue
            p_charted = None
            if charted_match:
                p_norm = _norm(player)
                raw_stats = charted_match.get(p_norm)
                if raw_stats:
                    # Map to the format expected by update_attributes_from_match
                    p_charted = {
                        'aggression': raw_stats.get('aggression_index'),
                        'pattern_diversity': raw_stats.get('pattern_diversity'),
                        'serve_entropy': raw_stats.get('serve_entropy'),
                    }
            update_attributes_from_match(
                attributes[player],
                match_dict,
                is_winner=is_winner,
                fatigue_snapshot=fatigue_snap,
                charted_data=p_charted,
            )

    # ================================================================
    # STEP 4: ASSEMBLE OUTPUT
    # ================================================================

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Chronological pass complete: {elapsed:.1f}s")
    print(f"Processed: {processed:,} feature rows from {len(matches):,} matches")
    print(f"Skipped: {skipped:,} matches (missing names)")

    print("\nBuilding DataFrame...")
    X = pd.DataFrame(feature_rows)
    y = pd.Series(labels, name="label")
    dates = pd.Series(dates_list, name="date")

    print(f"Feature matrix: {X.shape[0]:,} rows × {X.shape[1]} features")
    print(f"Features: {X.columns.tolist()[:20]}...")

    # Verify temporal split
    train_count = (dates < TEMPORAL_CUTOFF).sum()
    test_count = (dates >= TEMPORAL_CUTOFF).sum()
    print(f"\nTemporal split verification:")
    print(f"  Train (pre-2023): {train_count:,}")
    print(f"  Test (2023+):     {test_count:,}")

    # Save training data
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "wb") as f:
        pickle.dump((X, y, dates), f, protocol=pickle.HIGHEST_PROTOCOL)

    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"Saved: {file_size:.1f} MB")

    # Save attribute accumulators (for FIFA cards)
    print(f"Saving player attributes to {ATTRIB_FILE}...")
    with open(ATTRIB_FILE, "wb") as f:
        pickle.dump(attributes, f, protocol=pickle.HIGHEST_PROTOCOL)

    # Save Glicko system state (for live predictions)
    glicko_file = DATA_DIR / "processed" / "glicko2_state.pkl"
    with open(glicko_file, "wb") as f:
        pickle.dump(glicko, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Saved Glicko-2 state: {len(glicko.ratings)} players")

    # ================================================================
    # STEP 5: QUICK VALIDATION
    # ================================================================

    print(f"\n{'='*60}")
    print("QUICK VALIDATION")
    print(f"{'='*60}")

    # Train XGBoost and check Brier
    try:
        import xgboost as xgb
        from sklearn.metrics import brier_score_loss

        tr = dates < TEMPORAL_CUTOFF
        te = ~tr

        print(f"\nTraining XGBoost on {tr.sum():,} rows...")
        model = xgb.XGBClassifier(
            max_depth=6,
            learning_rate=0.1,
            n_estimators=700,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=42,
        )
        model.fit(X[tr], y[tr])

        probs = model.predict_proba(X[te])[:, 1]
        brier = brier_score_loss(y[te], probs)

        print(f"\n  V4 XGBoost Brier:  {brier:.4f}")
        print(f"  V2 LGBM baseline:  0.1855")
        print(f"  Delta:             {brier - 0.1855:+.4f}")

        if brier < 0.1855:
            print(f"  IMPROVEMENT — weather v2 + interactions are helping")
        elif brier < 0.1863:
            print(f"  NEUTRAL — on par with v2 XGB (0.1863), no regression")
        else:
            print(f"  REGRESSION — weather features adding noise, review")

        # Top feature importances
        importance = model.feature_importances_
        feat_imp = sorted(
            zip(X.columns, importance), key=lambda x: x[1], reverse=True
        )
        print(f"\nTop 15 features:")
        for fname, imp in feat_imp[:15]:
            print(f"  {fname:35s} {imp:.4f}")

        # Save the model
        model_file = PROJECT_ROOT / "models" / "hard" / "best_edge_v2_model.pkl"
        model_file.parent.mkdir(parents=True, exist_ok=True)
        with open(model_file, "wb") as f:
            pickle.dump(model, f)
        print(f"\nModel saved to {model_file}")

    except ImportError as e:
        print(f"\nCould not run validation: {e}")
        print("Install with: pip install xgboost scikit-learn --break-system-packages")

    # Summary
    total_time = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"BUILD COMPLETE")
    print(f"Total time: {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Features: {X.shape[1]}")
    print(f"ZERO LEAKAGE: Snapshot before update, temporal split enforced")
    print(f"{'='*60}")


if __name__ == "__main__":
    build_features()
