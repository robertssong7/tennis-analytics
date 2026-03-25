"""
TennisIQ Pattern API — parquet-backed endpoints for matchup,
score-state, player profiles, and court speed.
No DB dependency. Import into main.py.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/api/v2", tags=["patterns"])

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PARSED_POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
PLAYER_PROFILES = REPO_ROOT / "data" / "processed" / "player_profiles.parquet"
COURT_SPEED = REPO_ROOT / "data" / "court_speed.csv"

# Cache dataframes in memory on first load
_cache = {}

def get_points():
    if "points" not in _cache:
        if not PARSED_POINTS.exists():
            raise HTTPException(503, "Charted data not available (parsed_points.parquet missing)")
        _cache["points"] = pd.read_parquet(PARSED_POINTS)
    return _cache["points"]

def get_profiles():
    if "profiles" not in _cache:
        if not PLAYER_PROFILES.exists():
            raise HTTPException(503, "Player profiles not available")
        _cache["profiles"] = pd.read_parquet(PLAYER_PROFILES)
    return _cache["profiles"]

def get_court_speed():
    if "court_speed" not in _cache:
        _cache["court_speed"] = pd.read_csv(COURT_SPEED)
    return _cache["court_speed"]

def fuzzy_match(name: str, known: list) -> Optional[str]:
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

def serve_dir_stats(pts):
    n = len(pts)
    if n == 0:
        return {"n": 0, "wide": 0, "body": 0, "T": 0, "entropy": 0}
    vc = pts["serve_direction"].value_counts()
    w, b, t = vc.get("wide", 0)/n, vc.get("body", 0)/n, vc.get("T", 0)/n
    probs = np.array([x for x in [w, b, t] if x > 0])
    ent = -np.sum(probs * np.log2(probs)) if len(probs) > 1 else 0.0
    return {"n": n, "wide": round(w, 4), "body": round(b, 4), "T": round(t, 4), "entropy": round(ent, 3)}

def outcome_stats(pts):
    n = len(pts)
    if n == 0:
        return {"n": 0}
    oc = pts["point_outcome"].value_counts()
    winners = oc.get("winner", 0) + oc.get("ace", 0)
    ue = oc.get("unforced_error", 0)
    fe = oc.get("forced_error", 0)
    denom = winners + ue
    return {
        "n": n,
        "winners": int(winners),
        "unforced_errors": int(ue),
        "forced_errors": int(fe),
        "aggression": round(winners / denom, 4) if denom > 10 else None,
        "avg_rally": round(pts["rally_length"].mean(), 2),
        "short_rally_pct": round((pts["rally_length"] < 4).mean(), 4),
        "long_rally_pct": round((pts["rally_length"] > 8).mean(), 4),
    }


@router.get("/players")
def list_players(min_matches: int = Query(5)):
    profiles = get_profiles()
    top = profiles[profiles["n_charted_matches"] >= min_matches].sort_values(
        "n_charted_matches", ascending=False
    )
    return [{
        "name": r["player"],
        "charted_matches": int(r["n_charted_matches"]),
        "aggression_index": round(r["aggression_index"], 3),
        "serve_wide_pct": round(r["serve_wide_pct"], 3),
        "serve_body_pct": round(r["serve_body_pct"], 3),
        "serve_t_pct": round(r["serve_t_pct"], 3),
        "ace_rate": round(r["ace_rate"], 4),
    } for _, r in top.iterrows()]


@router.get("/player/{name}")
def player_profile(name: str):
    profiles = get_profiles()
    points = get_points()
    all_names = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))
    matched = fuzzy_match(name, all_names)
    if not matched:
        raise HTTPException(404, f"Player not found: {name}")

    prof_row = profiles[profiles["player"] == matched]
    prof = prof_row.iloc[0].to_dict() if not prof_row.empty else {}

    # Get all points involving this player
    mask = (points["Player 1"] == matched) | (points["Player 2"] == matched)
    player_pts = points[mask]
    n_matches = player_pts["match_id"].nunique()

    # Server/returner perspective
    server_mask = ((player_pts["Svr"] == 1) & (player_pts["Player 1"] == matched)) | \
                  ((player_pts["Svr"] == 2) & (player_pts["Player 2"] == matched))
    serving = player_pts[server_mask]
    returning = player_pts[~server_mask]

    # Point winner
    player_pts_won = player_pts[
        ((player_pts["PtWinner"] == 1) & (player_pts["Player 1"] == matched)) |
        ((player_pts["PtWinner"] == 2) & (player_pts["Player 2"] == matched))
    ]

    # Break points faced (serving)
    bp_faced = _classify_break_points(serving, matched)
    bp_won = bp_faced[
        ((bp_faced["PtWinner"] == 1) & (bp_faced["Player 1"] == matched)) |
        ((bp_faced["PtWinner"] == 2) & (bp_faced["Player 2"] == matched))
    ]

    # Break points on return
    bp_return = _classify_break_points(returning, matched, as_returner=True)
    bp_converted = bp_return[
        ((bp_return["PtWinner"] == 1) & (bp_return["Player 1"] == matched)) |
        ((bp_return["PtWinner"] == 2) & (bp_return["Player 2"] == matched))
    ]

    serve_stats = serve_dir_stats(serving)
    rally_serving = outcome_stats(serving)
    rally_returning = outcome_stats(returning)

    return {
        "name": matched,
        "charted_matches": n_matches,
        "total_points": len(player_pts),
        "profile": {k: round(v, 4) if isinstance(v, float) else v for k, v in prof.items()},
        "serve_direction": serve_stats,
        "serving": {
            **rally_serving,
            "points_won_pct": round(len(bp_won.index.union(
                serving[serving.index.isin(player_pts_won.index)].index
            )) / len(serving), 4) if len(serving) > 0 else 0,
        },
        "returning": rally_returning,
        "break_points": {
            "faced": len(bp_faced),
            "saved_pct": round(len(bp_won) / len(bp_faced), 4) if len(bp_faced) > 0 else 0,
            "return_chances": len(bp_return),
            "converted_pct": round(len(bp_converted) / len(bp_return), 4) if len(bp_return) > 0 else 0,
        },
        "surfaces": _surface_breakdown(player_pts, matched),
    }


def _classify_break_points(pts, player, as_returner=False):
    """Filter to break point situations."""
    score = pts["Pts"].astype(str)
    svr = pts["Svr"]
    if not as_returner:
        # Player is serving, BP = returner at 40+
        bp = pts[
            ((svr == 1) & (pts["Player 1"] == player) & (
                (score.str.endswith("-40") & score.str.split("-").str[0].isin(["0","15","30"])) |
                (score == "40-AD")
            )) |
            ((svr == 2) & (pts["Player 2"] == player) & (
                (score.str.startswith("40-") & score.str.split("-").str[1].isin(["0","15","30"])) |
                (score == "AD-40")
            ))
        ]
    else:
        # Player is returning, BP = player at 40+
        bp = pts[
            ((svr == 1) & (pts["Player 2"] == player) & (
                (score.str.endswith("-40") & score.str.split("-").str[0].isin(["0","15","30"])) |
                (score == "40-AD")
            )) |
            ((svr == 2) & (pts["Player 1"] == player) & (
                (score.str.startswith("40-") & score.str.split("-").str[1].isin(["0","15","30"])) |
                (score == "AD-40")
            ))
        ]
    return bp


def _surface_breakdown(pts, player):
    """Stats by surface."""
    result = {}
    for surface in pts["Surface"].dropna().unique():
        surf_pts = pts[pts["Surface"] == surface]
        n_matches = surf_pts["match_id"].nunique()
        if n_matches < 2:
            continue
        server_mask = ((surf_pts["Svr"] == 1) & (surf_pts["Player 1"] == player)) | \
                      ((surf_pts["Svr"] == 2) & (surf_pts["Player 2"] == player))
        serving = surf_pts[server_mask]
        result[surface] = {
            "matches": n_matches,
            "points": len(surf_pts),
            "serve_direction": serve_dir_stats(serving),
            "rally": outcome_stats(serving),
        }
    return result


@router.get("/matchup")
def matchup_analysis(
    p1: str = Query(..., description="Player 1 name"),
    p2: str = Query(..., description="Player 2 name"),
):
    points = get_points()
    profiles = get_profiles()
    all_names = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))

    m1 = fuzzy_match(p1, all_names)
    m2 = fuzzy_match(p2, all_names)
    if not m1:
        raise HTTPException(404, f"Player not found: {p1}")
    if not m2:
        raise HTTPException(404, f"Player not found: {p2}")

    # H2H points
    h2h = points[
        ((points["Player 1"] == m1) & (points["Player 2"] == m2)) |
        ((points["Player 1"] == m2) & (points["Player 2"] == m1))
    ]
    if len(h2h) == 0:
        raise HTTPException(404, f"No charted H2H matches between {m1} and {m2}")

    h2h = h2h.copy()
    h2h["server"] = np.where(h2h["Svr"] == 1, h2h["Player 1"], h2h["Player 2"])
    h2h["point_winner"] = np.where(h2h["PtWinner"] == 1, h2h["Player 1"], h2h["Player 2"])

    # Match list
    matches = []
    for mid, mdf in h2h.groupby("match_id"):
        r = mdf.iloc[0]
        matches.append({
            "date": r["Date"], "tournament": r["Tournament"],
            "surface": r["Surface"], "round": r["Round"],
            "points": len(mdf),
            "p1_pts": int((mdf["point_winner"] == m1).sum()),
            "p2_pts": int((mdf["point_winner"] == m2).sum()),
        })
    matches.sort(key=lambda x: x["date"], reverse=True)

    # Per-player stats
    players_data = {}
    for player in [m1, m2]:
        serving = h2h[h2h["server"] == player]
        returning = h2h[h2h["server"] != player]
        prof_row = profiles[profiles["player"] == player]
        global_prof = prof_row.iloc[0].to_dict() if not prof_row.empty else {}

        # BP classification
        bp_serving = _classify_break_points(serving, player)
        bp_saved = bp_serving[
            ((bp_serving["PtWinner"] == 1) & (bp_serving["Player 1"] == player)) |
            ((bp_serving["PtWinner"] == 2) & (bp_serving["Player 2"] == player))
        ]

        players_data[player] = {
            "name": player,
            "global_profile": {k: round(v, 4) if isinstance(v, float) else v for k, v in global_prof.items()},
            "matchup_serve": serve_dir_stats(serving),
            "matchup_rally": outcome_stats(serving),
            "serve_pts_won": round((serving["point_winner"] == player).mean(), 4) if len(serving) > 0 else 0,
            "return_pts_won": round((returning["point_winner"] == player).mean(), 4) if len(returning) > 0 else 0,
            "bp_faced": len(bp_serving),
            "bp_saved_pct": round(len(bp_saved) / len(bp_serving), 4) if len(bp_serving) > 0 else 0,
        }

    return {
        "p1": m1, "p2": m2,
        "charted_matches": h2h["match_id"].nunique(),
        "total_points": len(h2h),
        "matches": matches,
        "players": players_data,
    }


@router.get("/court-speed")
def court_speed(
    tournament: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
):
    cpi = get_court_speed()
    if tournament:
        cpi = cpi[cpi["tournament"].str.contains(tournament, case=False, na=False)]
    if year:
        cpi = cpi[cpi["year"] == year]
    cpi = cpi[cpi["cpi"] > 0]
    import json as _json
    return _json.loads(cpi.to_json(orient="records"))


@router.get("/search")
def search_players(q: str = Query(..., min_length=2)):
    profiles = get_profiles()
    matches = profiles[profiles["player"].str.contains(q, case=False, na=False)]
    matches = matches.nlargest(10, "n_charted_matches")
    return [{"name": r["player"], "matches": int(r["n_charted_matches"])} for _, r in matches.iterrows()]


@router.get("/player/{name}/deep")
def player_deep_analysis(name: str):
    """Full deep analysis: win/loss pattern splits across all dimensions."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from deep_player_analysis import full_analysis, fuzzy_find

    points = get_points()
    all_names = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))
    matched = fuzzy_find(name, all_names)
    if not matched:
        raise HTTPException(404, f"Player not found: {name}")

    result = full_analysis(points, matched)
    # Convert numpy types to native Python for JSON serialization
    import json
    def convert(obj):
        import numpy as np
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, dict): return {k: convert(v) for k, v in obj.items()}
        if isinstance(obj, list): return [convert(i) for i in obj]
        if isinstance(obj, bool): return bool(obj)
        return obj
    return convert(result)
