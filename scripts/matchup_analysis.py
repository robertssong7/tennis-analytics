"""
TennisIQ Matchup Analysis Module
Compares two players' shot patterns in head-to-head matches vs global baselines.
Usage: python3 scripts/matchup_analysis.py "Carlos Alcaraz" "Novak Djokovic"
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
PLAYER_PROFILES = REPO_ROOT / "data" / "processed" / "player_profiles.parquet"


def fuzzy_find_player(name, known_players):
    """Case-insensitive partial match against known player names."""
    name_lower = name.strip().lower()
    # Exact match first
    for p in known_players:
        if p.lower() == name_lower:
            return p
    # Substring match
    matches = [p for p in known_players if name_lower in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous name '{name}'. Matches: {matches[:10]}")
        sys.exit(1)
    # Last name match
    matches = [p for p in known_players if name_lower.split()[-1] in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous last name '{name}'. Matches: {matches[:10]}")
        sys.exit(1)
    print(f"Player '{name}' not found in charted data.")
    sys.exit(1)


def classify_pressure(pts_str, svr):
    """
    Classify point score into pressure context.
    pts_str: e.g. '30-40', 'AD-40'
    svr: 1 or 2 (who is serving)
    Returns: dict with is_break_point, is_game_point, pressure_level
    """
    if not isinstance(pts_str, str) or '-' not in pts_str:
        return {"is_break_point": False, "is_game_point": False, "pressure_level": "normal"}

    parts = pts_str.split('-')
    if len(parts) != 2:
        return {"is_break_point": False, "is_game_point": False, "pressure_level": "normal"}

    p1_score, p2_score = parts[0].strip(), parts[1].strip()

    # Break point: returner is one point from winning the game
    is_break_point = False
    if svr == 1:
        # P1 serving, P2 returning. Break point if P2 at 40 and P1 < 40, or 40-AD
        if p2_score == "40" and p1_score in ("0", "15", "30"):
            is_break_point = True
        if p1_score == "40" and p2_score == "AD":
            is_break_point = True
    elif svr == 2:
        # P2 serving, P1 returning. Break point if P1 at 40 and P2 < 40, or AD-40
        if p1_score == "40" and p2_score in ("0", "15", "30"):
            is_break_point = True
        if p2_score == "40" and p1_score == "AD":
            is_break_point = True

    # Game point for server
    is_game_point = False
    if svr == 1:
        if p1_score == "40" and p2_score in ("0", "15", "30"):
            is_game_point = True
        if p2_score == "40" and p1_score == "AD":
            is_game_point = True
    elif svr == 2:
        if p2_score == "40" and p1_score in ("0", "15", "30"):
            is_game_point = True
        if p1_score == "40" and p2_score == "AD":
            is_game_point = True

    if is_break_point:
        pressure_level = "break_point"
    elif is_game_point:
        pressure_level = "game_point"
    elif p1_score == "40" and p2_score == "40":
        pressure_level = "deuce"
    else:
        pressure_level = "normal"

    return {
        "is_break_point": is_break_point,
        "is_game_point": is_game_point,
        "pressure_level": pressure_level,
    }


def compute_serve_stats(points_df):
    """Compute serve direction distribution from a points DataFrame."""
    total = len(points_df)
    if total == 0:
        return {"n": 0}
    dir_counts = points_df["serve_direction"].value_counts()
    stats = {
        "n": total,
        "wide_pct": dir_counts.get("wide", 0) / total,
        "body_pct": dir_counts.get("body", 0) / total,
        "t_pct": dir_counts.get("T", 0) / total,
    }
    # Entropy
    probs = np.array([stats["wide_pct"], stats["body_pct"], stats["t_pct"]])
    probs = probs[probs > 0]
    stats["entropy"] = -np.sum(probs * np.log2(probs)) if len(probs) > 1 else 0.0
    return stats


def compute_rally_stats(points_df):
    """Compute rally length and aggression stats."""
    total = len(points_df)
    if total == 0:
        return {"n": 0}
    rl = points_df["rally_length"]
    outcomes = points_df["point_outcome"].value_counts()
    winners = outcomes.get("winner", 0) + outcomes.get("ace", 0)
    uf_errors = outcomes.get("unforced_error", 0)
    agg_denom = winners + uf_errors
    return {
        "n": total,
        "avg_rally_length": rl.mean(),
        "short_rally_pct": (rl < 4).mean(),
        "long_rally_pct": (rl > 8).mean(),
        "aggression_index": winners / agg_denom if agg_denom > 0 else 0.5,
    }


def compute_matchup_stats(points, player_a, player_b):
    """
    Full matchup analysis between two players.
    Returns dict with each player's serving/returning stats in this matchup.
    """
    # Filter to H2H matches
    h2h = points[
        ((points["Player 1"] == player_a) & (points["Player 2"] == player_b))
        | ((points["Player 1"] == player_b) & (points["Player 2"] == player_a))
    ].copy()

    n_matches = h2h["match_id"].nunique()
    n_points = len(h2h)

    if n_points == 0:
        return {"error": f"No charted H2H matches between {player_a} and {player_b}"}

    # Determine who is serving on each point
    # Svr=1 means Player 1 serves, Svr=2 means Player 2 serves
    # We need to map to player_a / player_b regardless of P1/P2 assignment
    h2h["server"] = np.where(
        h2h["Svr"] == 1, h2h["Player 1"], h2h["Player 2"]
    )
    h2h["returner"] = np.where(
        h2h["Svr"] == 1, h2h["Player 2"], h2h["Player 1"]
    )

    # Who won each point
    h2h["point_winner"] = np.where(
        h2h["PtWinner"] == 1, h2h["Player 1"], h2h["Player 2"]
    )

    # Classify pressure for each point
    pressure_data = h2h.apply(
        lambda r: classify_pressure(r["Pts"], r["Svr"]), axis=1, result_type="expand"
    )
    h2h = pd.concat([h2h, pressure_data], axis=1)

    # Match list with dates and results
    match_list = []
    for mid, mdf in h2h.groupby("match_id"):
        row0 = mdf.iloc[0]
        pts_won_a = (mdf["point_winner"] == player_a).sum()
        pts_won_b = (mdf["point_winner"] == player_b).sum()
        match_list.append({
            "date": row0["Date"],
            "tournament": row0["Tournament"],
            "surface": row0["Surface"],
            "round": row0["Round"],
            "points": len(mdf),
            f"{player_a}_pts": pts_won_a,
            f"{player_b}_pts": pts_won_b,
        })
    match_list.sort(key=lambda x: x["date"], reverse=True)

    results = {
        "player_a": player_a,
        "player_b": player_b,
        "n_charted_matches": n_matches,
        "n_points": n_points,
        "matches": match_list,
    }

    # Per-player serving stats
    for player, opponent in [(player_a, player_b), (player_b, player_a)]:
        serving = h2h[h2h["server"] == player]
        returning = h2h[h2h["returner"] == player]
        bp_serving = serving[serving["is_break_point"]]
        bp_returning = returning[returning["is_break_point"]]

        key = player.split()[-1].lower()  # last name as key

        # Overall serve stats in matchup
        results[f"{key}_serve"] = compute_serve_stats(serving)

        # Serve under break point pressure
        results[f"{key}_serve_bp"] = compute_serve_stats(bp_serving)

        # Rally stats when serving
        results[f"{key}_rally_serving"] = compute_rally_stats(serving)

        # Rally stats when returning
        results[f"{key}_rally_returning"] = compute_rally_stats(returning)

        # Win rates
        serve_pts = len(serving)
        return_pts = len(returning)
        results[f"{key}_serve_pts_won"] = (
            (serving["point_winner"] == player).sum() / serve_pts
            if serve_pts > 0 else 0
        )
        results[f"{key}_return_pts_won"] = (
            (returning["point_winner"] == player).sum() / return_pts
            if return_pts > 0 else 0
        )

        # Break point conversion/save
        bp_faced = len(bp_serving)
        bp_saved = (bp_serving["point_winner"] == player).sum() if bp_faced > 0 else 0
        results[f"{key}_bp_faced"] = bp_faced
        results[f"{key}_bp_saved_pct"] = bp_saved / bp_faced if bp_faced > 0 else 0

        bp_chances = len(bp_returning)
        bp_converted = (
            (bp_returning["point_winner"] == player).sum() if bp_chances > 0 else 0
        )
        results[f"{key}_bp_chances"] = bp_chances
        results[f"{key}_bp_converted_pct"] = (
            bp_converted / bp_chances if bp_chances > 0 else 0
        )

    return results


def load_global_profile(profiles_df, player_name):
    """Load a player's global profile for comparison."""
    row = profiles_df[profiles_df["player"] == player_name]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def format_pct(val, digits=1):
    return f"{val * 100:.{digits}f}%"


