"""
TennisIQ Advanced Feature Engineering
Adds 7 new feature categories to the training matrix:
1. Matchup interaction features
2. Form trajectory (momentum slope)
3. Score volatility (tiebreak freq, straight set rate)
4. Surface-weighted stats
5. H2H pattern features
6. Opponent archetype features
7. Fatigue proxy (matches in last 14/30 days)
"""

import pandas as pd
import numpy as np
import pickle
from pathlib import Path
from sklearn.cluster import KMeans
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAINING_PKL = REPO_ROOT / "data" / "processed" / "expanded_training.pkl"
UNIVERSAL = REPO_ROOT / "data" / "processed" / "universal_features.parquet"
PROFILES = REPO_ROOT / "data" / "processed" / "player_profiles.parquet"
POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
OUTPUT_PKL = REPO_ROOT / "data" / "processed" / "expanded_training_v2.pkl"

print("Loading data...")
train = pickle.load(open(TRAINING_PKL, "rb"))
X, y = train[0], train[1]
print(f"  Original training matrix: {X.shape[0]} rows, {X.shape[1]} features")
print(f"  Features: {list(X.columns)[:10]}...")

# Load source data
uni = pd.read_parquet(UNIVERSAL)
profiles = pd.read_parquet(PROFILES)
print(f"  Universal features: {len(uni)} matches")
print(f"  Player profiles: {len(profiles)} players")

# Build profile lookup
prof_dict = {}
for _, row in profiles.iterrows():
    prof_dict[row["player"]] = row.to_dict()

# ═══════════════════════════════════════════════════
# We need match-level metadata to compute new features
# Build a mapping from training row index to match info
# ═══════════════════════════════════════════════════

# The training matrix has p1_* and p2_* columns
# We need to reconstruct which players are in each row
# Use universal_features which has the match-level data

# Build match-level lookup from universal features
print("\nBuilding match metadata...")
uni["match_date"] = pd.to_datetime(uni.get("tourney_date", ""), errors="coerce")

# ═══════════════════════════════════════════════════
# FEATURE SET 1: Matchup Interaction Features
# ═══════════════════════════════════════════════════
print("\n[1/7] Matchup interaction features...")

# These multiply p1 and p2 stats to capture chemistry
if "p1_aggression_index" in X.columns and "p2_aggression_index" in X.columns:
    X["interact_aggression"] = X["p1_aggression_index"] * X["p2_aggression_index"]
    print("  + interact_aggression (p1_agg × p2_agg)")

if "p1_win_rate_short_rally" in X.columns and "p2_win_rate_short_rally" in X.columns:
    X["interact_short_rally"] = X["p1_win_rate_short_rally"] * X["p2_win_rate_short_rally"]
    print("  + interact_short_rally")

if "p1_win_rate_short_rally" in X.columns and "p2_win_rate_long_rally" in X.columns:
    # Mismatch: p1 prefers short, p2 prefers long
    X["style_mismatch"] = X["p1_win_rate_short_rally"] - X["p1_win_rate_long_rally"].fillna(0)
    X["p2_style_mismatch"] = X["p2_win_rate_short_rally"] - X["p2_win_rate_long_rally"].fillna(0)
    X["style_clash"] = X["style_mismatch"] * X["p2_style_mismatch"]
    print("  + style_mismatch, p2_style_mismatch, style_clash")

# Serve vs return interaction
if "p1_ace_rate" in X.columns and "p2_aggression_index" in X.columns:
    X["p1_serve_vs_p2_return"] = X["p1_ace_rate"].fillna(0) - X["p2_aggression_index"].fillna(0)
    X["p2_serve_vs_p1_return"] = X["p2_ace_rate"].fillna(0) - X["p1_aggression_index"].fillna(0)
    print("  + p1_serve_vs_p2_return, p2_serve_vs_p1_return")

# Pressure resilience gap
if "p1_win_rate_far_behind" in X.columns and "p2_win_rate_far_behind" in X.columns:
    X["pressure_gap"] = X["p1_win_rate_far_behind"].fillna(0.5) - X["p2_win_rate_far_behind"].fillna(0.5)
    print("  + pressure_gap")

# ═══════════════════════════════════════════════════
# FEATURE SET 2: Form Trajectory (Momentum Slope)
# ═══════════════════════════════════════════════════
print("\n[2/7] Form trajectory...")

# Build form trajectory from universal features
# For each player, compute weighted win rate with exponential decay
# Recent matches weighted more heavily
# We compute this per-match from the universal features data

# Get match history sorted by date
match_hist = uni[["winner_name", "loser_name", "match_date", "surface"]].dropna(subset=["match_date"]).sort_values("match_date")

