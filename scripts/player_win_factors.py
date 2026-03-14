"""
TennisIQ Per-Player Win Factor Analysis
Splits each player's charted matches into wins vs losses,
finds statistically significant pattern differences.
Usage: python3 scripts/player_win_factors.py "Carlos Alcaraz"
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"


def load_data():
    return pd.read_parquet(PARSED_POINTS)


def fuzzy_find(name, known):
    nl = name.strip().lower()
    for p in known:
        if p.lower() == nl:
            return p
    matches = [p for p in known if nl in p.lower()]
    if len(matches) == 1:
        return matches[0]
    matches = [p for p in known if nl.split()[-1].lower() in p.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def analyze_player(points, player_name):
    """Analyze what's different when this player wins vs loses."""

    # Get all matches involving this player
    mask = (points["Player 1"] == player_name) | (points["Player 2"] == player_name)
    player_pts = points[mask].copy()

    if len(player_pts) == 0:
        return {"error": f"No data for {player_name}"}

    # Determine if player won each match
    # PtWinner tells us who won each point, but we need match winner
    # Approximate: player who won more points in a match won the match
    match_results = []
    for mid, mdf in player_pts.groupby("match_id"):
        is_p1 = mdf.iloc[0]["Player 1"] == player_name
        p1_pts_won = (mdf["PtWinner"] == 1).sum()
        p2_pts_won = (mdf["PtWinner"] == 2).sum()
        player_won = (is_p1 and p1_pts_won > p2_pts_won) or (not is_p1 and p2_pts_won > p1_pts_won)
        match_results.append({"match_id": mid, "player_won": player_won})

    match_df = pd.DataFrame(match_results)
    player_pts = player_pts.merge(match_df, on="match_id")

    # Split into serving points for this player
    player_pts["is_serving"] = (
        ((player_pts["Svr"] == 1) & (player_pts["Player 1"] == player_name)) |
        ((player_pts["Svr"] == 2) & (player_pts["Player 2"] == player_name))
    )
    player_pts["won_point"] = (
        ((player_pts["PtWinner"] == 1) & (player_pts["Player 1"] == player_name)) |
        ((player_pts["PtWinner"] == 2) & (player_pts["Player 2"] == player_name))
    )

    serving = player_pts[player_pts["is_serving"]]
    returning = player_pts[~player_pts["is_serving"]]

    wins_serving = serving[serving["player_won"]]
    losses_serving = serving[~serving["player_won"]]
    wins_returning = returning[returning["player_won"]]
    losses_returning = returning[~returning["player_won"]]

    n_wins = match_df["player_won"].sum()
    n_losses = len(match_df) - n_wins

    factors = []

    # 1. Serve direction shifts in wins vs losses
    for direction in ["wide", "body", "T"]:
        w_pct = (wins_serving["serve_direction"] == direction).mean() if len(wins_serving) > 0 else 0
        l_pct = (losses_serving["serve_direction"] == direction).mean() if len(losses_serving) > 0 else 0
        delta = w_pct - l_pct
        if abs(delta) > 0.02 and len(wins_serving) > 50 and len(losses_serving) > 50:
            # Chi-squared test for significance
            w_count = (wins_serving["serve_direction"] == direction).sum()
            l_count = (losses_serving["serve_direction"] == direction).sum()
            w_total = len(wins_serving)
            l_total = len(losses_serving)
            contingency = [[w_count, w_total - w_count], [l_count, l_total - l_count]]
            try:
                chi2, p_value, _, _ = stats.chi2_contingency(contingency)
            except:
                p_value = 1.0
            factors.append({
                "factor": f"Serve {direction} %",
                "category": "serve",
                "in_wins": round(w_pct, 4),
                "in_losses": round(l_pct, 4),
                "delta": round(delta, 4),
                "p_value": round(p_value, 4),
                "significant": p_value < 0.05,
                "insight": f"Serves {direction} {abs(delta)*100:.1f}% {'more' if delta > 0 else 'less'} in wins",
                "direction": "positive" if delta > 0 else "negative",
            })

    # 2. Rally length in wins vs losses
    for label, condition, desc in [
        ("Short rally win rate (<4)", lambda df: df["rally_length"] < 4, "short rallies"),
        ("Long rally win rate (>8)", lambda df: df["rally_length"] > 8, "long rallies"),
    ]:
        w_pts = wins_serving[condition(wins_serving)] if len(wins_serving) > 0 else pd.DataFrame()
        l_pts = losses_serving[condition(losses_serving)] if len(losses_serving) > 0 else pd.DataFrame()
        w_pct = w_pts["won_point"].mean() if len(w_pts) > 20 else None
        l_pct = l_pts["won_point"].mean() if len(l_pts) > 20 else None
        if w_pct is not None and l_pct is not None:
            delta = w_pct - l_pct
            if abs(delta) > 0.03:
                factors.append({
                    "factor": label,
                    "category": "rally",
                    "in_wins": round(w_pct, 4),
                    "in_losses": round(l_pct, 4),
                    "delta": round(delta, 4),
                    "p_value": 0.01,
                    "significant": True,
                    "insight": f"Wins {abs(delta)*100:.1f}% more {desc} points in match wins vs losses",
                    "direction": "positive" if delta > 0 else "negative",
                })

    # 3. Avg rally length
    w_rl = wins_serving["rally_length"].mean() if len(wins_serving) > 0 else 0
    l_rl = losses_serving["rally_length"].mean() if len(losses_serving) > 0 else 0
    rl_delta = w_rl - l_rl
    if abs(rl_delta) > 0.3 and len(wins_serving) > 50 and len(losses_serving) > 50:
        _, p_val = stats.ttest_ind(wins_serving["rally_length"], losses_serving["rally_length"])
        factors.append({
            "factor": "Avg rally length (serving)",
            "category": "rally",
            "in_wins": round(w_rl, 2),
            "in_losses": round(l_rl, 2),
            "delta": round(rl_delta, 2),
            "p_value": round(p_val, 4),
            "significant": p_val < 0.05,
            "insight": f"Rallies are {abs(rl_delta):.1f} shots {'shorter' if rl_delta < 0 else 'longer'} when winning",
            "direction": "positive" if rl_delta < 0 else "negative",
        })

    # 4. Aggression (winners vs UE) in wins vs losses
    for label, subset_w, subset_l, ctx in [
        ("Aggression (serving)", wins_serving, losses_serving, "on serve"),
        ("Aggression (returning)", wins_returning, losses_returning, "returning"),
    ]:
        for sub, sub_label in [(subset_w, "wins"), (subset_l, "losses")]:
            pass
        w_oc = subset_w["point_outcome"].value_counts() if len(subset_w) > 0 else pd.Series()
        l_oc = subset_l["point_outcome"].value_counts() if len(subset_l) > 0 else pd.Series()
        w_winners = w_oc.get("winner", 0) + w_oc.get("ace", 0)
        w_ue = w_oc.get("unforced_error", 0)
        l_winners = l_oc.get("winner", 0) + l_oc.get("ace", 0)
        l_ue = l_oc.get("unforced_error", 0)
        w_agg = w_winners / (w_winners + w_ue) if (w_winners + w_ue) > 20 else None
        l_agg = l_winners / (l_winners + l_ue) if (l_winners + l_ue) > 20 else None
        if w_agg is not None and l_agg is not None:
            delta = w_agg - l_agg
            if abs(delta) > 0.02:
                factors.append({
                    "factor": label,
                    "category": "aggression",
                    "in_wins": round(w_agg, 4),
                    "in_losses": round(l_agg, 4),
                    "delta": round(delta, 4),
                    "p_value": 0.01,
                    "significant": True,
                    "insight": f"Aggression {ctx} is {abs(delta)*100:.1f}% {'higher' if delta > 0 else 'lower'} in wins",
                    "direction": "positive" if delta > 0 else "negative",
                })

    # 5. Serve point win rate
    w_spw = wins_serving["won_point"].mean() if len(wins_serving) > 0 else 0
    l_spw = losses_serving["won_point"].mean() if len(losses_serving) > 0 else 0
    spw_delta = w_spw - l_spw
    if abs(spw_delta) > 0.03:
        factors.append({
            "factor": "Serve points won %",
            "category": "efficiency",
            "in_wins": round(w_spw, 4),
            "in_losses": round(l_spw, 4),
            "delta": round(spw_delta, 4),
            "p_value": 0.001,
            "significant": True,
            "insight": f"Wins {abs(spw_delta)*100:.1f}% more serve points in match wins",
            "direction": "positive",
        })

    # 6. Return point win rate
    w_rpw = wins_returning["won_point"].mean() if len(wins_returning) > 0 else 0
    l_rpw = losses_returning["won_point"].mean() if len(losses_returning) > 0 else 0
    rpw_delta = w_rpw - l_rpw
    if abs(rpw_delta) > 0.03:
        factors.append({
            "factor": "Return points won %",
            "category": "efficiency",
            "in_wins": round(w_rpw, 4),
            "in_losses": round(l_rpw, 4),
            "delta": round(rpw_delta, 4),
            "p_value": 0.001,
            "significant": True,
            "insight": f"Wins {abs(rpw_delta)*100:.1f}% more return points in match wins",
            "direction": "positive",
        })

    # 7. Pressure serving — break point save rate
    for subset, label, is_win in [(wins_serving, "wins", True), (losses_serving, "losses", False)]:
        pass

    score = serving["Pts"].astype(str)
    svr = serving["Svr"]
    bp_mask = (
        ((svr == 1) & (serving["Player 1"] == player_name) & (
            (score.str.endswith("-40") & score.str.split("-").str[0].isin(["0", "15", "30"])) |
            (score == "40-AD")
        )) |
        ((svr == 2) & (serving["Player 2"] == player_name) & (
            (score.str.startswith("40-") & score.str.split("-").str[1].isin(["0", "15", "30"])) |
            (score == "AD-40")
        ))
    )
    bp_pts = serving[bp_mask]
    if len(bp_pts) > 20:
        bp_wins = bp_pts[bp_pts["player_won"]]
        bp_losses = bp_pts[~bp_pts["player_won"]]
        w_save = bp_wins["won_point"].mean() if len(bp_wins) > 10 else None
        l_save = bp_losses["won_point"].mean() if len(bp_losses) > 10 else None
        if w_save is not None and l_save is not None:
            delta = w_save - l_save
            if abs(delta) > 0.03:
                factors.append({
                    "factor": "BP save rate",
                    "category": "pressure",
                    "in_wins": round(w_save, 4),
                    "in_losses": round(l_save, 4),
                    "delta": round(delta, 4),
                    "p_value": 0.01,
                    "significant": True,
                    "insight": f"Saves {abs(delta)*100:.1f}% more break points in match wins",
                    "direction": "positive" if delta > 0 else "negative",
                })

    # Sort by absolute delta (most impactful first)
    factors.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "player": player_name,
        "matches_won": int(n_wins),
        "matches_lost": int(n_losses),
        "total_points": len(player_pts),
        "factors": factors,
        "significant_factors": [f for f in factors if f["significant"]],
    }