def print_report(stats, profiles_df):
    """Print formatted matchup report."""
    if "error" in stats:
        print(stats["error"])
        return

    pa = stats["player_a"]
    pb = stats["player_b"]
    ka = pa.split()[-1].lower()
    kb = pb.split()[-1].lower()

    prof_a = load_global_profile(profiles_df, pa)
    prof_b = load_global_profile(profiles_df, pb)

    print("=" * 72)
    print(f"  TENNISIQ MATCHUP ANALYSIS: {pa} vs {pb}")
    print(f"  {stats['n_charted_matches']} charted matches | {stats['n_points']} total points")
    print("=" * 72)

    # Match history
    print(f"\n--- CHARTED MATCH HISTORY ---")
    for m in stats["matches"]:
        print(
            f"  {m['date']}  {m['tournament']:30s} {m['round']:5s}  "
            f"{m['surface']:6s}  {m[f'{pa}_pts']}-{m[f'{pb}_pts']} pts"
        )

    # For each player
    for player, key, profile, opp_key in [
        (pa, ka, prof_a, kb),
        (pb, kb, prof_b, ka),
    ]:
        print(f"\n{'=' * 72}")
        print(f"  {player.upper()} — SERVING IN THIS MATCHUP")
        print(f"{'=' * 72}")

        sv = stats[f"{key}_serve"]
        sv_bp = stats[f"{key}_serve_bp"]
        rally_s = stats[f"{key}_rally_serving"]

        if sv["n"] == 0:
            print("  No serve data.")
            continue

        # Serve direction: matchup vs global
        print(f"\n  SERVE DIRECTION ({sv['n']} serve points)")
        print(f"  {'':20s} {'Matchup':>10s}  {'Global':>10s}  {'Delta':>10s}")
        if profile:
            for label, mk, gk in [
                ("Wide", "wide_pct", "serve_wide_pct"),
                ("Body", "body_pct", "serve_body_pct"),
                ("T", "t_pct", "serve_t_pct"),
            ]:
                mv = sv[mk]
                gv = profile[gk]
                delta = mv - gv
                sign = "+" if delta >= 0 else ""
                print(
                    f"  {label:20s} {format_pct(mv):>10s}  "
                    f"{format_pct(gv):>10s}  {sign}{format_pct(delta):>9s}"
                )
            print(
                f"  {'Entropy':20s} {sv['entropy']:>10.3f}  "
                f"{profile['serve_dir_entropy']:>10.3f}"
            )
        else:
            for label, mk in [("Wide", "wide_pct"), ("Body", "body_pct"), ("T", "t_pct")]:
                print(f"  {label:20s} {format_pct(sv[mk]):>10s}")

        # Break point serve direction
        if sv_bp["n"] >= 5:
            print(f"\n  SERVE UNDER BREAK POINT ({sv_bp['n']} points)")
            print(f"  {'':20s} {'At BP':>10s}  {'Overall':>10s}  {'Delta':>10s}")
            for label, mk in [("Wide", "wide_pct"), ("Body", "body_pct"), ("T", "t_pct")]:
                bpv = sv_bp[mk]
                ov = sv[mk]
                delta = bpv - ov
                sign = "+" if delta >= 0 else ""
                print(
                    f"  {label:20s} {format_pct(bpv):>10s}  "
                    f"{format_pct(ov):>10s}  {sign}{format_pct(delta):>9s}"
                )
        elif sv_bp["n"] > 0:
            print(f"\n  SERVE UNDER BREAK POINT ({sv_bp['n']} points — too few for reliable split)")

        # Rally and aggression
        print(f"\n  RALLY PROFILE (serving)")
        print(f"  Avg rally length:   {rally_s['avg_rally_length']:.1f} shots")
        print(f"  Short rally (<4):   {format_pct(rally_s['short_rally_pct'])}")
        print(f"  Long rally (>8):    {format_pct(rally_s['long_rally_pct'])}")
        print(f"  Aggression index:   {rally_s['aggression_index']:.3f}", end="")
        if profile:
            print(f"  (global: {profile['aggression_index']:.3f})")
        else:
            print()

        # Win rates
        print(f"\n  POINT WIN RATES")
        print(f"  Serve pts won:      {format_pct(stats[f'{key}_serve_pts_won'])}")
        print(f"  Return pts won:     {format_pct(stats[f'{key}_return_pts_won'])}")
        print(
            f"  BP saved:           {stats[f'{key}_bp_saved_pct']:.0%} "
            f"({int(stats[f'{key}_bp_saved_pct'] * stats[f'{key}_bp_faced'])}"
            f"/{stats[f'{key}_bp_faced']})"
        )
        print(
            f"  BP converted:       {stats[f'{key}_bp_converted_pct']:.0%} "
            f"({int(stats[f'{key}_bp_converted_pct'] * stats[f'{key}_bp_chances'])}"
            f"/{stats[f'{key}_bp_chances']})"
        )

    print(f"\n{'=' * 72}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/matchup_analysis.py 'Player A' 'Player B'")
        print("\nTop 20 most-charted players:")
        profiles = pd.read_parquet(PLAYER_PROFILES)
        top = profiles.nlargest(20, "n_charted_matches")[["player", "n_charted_matches"]]
        for _, row in top.iterrows():
            print(f"  {row['player']:30s} ({int(row['n_charted_matches'])} matches)")
        sys.exit(0)

    player_a_input = sys.argv[1]
    player_b_input = sys.argv[2]

    print("Loading data...")
    points = pd.read_parquet(PARSED_POINTS)
    profiles = pd.read_parquet(PLAYER_PROFILES)

    all_players = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))

    player_a = fuzzy_find_player(player_a_input, all_players)
    player_b = fuzzy_find_player(player_b_input, all_players)
    print(f"Matched: {player_a} vs {player_b}")

    stats = compute_matchup_stats(points, player_a, player_b)
    print_report(stats, profiles)


if __name__ == "__main__":
    main()