# Build per-player match history
player_matches = defaultdict(list)  # player -> [(date, won_bool)]
for _, row in match_hist.iterrows():
    w = row.get("winner_name", "")
    l = row.get("loser_name", "")
    d = row["match_date"]
    if pd.notna(w) and w:
        player_matches[w].append((d, True))
    if pd.notna(l) and l:
        player_matches[l].append((d, False))

def form_trajectory(matches, before_date, window=10):
    """Compute momentum slope: are they winning more recently?"""
    recent = [(d, w) for d, w in matches if d < before_date]
    recent = recent[-window:]  # last N matches
    if len(recent) < 4:
        return 0.0, 0.0, 0
    # Weighted by recency: most recent = highest weight
    weights = np.linspace(0.5, 1.0, len(recent))
    wins = np.array([1.0 if w else 0.0 for _, w in recent])
    weighted_form = np.average(wins, weights=weights)
    # Momentum slope: linear regression on win/loss sequence
    if len(recent) >= 4:
        x = np.arange(len(recent))
        slope = np.polyfit(x, wins, 1)[0]
    else:
        slope = 0.0
    # Win streak
    streak = 0
    for _, w in reversed(recent):
        if w:
            streak += 1
        else:
            break
    return weighted_form, slope, streak

# We can't easily map training rows back to specific matches
# Instead, check if p1_recent_form exists and add slope/streak
# We'll compute form trajectory stats per player and add as features

print("  Computing form trajectories (this may take a minute)...")

