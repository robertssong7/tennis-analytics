"""
TennisIQ Score-State Analysis Module
Deep behavioral analysis by game state, set context, and match format.
Usage:
  python3 scripts/score_state_analysis.py "Carlos Alcaraz"
  python3 scripts/score_state_analysis.py "Carlos Alcaraz" "Novak Djokovic"
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
PLAYER_PROFILES = REPO_ROOT / "data" / "processed" / "player_profiles.parquet"

# Map point score string to number of points played (for deuce/ad court)
SCORE_TO_POINTS = {
    "0-0": 0, "15-0": 1, "0-15": 1,
    "30-0": 2, "15-15": 2, "0-30": 2,
    "40-0": 3, "30-15": 3, "15-30": 3, "0-40": 3,
    "40-15": 4, "30-30": 4, "15-40": 4,
    "40-30": 5, "30-40": 5,
    "40-40": 6, "AD-40": 7, "40-AD": 7,
}


def fuzzy_find_player(name, known_players):
    name_lower = name.strip().lower()
    for p in known_players:
        if p.lower() == name_lower:
            return p
    matches = [p for p in known_players if name_lower in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous name '{name}'. Matches: {matches[:10]}")
        sys.exit(1)
    matches = [p for p in known_players if name_lower.split()[-1] in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous last name '{name}'. Matches: {matches[:10]}")
        sys.exit(1)
    print(f"Player '{name}' not found in charted data.")
    sys.exit(1)


def enrich_points(df):
    """Add derived score-state columns to points DataFrame."""
    df = df.copy()

    # Who is serving (by name)
    df["server"] = np.where(df["Svr"] == 1, df["Player 1"], df["Player 2"])
    df["returner"] = np.where(df["Svr"] == 1, df["Player 2"], df["Player 1"])
    df["point_winner"] = np.where(df["PtWinner"] == 1, df["Player 1"], df["Player 2"])

    # Server's game lead: positive = server ahead, negative = server behind
    server_games = np.where(df["Svr"] == 1, df["Gm1"], df["Gm2"])
    returner_games = np.where(df["Svr"] == 1, df["Gm2"], df["Gm1"])
    df["server_game_lead"] = server_games - returner_games

    # Set score from server's perspective
    server_sets = np.where(df["Svr"] == 1, df["Set1"], df["Set2"])
    returner_sets = np.where(df["Svr"] == 1, df["Set2"], df["Set1"])
    df["server_set_lead"] = server_sets - returner_sets

    # Deuce vs Ad court
    pts_played = df["Pts"].map(SCORE_TO_POINTS)
    df["court_side"] = np.where(pts_played % 2 == 0, "deuce", "ad")
    # NaN for unmapped scores (tiebreaks, etc.) — fill as unknown
    df["court_side"] = df["court_side"].fillna("unknown")

    # Tiebreak
    df["is_tiebreak"] = (df["Gm1"].fillna(0) >= 6) & (df["Gm2"].fillna(0) >= 6)

    # Pressure classification
    df["is_break_point"] = False
    # Server at risk: returner at 40, server below 40 or AD-40
    for svr_val, s1col, s2col in [(1, "Pts", "Pts"), (2, "Pts", "Pts")]:
        mask_svr = df["Svr"] == svr_val
        pts = df["Pts"].astype(str)
        if svr_val == 1:
            # P1 serves, break point if P2 score is 40/AD and P1 is below
            bp = mask_svr & (
                (pts.str.endswith("-40") & pts.str.split("-").str[0].isin(["0", "15", "30"])) |
                (pts == "40-AD")
            )
        else:
            # P2 serves, break point if P1 score is 40/AD and P2 is below
            bp = mask_svr & (
                (pts.str.startswith("40-") & pts.str.split("-").str[1].isin(["0", "15", "30"])) |
                (pts == "AD-40")
            )
        df.loc[bp, "is_break_point"] = True

    # Game point for server
    df["is_game_point"] = False
    for svr_val in [1, 2]:
        mask_svr = df["Svr"] == svr_val
        pts = df["Pts"].astype(str)
        if svr_val == 1:
            gp = mask_svr & (
                (pts.str.startswith("40-") & pts.str.split("-").str[1].isin(["0", "15", "30"])) |
                (pts == "AD-40")
            )
        else:
            gp = mask_svr & (
                (pts.str.endswith("-40") & pts.str.split("-").str[0].isin(["0", "15", "30"])) |
                (pts == "40-AD")
            )
        df.loc[gp, "is_game_point"] = True

    # Match format
    df["best_of"] = df["Best of"].astype(str).str.strip()

    return df


def serve_dir_stats(pts_df):
    """Return serve direction dict from a slice."""
    n = len(pts_df)
    if n == 0:
        return {"n": 0, "wide": 0, "body": 0, "T": 0, "entropy": 0}
    vc = pts_df["serve_direction"].value_counts()
    w = vc.get("wide", 0) / n
    b = vc.get("body", 0) / n
    t = vc.get("T", 0) / n
    probs = np.array([w, b, t])
    probs = probs[probs > 0]
    ent = -np.sum(probs * np.log2(probs)) if len(probs) > 1 else 0.0
    return {"n": n, "wide": w, "body": b, "T": t, "entropy": ent}


def aggression_stats(pts_df):
    """Return aggression index from a slice."""
    n = len(pts_df)
    if n == 0:
        return {"n": 0, "aggression": 0, "avg_rally": 0}
    oc = pts_df["point_outcome"].value_counts()
    winners = oc.get("winner", 0) + oc.get("ace", 0)
    ue = oc.get("unforced_error", 0)
    denom = winners + ue
    return {
        "n": n,
        "aggression": winners / denom if denom > 10 else None,
        "avg_rally": pts_df["rally_length"].mean(),
        "short_pct": (pts_df["rally_length"] < 4).mean(),
        "long_pct": (pts_df["rally_length"] > 8).mean(),
        "win_pct": (pts_df["point_winner"] == pts_df["server"]).mean() if n > 0 else 0,
    }


def fmt(val, pct=True):
    if val is None:
        return "   N/A"
    if pct:
        return f"{val * 100:5.1f}%"
    return f"{val:6.2f}"


def print_serve_table(label, stats, baseline=None, min_n=10):
    """Print a serve direction row with optional delta from baseline."""
    if stats["n"] < min_n:
        print(f"  {label:32s}  ({stats['n']} pts — too few)")
        return
    line = f"  {label:32s}  n={stats['n']:>4d}  W={fmt(stats['wide'])}  B={fmt(stats['body'])}  T={fmt(stats['T'])}  H={stats['entropy']:.2f}"
    if baseline and baseline["n"] >= min_n:
        dw = stats["wide"] - baseline["wide"]
        dt = stats["T"] - baseline["T"]
        line += f"  | dW={dw:+.1%} dT={dt:+.1%}"
    print(line)


def analyze_player_serving(df, player_name):
    """Full score-state analysis for one player's serve."""
    serving = df[df["server"] == player_name]
    n_total = len(serving)
    if n_total == 0:
        print(f"  No serve data for {player_name}")
        return

    baseline = serve_dir_stats(serving)
    baseline_agg = aggression_stats(serving)

    print(f"\n{'=' * 90}")
    print(f"  {player_name.upper()} — SCORE-STATE SERVING ANALYSIS ({n_total} serve points)")
    print(f"{'=' * 90}")

    # ── Overall baseline ──
    print(f"\n  --- SERVE DIRECTION BY CONTEXT ---")
    print(f"  {'Context':32s}  {'':>6s}  {'Wide':>6s}  {'Body':>6s}  {'T':>7s}  {'H':>5s}  | vs baseline")
    print_serve_table("ALL SERVE POINTS (baseline)", baseline)

    # ── Court side ──
    print()
    for side in ["deuce", "ad"]:
        sl = serving[serving["court_side"] == side]
        print_serve_table(f"{side.title()} court", serve_dir_stats(sl), baseline)

    # ── Pressure situations ──
    print()
    bp = serving[serving["is_break_point"]]
    gp = serving[serving["is_game_point"]]
    deuce = serving[serving["Pts"].astype(str) == "40-40"]
    print_serve_table("Break point (facing)", serve_dir_stats(bp), baseline)
    print_serve_table("Game point (holding)", serve_dir_stats(gp), baseline)
    print_serve_table("Deuce (40-40)", serve_dir_stats(deuce), baseline)

    # ── Specific point scores ──
    print()
    for score_label, score_val in [
        ("0-0 (opening point)", "0-0"),
        ("0-30 (in trouble)", "0-30"),
        ("30-0 (cruising)", "30-0"),
        ("0-40 (triple BP)", "0-40"),
        ("15-40 (double BP)", "15-40"),
        ("30-40 (break point)", "30-40"),
    ]:
        # Adjust for server perspective
        sl1 = serving[(serving["Svr"] == 1) & (serving["Pts"].astype(str) == score_val)]
        # For Svr==2, flip the score
        flipped = "-".join(reversed(score_val.split("-")))
        sl2 = serving[(serving["Svr"] == 2) & (serving["Pts"].astype(str) == flipped)]
        sl = pd.concat([sl1, sl2])
        print_serve_table(score_label, serve_dir_stats(sl), baseline)

    # ── Game lead context ──
    print(f"\n  --- SERVE DIRECTION BY GAME CONTEXT ---")
    print(f"  {'Context':32s}  {'':>6s}  {'Wide':>6s}  {'Body':>6s}  {'T':>7s}  {'H':>5s}  | vs baseline")
    for label, cond in [
        ("Down 2+ breaks", serving["server_game_lead"] <= -2),
        ("Down 1 break", serving["server_game_lead"] == -1),
        ("On serve (equal)", serving["server_game_lead"] == 0),
        ("Up 1 break", serving["server_game_lead"] == 1),
        ("Up 2+ breaks", serving["server_game_lead"] >= 2),
    ]:
        sl = serving[cond]
        print_serve_table(label, serve_dir_stats(sl), baseline)

    # ── Set context ──
    print()
    for label, cond in [
        ("Down a set", serving["server_set_lead"] < 0),
        ("Sets level", serving["server_set_lead"] == 0),
        ("Up a set", serving["server_set_lead"] > 0),
    ]:
        sl = serving[cond]
        print_serve_table(label, serve_dir_stats(sl), baseline)

    # ── Tiebreaks ──
    print()
    tb = serving[serving["is_tiebreak"]]
    print_serve_table("In tiebreak", serve_dir_stats(tb), baseline)

    # ── Best-of-3 vs Best-of-5 ──
    bo3 = serving[serving["best_of"] == "3"]
    bo5 = serving[serving["best_of"] == "5"]
    if len(bo3) > 20 and len(bo5) > 20:
        print()
        print_serve_table("Best-of-3 matches", serve_dir_stats(bo3), baseline)
        print_serve_table("Best-of-5 matches", serve_dir_stats(bo5), baseline)

    # ── Aggression by context ──
    print(f"\n  --- AGGRESSION & RALLY BY CONTEXT ---")
    print(f"  {'Context':32s}  {'n':>5s}  {'Aggr':>6s}  {'AvgRL':>6s}  {'Short':>6s}  {'Long':>6s}  {'WinPt':>6s}")

    for label, sl in [
        ("All serve points", serving),
        ("Break point (facing)", bp),
        ("Game point (holding)", gp),
        ("Down 2+ breaks", serving[serving["server_game_lead"] <= -2]),
        ("Down 1 break", serving[serving["server_game_lead"] == -1]),
        ("On serve (equal)", serving[serving["server_game_lead"] == 0]),
        ("Up 1 break", serving[serving["server_game_lead"] == 1]),
        ("Up 2+ breaks", serving[serving["server_game_lead"] >= 2]),
        ("Down a set", serving[serving["server_set_lead"] < 0]),
        ("Up a set", serving[serving["server_set_lead"] > 0]),
        ("In tiebreak", tb),
    ]:
        a = aggression_stats(sl)
        if a["n"] < 10:
            print(f"  {label:32s}  {a['n']:>5d}  (too few)")
            continue
        print(
            f"  {label:32s}  {a['n']:>5d}  {fmt(a['aggression'])}  "
            f"{a['avg_rally']:>5.1f}  {fmt(a['short_pct'])}  {fmt(a['long_pct'])}  {fmt(a['win_pct'])}"
        )

    if len(bo3) > 20 and len(bo5) > 20:
        print()
        for label, sl in [("Best-of-3", bo3), ("Best-of-5", bo5)]:
            a = aggression_stats(sl)
            print(
                f"  {label:32s}  {a['n']:>5d}  {fmt(a['aggression'])}  "
                f"{a['avg_rally']:>5.1f}  {fmt(a['short_pct'])}  {fmt(a['long_pct'])}  {fmt(a['win_pct'])}"
            )


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 scripts/score_state_analysis.py 'Player Name'")
        print("  python3 scripts/score_state_analysis.py 'Player A' 'Player B'")
        print("\nTop 20 most-charted players:")
        profiles = pd.read_parquet(PLAYER_PROFILES)
        top = profiles.nlargest(20, "n_charted_matches")[["player", "n_charted_matches"]]
        for _, row in top.iterrows():
            print(f"  {row['player']:30s} ({int(row['n_charted_matches'])} matches)")
        sys.exit(0)

    print("Loading data...")
    points = pd.read_parquet(PARSED_POINTS)
    all_players = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))

    player_a = fuzzy_find_player(sys.argv[1], all_players)

    # Filter to relevant matches
    if len(sys.argv) >= 3:
        player_b = fuzzy_find_player(sys.argv[2], all_players)
        mask = (
            ((points["Player 1"] == player_a) & (points["Player 2"] == player_b)) |
            ((points["Player 1"] == player_b) & (points["Player 2"] == player_a))
        )
        points = points[mask]
        n_matches = points["match_id"].nunique()
        print(f"Matched: {player_a} vs {player_b} ({n_matches} charted matches, {len(points)} points)")
    else:
        player_b = None
        mask = (points["Player 1"] == player_a) | (points["Player 2"] == player_a)
        points = points[mask]
        n_matches = points["match_id"].nunique()
        print(f"Matched: {player_a} ({n_matches} charted matches, {len(points)} points)")

    points = enrich_points(points)

    analyze_player_serving(points, player_a)
    if player_b:
        analyze_player_serving(points, player_b)

    print(f"\n{'=' * 90}")


if __name__ == "__main__":
    main()