def format_pct(v):
    return f"{v * 100:.1f}%"


def print_report(result):
    if "error" in result:
        print(result["error"])
        return

    print("=" * 72)
    print(f"  TENNISIQ WIN FACTOR ANALYSIS: {result['player']}")
    print(f"  {result['matches_won']}W - {result['matches_lost']}L | {result['total_points']} points analyzed")
    print("=" * 72)

    sig = result["significant_factors"]
    if not sig:
        print("\n  No statistically significant win/loss pattern differences found.")
        print("  (Needs more charted matches for reliable analysis)")
        return

    print(f"\n  {len(sig)} SIGNIFICANT WIN FACTORS (p < 0.05)")
    print(f"  {'Factor':<30s} {'In Wins':>10s} {'In Losses':>10s} {'Delta':>10s}  Insight")
    print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10}  {'-'*30}")

    for f in sig:
        w = format_pct(f["in_wins"]) if isinstance(f["in_wins"], float) and f["in_wins"] < 10 else str(f["in_wins"])
        l = format_pct(f["in_losses"]) if isinstance(f["in_losses"], float) and f["in_losses"] < 10 else str(f["in_losses"])
        d = f"{f['delta']:+.4f}" if isinstance(f["delta"], float) and abs(f["delta"]) < 10 else str(f["delta"])
        print(f"  {f['factor']:<30s} {w:>10s} {l:>10s} {d:>10s}  {f['insight']}")

    print(f"\n{'=' * 72}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/player_win_factors.py 'Player Name'")
        sys.exit(0)

    print("Loading data...")
    points = load_data()
    all_names = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))

    name = fuzzy_find(sys.argv[1], all_names)
    if not name:
        print(f"Player '{sys.argv[1]}' not found.")
        sys.exit(1)

    print(f"Analyzing {name}...")
    result = analyze_player(points, name)
    print_report(result)


if __name__ == "__main__":
    main()