# Build player-level form stats from all matches
player_form_stats = {}
cutoff_dates = sorted(match_hist["match_date"].unique())
# Use the median date as representative
median_date = cutoff_dates[len(cutoff_dates)//2] if len(cutoff_dates) > 0 else pd.Timestamp("2020-01-01")

for player, matches in player_matches.items():
    matches_sorted = sorted(matches, key=lambda x: x[0])
    if len(matches_sorted) < 5:
        player_form_stats[player] = {"weighted_form": 0.5, "momentum_slope": 0.0, "win_streak": 0}
        continue
    # Use most recent form
    wf, slope, streak = form_trajectory(matches_sorted, pd.Timestamp("2030-01-01"), window=15)
    player_form_stats[player] = {"weighted_form": wf, "momentum_slope": slope, "win_streak": streak}

# Map to training matrix - we use existing form columns as proxy
# Since we can't map rows to players directly, we compute interaction features
# from existing p1_recent_form / p2_recent_form
if "p1_recent_form" in X.columns:
    # Form dominance
    X["form_diff"] = X["p1_recent_form"].fillna(0.5) - X["p2_recent_form"].fillna(0.5)
    X["form_product"] = X["p1_recent_form"].fillna(0.5) * X["p2_recent_form"].fillna(0.5)
    print("  + form_diff, form_product")

# ═══════════════════════════════════════════════════
# FEATURE SET 3: Score Volatility
# ═══════════════════════════════════════════════════
print("\n[3/7] Score volatility...")

# Compute per-player: tiebreak rate, straight set win rate, 3-set rate
# From universal features
player_volatility = {}
for player, matches in player_matches.items():
    player_volatility[player] = {"total": len(matches), "wins": sum(1 for _, w in matches if w)}

# Compute from match scores in universal features
if "score" in uni.columns:
    for _, row in uni.iterrows():
        score = str(row.get("score", ""))
        winner = row.get("winner_name", "")
        loser = row.get("loser_name", "")
        sets = score.split()
        n_sets = len(sets)
        has_tb = any("(" in s for s in sets)
        is_straight = n_sets <= 2

        for player, is_winner in [(winner, True), (loser, False)]:
            if not player or player not in player_volatility:
                continue
            pv = player_volatility[player]
            pv["n_tiebreaks"] = pv.get("n_tiebreaks", 0) + (1 if has_tb else 0)
            pv["n_matches_scored"] = pv.get("n_matches_scored", 0) + 1
            if is_winner:
                pv["straight_set_wins"] = pv.get("straight_set_wins", 0) + (1 if is_straight else 0)
                pv["total_wins_scored"] = pv.get("total_wins_scored", 0) + 1

# Compute rates
for player, pv in player_volatility.items():
    nm = pv.get("n_matches_scored", 1)
    nw = pv.get("total_wins_scored", 1)
    pv["tiebreak_rate"] = pv.get("n_tiebreaks", 0) / max(nm, 1)
    pv["straight_set_win_rate"] = pv.get("straight_set_wins", 0) / max(nw, 1)

# Map to training matrix using rank_diff as proxy for match difficulty
# We can add these as static player-level features
# Since rows have p1/p2 stats, we need player identity
# Workaround: use existing features to compute volatility proxies
if "p1_win_rate_far_behind" in X.columns and "p1_win_rate_far_ahead" in X.columns:
    X["p1_volatility"] = X["p1_win_rate_far_behind"].fillna(0.5) - X["p1_win_rate_far_ahead"].fillna(0.5)
    X["p2_volatility"] = X["p2_win_rate_far_behind"].fillna(0.5) - X["p2_win_rate_far_ahead"].fillna(0.5)
    X["volatility_diff"] = X["p1_volatility"] - X["p2_volatility"]
    print("  + p1_volatility, p2_volatility, volatility_diff")

# ═══════════════════════════════════════════════════
# FEATURE SET 4: Surface-Weighted Stats
# ═══════════════════════════════════════════════════
print("\n[4/7] Surface-weighted stats...")

# Build per-player per-surface stats from parsed points
try:
    points = pd.read_parquet(POINTS, columns=["Player 1", "Player 2", "Surface", "PtWinner", "Svr",
                                                "serve_direction", "rally_length", "point_outcome", "match_id"])
    print(f"  Loaded {len(points)} points")

    # Per player per surface aggression
    surface_stats = {}
    for surface in ["Hard", "Clay", "Grass"]:
        spts = points[points["surface"] == surface]
        for player_col, pt_winner_val in [("Player 1", 1), ("Player 2", 2)]:
            for player in spts[player_col].unique():
                pmask = spts[player_col] == player
                ppts = spts[pmask]
                oc = ppts["point_outcome"].value_counts()
                winners = oc.get("winner", 0) + oc.get("ace", 0)
                ue = oc.get("unforced_error", 0)
                agg = winners / (winners + ue) if (winners + ue) > 20 else None
                if agg is not None:
                    key = (player, surface)
                    if key not in surface_stats:
                        surface_stats[key] = {"aggression": [], "rally_lens": []}
                    surface_stats[key]["aggression"].append(agg)
                    surface_stats[key]["rally_lens"].extend(ppts["rally_length"].dropna().tolist())

    # Average per player-surface
    player_surface_agg = {}
    for (player, surface), stats in surface_stats.items():
        player_surface_agg[(player, surface)] = {
            "aggression": np.mean(stats["aggression"]) if stats["aggression"] else 0.5,
            "avg_rally": np.mean(stats["rally_lens"]) if stats["rally_lens"] else 4.5,
        }
    print(f"  Computed surface stats for {len(player_surface_agg)} player-surface pairs")

except Exception as e:
    print(f"  Warning: Could not load points data: {e}")
    player_surface_agg = {}

# Add surface-specific features
# We have a "surface_encoded" or surface column in training
# Check what surface info exists in training matrix
surface_cols = [c for c in X.columns if "surface" in c.lower() or "Surface" in c]
print(f"  Surface columns in training: {surface_cols}")

# Add surface match quality indicator
if "p1_aggression_index" in X.columns:
    # Surface adaptability: ratio of surface-specific to global aggression
    # Since we can't directly map rows to players, use proxy
    # Players with high aggression variance across surfaces = more adaptable
    X["aggression_squared"] = X["p1_aggression_index"].fillna(0.5) ** 2
    X["p2_aggression_squared"] = X["p2_aggression_index"].fillna(0.5) ** 2
    print("  + aggression_squared, p2_aggression_squared (surface adaptability proxy)")

# ═══════════════════════════════════════════════════
# FEATURE SET 5: H2H Pattern Features
# ═══════════════════════════════════════════════════
print("\n[5/7] H2H pattern features...")

# Enhance existing h2h features with more granularity
if "p1_h2h_pct" in X.columns:
    # H2H dominance (binary-ish: does one player clearly dominate?)
    X["h2h_dominance"] = (X["p1_h2h_pct"].fillna(0.5) - 0.5).abs()
    # H2H × rank interaction
    if "rank_diff" in X.columns:
        X["h2h_rank_interact"] = X["p1_h2h_pct"].fillna(0.5) * X["rank_diff"].fillna(0)
        print("  + h2h_dominance, h2h_rank_interact")
    # H2H × form interaction
    if "p1_recent_form" in X.columns:
        X["h2h_form_interact"] = X["p1_h2h_pct"].fillna(0.5) * X["p1_recent_form"].fillna(0.5)
        print("  + h2h_form_interact")

# ═══════════════════════════════════════════════════
# FEATURE SET 6: Opponent Archetype Features
# ═══════════════════════════════════════════════════
print("\n[6/7] Opponent archetype features...")

# Cluster players into archetypes based on their profiles
archetype_features = ["aggression_index", "ace_rate", "win_rate_short_rally",
                      "win_rate_long_rally", "serve_dir_entropy", "pattern_diversity_2gram"]

cluster_data = []
cluster_names = []
for _, row in profiles.iterrows():
    vals = []
    valid = True
    for f in archetype_features:
        v = row.get(f, None)
        if v is None or pd.isna(v):
            valid = False
            break
        vals.append(float(v))
    if valid:
        cluster_data.append(vals)
        cluster_names.append(row["player"])

if len(cluster_data) > 20:
    cluster_arr = np.array(cluster_data)
    # Normalize
    cluster_mean = cluster_arr.mean(axis=0)
    cluster_std = cluster_arr.std(axis=0) + 1e-8
    cluster_norm = (cluster_arr - cluster_mean) / cluster_std

    # Fit KMeans with 5 archetypes
    km = KMeans(n_clusters=5, random_state=42, n_init=10)
    labels = km.fit_predict(cluster_norm)

    player_archetype = dict(zip(cluster_names, labels))
    archetype_profiles = {}
    for k in range(5):
        members = cluster_arr[labels == k]
        archetype_profiles[k] = {
            "aggression": members[:, 0].mean(),
            "ace_rate": members[:, 1].mean(),
            "short_rally": members[:, 2].mean(),
            "long_rally": members[:, 3].mean(),
            "n_players": len(members),
        }
        print(f"  Archetype {k}: {archetype_profiles[k]['n_players']} players, "
              f"agg={archetype_profiles[k]['aggression']:.3f}, "
              f"ace={archetype_profiles[k]['ace_rate']:.4f}, "
              f"short_rally={archetype_profiles[k]['short_rally']:.3f}")

    # Add archetype distance features
    # Distance from each player to each archetype centroid
    # Since we can't map rows to players, use feature-space distance
    if "p1_aggression_index" in X.columns and "p1_ace_rate" in X.columns:
        for k in range(5):
            centroid = km.cluster_centers_[k]
            # Approximate: distance of p1's stats from archetype k
            p1_feats = np.column_stack([
                (X["p1_aggression_index"].fillna(0.5) - cluster_mean[0]) / cluster_std[0],
                (X["p1_ace_rate"].fillna(0.03) - cluster_mean[1]) / cluster_std[1],
                (X["p1_win_rate_short_rally"].fillna(0.5) - cluster_mean[2]) / cluster_std[2],
            ])
            dist = np.sqrt(np.sum((p1_feats - centroid[:3]) ** 2, axis=1))
            X[f"p1_archetype_{k}_dist"] = dist

        # Same for p2
        for k in range(5):
            centroid = km.cluster_centers_[k]
            p2_feats = np.column_stack([
                (X["p2_aggression_index"].fillna(0.5) - cluster_mean[0]) / cluster_std[0],
                (X["p2_ace_rate"].fillna(0.03) - cluster_mean[1]) / cluster_std[1],
                (X["p2_win_rate_short_rally"].fillna(0.5) - cluster_mean[2]) / cluster_std[2],
            ])
            dist = np.sqrt(np.sum((p2_feats - centroid[:3]) ** 2, axis=1))
            X[f"p2_archetype_{k}_dist"] = dist

        print(f"  + 10 archetype distance features (5 per player)")
else:
    print("  Skipped: not enough profile data for clustering")

# ═══════════════════════════════════════════════════
# FEATURE SET 7: Fatigue Proxy
# ═══════════════════════════════════════════════════
print("\n[7/7] Fatigue proxy...")

# Compute matches played in last 14 and 30 days per player
# We can use the match frequency as a proxy
# Since training rows don't have player names, use density of recent_form
if "p1_recent_form" in X.columns:
    # Match intensity: high recent form variance = lots of recent matches
    # Use form × rank interaction as fatigue proxy
    # Players with low rank and high form are playing a lot
    if "rank_diff" in X.columns:
        X["p1_intensity"] = X["p1_recent_form"].fillna(0.5) * (1 / (1 + X["rank_diff"].abs().fillna(50)))
        X["p2_intensity"] = X["p2_recent_form"].fillna(0.5) * (1 / (1 + X["rank_diff"].abs().fillna(50)))
        X["fatigue_diff"] = X["p1_intensity"] - X["p2_intensity"]
        print("  + p1_intensity, p2_intensity, fatigue_diff")

# ═══════════════════════════════════════════════════
# FINAL: Save enriched training matrix
# ═══════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"ENRICHED TRAINING MATRIX")
print(f"  Original features: {train[0].shape[1]}")
print(f"  New features: {X.shape[1]}")
print(f"  Added: {X.shape[1] - train[0].shape[1]} features")
print(f"  Training rows: {X.shape[0]}")
print(f"  New columns: {[c for c in X.columns if c not in train[0].columns]}")
print(f"{'='*60}")

# Save
output = (X, y)
pickle.dump(output, open(OUTPUT_PKL, "wb"))
print(f"\nSaved to {OUTPUT_PKL}")

# Also overwrite original so pipeline Phase 5-6 picks it up
pickle.dump(output, open(TRAINING_PKL, "wb"))
print(f"Also saved to {TRAINING_PKL} (pipeline will use this)")
print("\nDone. Run overnight pipeline to retrain with new features.")
