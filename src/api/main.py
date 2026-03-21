"""
TennisIQ — FastAPI Server
src/api/main.py

All endpoints specified in Phase 6 + Phase 8 (agent loop).

Run:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()
logger = logging.getLogger(__name__)

from src.api.pattern_endpoints import router as pattern_router

app = FastAPI(title="TennisIQ API", version="1.0.0")
app.include_router(pattern_router)

from src.api.config import CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# ML Prediction Engine (Phase 8)
# Loaded once at startup from pkl files — no DB dependency.
# ─────────────────────────────────────────────────────────────

_predict_engine_loaded = False

def _get_engine():
    """Return the singleton PredictEngine, loading it on first call."""
    global _predict_engine_loaded
    from src.api.predict_engine import PredictEngine
    engine = PredictEngine.get()
    if not _predict_engine_loaded:
        try:
            engine.load()
            _predict_engine_loaded = True
        except Exception as e:
            logger.error(f"PredictEngine failed to load: {e}")
            raise HTTPException(503, f"ML models not available: {e}")
    return engine


_HEADSHOTS: dict = {}

def _get_headshots() -> dict:
    global _HEADSHOTS
    if not _HEADSHOTS:
        p = Path(__file__).parent.parent.parent / 'data' / 'player_headshots.json'
        if p.exists():
            import json as _json
            _HEADSHOTS = _json.loads(p.read_text())
    return _HEADSHOTS


_pattern_cache: dict = {}
_conditions_cache: dict = {}
_matchup_grid: dict = {}
_tournament_predictions: dict = {}

def _get_matchup_grid() -> dict:
    global _matchup_grid
    if not _matchup_grid:
        p = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'matchup_grid.json'
        if p.exists():
            import json as _json
            _matchup_grid = _json.loads(p.read_text())
    return _matchup_grid

def _get_tournament_predictions() -> dict:
    global _tournament_predictions
    if not _tournament_predictions:
        p = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'tournament_predictions.json'
        if p.exists():
            import json as _json
            _tournament_predictions = _json.loads(p.read_text())
    return _tournament_predictions


# ─────────────────────────────────────────────────────────────
# Database connection
# ─────────────────────────────────────────────────────────────

def get_conn():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(500, "DATABASE_URL not configured")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


# ─────────────────────────────────────────────────────────────
# Player lookup helper
# ─────────────────────────────────────────────────────────────

def find_player(conn, name: str) -> Optional[dict]:
    with conn.cursor() as cur:
        # Exact match
        cur.execute("""
            SELECT player_id, name, elo_overall, elo_hard, elo_clay, elo_grass,
                   elo_display, fifa_rating, card_tier, elo_peak, elo_peak_date,
                   elo_match_count, country, hand, height_cm
            FROM players
            WHERE LOWER(name) = LOWER(%s)
        """, (name,))
        row = cur.fetchone()
        if row:
            return dict(row)

        # Partial match
        cur.execute("""
            SELECT player_id, name, elo_overall, elo_hard, elo_clay, elo_grass,
                   elo_display, fifa_rating, card_tier, elo_peak, elo_peak_date,
                   elo_match_count, country, hand, height_cm
            FROM players
            WHERE LOWER(name) LIKE LOWER(%s)
            ORDER BY elo_display DESC
            LIMIT 1
        """, (f"%{name}%",))
        row = cur.fetchone()
        return dict(row) if row else None


def get_profile(conn, player_id: int, surface: str = "hard") -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT * FROM player_profiles
            WHERE player_id = %s AND surface = %s
        """, (player_id, surface))
        row = cur.fetchone()
        return dict(row) if row else None


def get_card_attributes(conn, player_id: int, surface: str = "hard") -> dict:
    """Get card attributes, falling back to 50 for any missing values."""
    profile = get_profile(conn, player_id, surface)
    if profile:
        return {
            "srv": int(profile.get("attr_srv") or 50),
            "ret": int(profile.get("attr_ret") or 50),
            "pat": int(profile.get("attr_pat") or 50),
            "spd": int(profile.get("attr_spd") or 50),
            "hrd": int(profile.get("attr_hrd") or 50),
            "cly": int(profile.get("attr_cly") or 50),
        }
    return {"srv": 50, "ret": 50, "pat": 50, "spd": 50, "hrd": 50, "cly": 50}


def get_h2h(conn, p1_id: int, p2_id: int, limit: int = 20) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.match_id, m.match_date, m.surface, m.round,
                   t.name AS tournament, m.score,
                   m.winner_id, m.loser_id
            FROM matches m
            LEFT JOIN tournaments t ON m.tournament_id = t.tournament_id
            WHERE (m.winner_id = %s AND m.loser_id = %s)
               OR (m.winner_id = %s AND m.loser_id = %s)
            ORDER BY m.match_date DESC
            LIMIT %s
        """, (p1_id, p2_id, p2_id, p1_id, limit))
        rows = [dict(r) for r in cur.fetchall()]

    p1_wins = sum(1 for r in rows if r["winner_id"] == p1_id)
    p2_wins = len(rows) - p1_wins

    return {
        "p1_wins":  p1_wins,
        "p2_wins":  p2_wins,
        "total":    len(rows),
        "matches":  rows,
    }


# ─────────────────────────────────────────────────────────────
# Win probability prediction
# ─────────────────────────────────────────────────────────────

def predict_win_prob(p1: dict, p2: dict, surface: str) -> dict:
    """Simple Elo-based win probability with CI."""
    from src.elo.elo_engine import expected_score

    surf_key = f"elo_{surface.lower()}" if surface in ("hard", "clay", "grass") else "elo_display"
    r1 = float(p1.get(surf_key) or p1.get("elo_display") or 1500)
    r2 = float(p2.get(surf_key) or p2.get("elo_display") or 1500)

    prob = expected_score(r1, r2)

    # Simple uncertainty: wider CI when Elo close
    elo_diff = abs(r1 - r2)
    ci_half  = max(0.03, 0.15 - elo_diff / 1000)

    return {
        "p1_win_prob":  round(prob, 3),
        "p2_win_prob":  round(1 - prob, 3),
        "ci_lower":     round(max(0, prob - ci_half), 3),
        "ci_upper":     round(min(1, prob + ci_half), 3),
        "method":       "elo",
    }


def get_top_factors(p1: dict, p2: dict, surface: str) -> list:
    """Return top 3 factors driving the prediction."""
    factors = []
    r1_display = float(p1.get("elo_display") or 1500)
    r2_display = float(p2.get("elo_display") or 1500)
    r1_surf = float(p1.get(f"elo_{surface}", r1_display) or r1_display)
    r2_surf = float(p2.get(f"elo_{surface}", r2_display) or r2_display)

    favored = p1["name"] if r1_display > r2_display else p2["name"]
    surf_fav = p1["name"] if r1_surf > r2_surf else p2["name"]

    factors.append({
        "factor":  "Overall Elo Rating",
        "favors":  favored,
        "delta":   round(abs(r1_display - r2_display), 0),
        "icon":    "📊"
    })
    factors.append({
        "factor":  f"{surface.title()} Court Elo",
        "favors":  surf_fav,
        "delta":   round(abs(r1_surf - r2_surf), 0),
        "icon":    "🎾"
    })
    if p1.get("elo_match_count") and p2.get("elo_match_count"):
        exp_fav = p1["name"] if p1["elo_match_count"] > p2["elo_match_count"] else p2["name"]
        factors.append({
            "factor":  "Match Experience",
            "favors":  exp_fav,
            "delta":   abs((p1.get("elo_match_count") or 0) - (p2.get("elo_match_count") or 0)),
            "icon":    "📈"
        })
    return factors[:3]


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ─────────────────────────────────────────────────────────────
# Phase 8: ML prediction endpoints (pkl-based, no DB required)
# ─────────────────────────────────────────────────────────────

class PredictRequest(BaseModel):
    player1: str
    player2: str
    surface: str = "hard"


@app.post("/predict")
def predict_matchup(req: PredictRequest):
    """
    Predict win probabilities using the stacked ensemble (XGB + LGB).

    Body: {"player1": "Sinner", "player2": "Alcaraz", "surface": "hard"}

    Returns:
        player1_win_prob, player2_win_prob, confidence (high/medium/low),
        confidence_reason, model name, elo_diff, individual model probs.
    """
    engine = _get_engine()

    try:
        result = engine.predict(req.player1, req.player2, req.surface)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(
                404,
                detail={
                    "error": msg,
                    "hint": "Try the full name (e.g. 'Jannik Sinner') or search /players/search?q=...",
                },
            )
        raise HTTPException(400, detail=str(e))

    return result


@app.get("/predict/player/{name}")
def predict_player_card(
    name: str,
    surface: str = Query("hard", description="hard | clay | grass"),
):
    """
    Return FIFA card data for a player.
    Includes overall rating, tier, 8 attributes, surface ratings, form modifier.
    """
    engine = _get_engine()

    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, detail=f"Player not found: {name!r}")

    card = engine.get_player_card(canonical, surface)
    headshots = _get_headshots()
    card['headshot_url'] = headshots.get(canonical)
    return card


@app.get("/matchup")
def matchup(
    p1: str = Query(..., description="Player 1 name"),
    p2: str = Query(..., description="Player 2 name"),
    surface: str = Query("hard", description="hard | clay | grass"),
):
    conn = get_conn()
    try:
        player1 = find_player(conn, p1)
        player2 = find_player(conn, p2)

        if not player1:
            raise HTTPException(404, f"Player not found: {p1}")
        if not player2:
            raise HTTPException(404, f"Player not found: {p2}")

        win_prob = predict_win_prob(player1, player2, surface)
        factors  = get_top_factors(player1, player2, surface)
        h2h      = get_h2h(conn, player1["player_id"], player2["player_id"])
        p1_card  = get_card_attributes(conn, player1["player_id"], surface)
        p2_card  = get_card_attributes(conn, player2["player_id"], surface)

        return {
            "p1": {
                **player1,
                "card_attributes": p1_card,
            },
            "p2": {
                **player2,
                "card_attributes": p2_card,
            },
            "win_probability":    win_prob,
            "confidence_interval": {
                "lower": win_prob["ci_lower"],
                "upper": win_prob["ci_upper"],
            },
            "top_3_factors":      factors,
            "head_to_head_record": h2h,
            "elo_comparison": {
                "p1_overall":  player1.get("elo_display"),
                "p2_overall":  player2.get("elo_display"),
                f"p1_{surface}": player1.get(f"elo_{surface}"),
                f"p2_{surface}": player2.get(f"elo_{surface}"),
            },
            "surface": surface,
        }
    finally:
        conn.close()


@app.get("/player/{name}")
def player_profile(
    name: str,
    surface: str = Query("hard"),
):
    conn = get_conn()
    try:
        player = find_player(conn, name)
        if not player:
            raise HTTPException(404, f"Player not found: {name}")

        pid  = player["player_id"]
        profile = get_profile(conn, pid, surface)
        attrs   = get_card_attributes(conn, pid, surface)

        # Elo history (last 50)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT match_date, surface, elo_before, elo_after,
                       opponent_elo, tournament_level, k_factor
                FROM elo_history
                WHERE player_id = %s
                ORDER BY match_date DESC
                LIMIT 50
            """, (str(pid),))
            history = [dict(r) for r in cur.fetchall()]

        return {
            **player,
            "surface":         surface,
            "profile":         profile,
            "card_attributes": attrs,
            "elo_history":     history,
            "data_confidence": profile.get("data_confidence") if profile else "excluded",
            "match_count":     profile.get("match_count") if profile else player.get("elo_match_count", 0),
        }
    finally:
        conn.close()


@app.get("/patterns/{name}")
def player_patterns(
    name: str,
    surface: str = Query("hard"),
    min_n: int = Query(15),
):
    conn = get_conn()
    try:
        player = find_player(conn, name)
        if not player:
            raise HTTPException(404, f"Player not found: {name}")

        pid = player["player_id"]
        profile = get_profile(conn, pid, surface)
        if not profile:
            return {"player": player, "surface": surface, "patterns": None,
                    "data_confidence": "excluded"}

        # Serve direction
        serve_dirs = {
            "wide": profile.get("serve_wide_pct"),
            "body": profile.get("serve_body_pct"),
            "T":    profile.get("serve_t_pct"),
        }

        return {
            "player":          player,
            "surface":         surface,
            "data_confidence": profile.get("data_confidence"),
            "match_count":     profile.get("match_count"),
            "serve_directions": serve_dirs,
            "serve_effectiveness": {
                "ace_rate":         profile.get("ace_rate"),
                "first_serve_pct":  profile.get("first_serve_pct"),
                "first_serve_won":  profile.get("first_serve_won"),
                "second_serve_won": profile.get("second_serve_won"),
            },
            "rally_patterns": {
                "avg_rally_length": profile.get("avg_rally_length"),
                "winner_rate":      profile.get("winner_rate"),
                "uf_error_rate":    profile.get("uf_error_rate"),
            },
            "pressure": {
                "bp_save_pct":    profile.get("bp_save_pct"),
                "bp_convert_pct": profile.get("bp_convert_pct"),
                "clutch_delta":   profile.get("clutch_delta"),
            },
        }
    finally:
        conn.close()


@app.get("/tournament/{name}")
def tournament(name: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.*,
                       COUNT(m.match_id) AS match_count
                FROM tournaments t
                LEFT JOIN matches m ON m.tournament_id = t.tournament_id
                WHERE LOWER(t.name) LIKE LOWER(%s)
                GROUP BY t.tournament_id
                ORDER BY match_count DESC
                LIMIT 1
            """, (f"%{name}%",))
            row = cur.fetchone()

        if not row:
            raise HTTPException(404, f"Tournament not found: {name}")

        return dict(row)
    finally:
        conn.close()


@app.get("/cards")
def cards_gallery(
    tier: Optional[str] = Query(None, description="legendary|gold|silver|bronze"),
    surface: Optional[str] = Query(None),
    sort: str = Query("fifa_rating", description="fifa_rating|elo|name|recent"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    try:
        conditions = ["elo_match_count >= 5", "fifa_rating IS NOT NULL"]
        params = []

        if tier:
            conditions.append("card_tier = %s")
            params.append(tier.lower())

        where_clause = " AND ".join(conditions)
        order_by = {
            "fifa_rating": "fifa_rating DESC NULLS LAST",
            "elo":         "elo_display DESC NULLS LAST",
            "name":        "name ASC",
            "recent":      "elo_last_updated DESC NULLS LAST",
        }.get(sort, "fifa_rating DESC NULLS LAST")

        # Legendary first
        order = f"CASE WHEN card_tier = 'legendary' THEN 0 ELSE 1 END, {order_by}"

        offset = (page - 1) * page_size
        params.extend([page_size, offset])

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT player_id, name, country, fifa_rating, card_tier,
                       elo_display, elo_hard, elo_clay, elo_grass,
                       elo_peak, elo_match_count
                FROM players
                WHERE {where_clause}
                ORDER BY {order}
                LIMIT %s OFFSET %s
            """, params)
            players = [dict(r) for r in cur.fetchall()]

            # Get card attributes for each
            result = []
            for p in players:
                attrs = get_card_attributes(conn, p["player_id"])
                result.append({**p, "card_attributes": attrs})

        return {
            "players":  result,
            "page":     page,
            "page_size": page_size,
            "total":    len(result),
        }
    finally:
        conn.close()


@app.get("/elo/history/{name}")
def elo_history(name: str):
    conn = get_conn()
    try:
        player = find_player(conn, name)
        if not player:
            raise HTTPException(404, f"Player not found: {name}")

        pid = str(player["player_id"])
        with conn.cursor() as cur:
            cur.execute("""
                SELECT match_date, surface, elo_before, elo_after,
                       opponent_elo, tournament_level, k_factor
                FROM elo_history
                WHERE player_id = %s
                ORDER BY match_date ASC
            """, (pid,))
            history = [dict(r) for r in cur.fetchall()]

        return {
            "player":       player,
            "elo_peak":     player.get("elo_peak"),
            "elo_peak_date": player.get("elo_peak_date"),
            "elo_by_surface": {
                "overall": player.get("elo_overall"),
                "hard":    player.get("elo_hard"),
                "clay":    player.get("elo_clay"),
                "grass":   player.get("elo_grass"),
                "display": player.get("elo_display"),
            },
            "history": history,
        }
    finally:
        conn.close()


@app.get("/players/search")
def search_players(q: str = Query(..., min_length=2)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT player_id, name, country, fifa_rating, card_tier,
                       elo_display, elo_match_count
                FROM players
                WHERE LOWER(name) LIKE LOWER(%s)
                   OR %s = ANY(name_variants)
                ORDER BY elo_display DESC NULLS LAST
                LIMIT 10
            """, (f"%{q}%", q))
            results = [dict(r) for r in cur.fetchall()]
        return {"results": results, "query": q}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# New player profile endpoints (ML-based, no DB required)
# ─────────────────────────────────────────────────────────────

@app.get("/player/{name}/patterns")
def player_patterns_new(name: str):
    """
    Returns play pattern data from parsed_points.parquet.
    """
    import pandas as pd
    import numpy as np

    if name in _pattern_cache:
        return _pattern_cache[name]

    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    PARQUET = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'parsed_points.parquet'
    if not PARQUET.exists():
        return {"available": False, "reason": "Charted data file not found"}

    pts = pd.read_parquet(PARQUET)

    # Find canonical name in charted data
    all_names = sorted(set(pts["Player 1"].unique()) | set(pts["Player 2"].unique()))
    # Try exact match first
    charted_name = None
    for n in all_names:
        if n.lower() == canonical.lower():
            charted_name = n
            break
    if charted_name is None:
        # Try last name match
        last = canonical.split()[-1].lower()
        candidates = [n for n in all_names if n.split()[-1].lower() == last]
        if candidates:
            charted_name = candidates[0]
    if charted_name is None:
        # Try contains
        candidates = [n for n in all_names if canonical.lower() in n.lower() or n.lower() in canonical.lower()]
        if candidates:
            charted_name = candidates[0]

    if charted_name is None:
        result = {
            "available": False,
            "player": canonical,
            "reason": "No charted data for this player. Coverage includes ~980 players from the Match Charting Project."
        }
        _pattern_cache[name] = result
        return result

    mask = (pts["Player 1"] == charted_name) | (pts["Player 2"] == charted_name)
    player_pts = pts[mask].copy()

    if len(player_pts) == 0:
        result = {"available": False, "player": canonical, "reason": "No charted data"}
        _pattern_cache[name] = result
        return result

    # Server perspective
    server_mask = ((player_pts["Svr"] == 1) & (player_pts["Player 1"] == charted_name)) | \
                  ((player_pts["Svr"] == 2) & (player_pts["Player 2"] == charted_name))
    serving = player_pts[server_mask]

    # Point winner perspective
    won_mask = ((player_pts["PtWinner"] == 1) & (player_pts["Player 1"] == charted_name)) | \
               ((player_pts["PtWinner"] == 2) & (player_pts["Player 2"] == charted_name))

    # Serve directions
    serve_dir = {}
    if "serve_direction" in serving.columns and len(serving) > 0:
        for direction in ["wide", "body", "T"]:
            dir_pts = serving[serving["serve_direction"] == direction]
            if len(dir_pts) > 0:
                won_dir = ((dir_pts["PtWinner"] == 1) & (dir_pts["Player 1"] == charted_name)) | \
                          ((dir_pts["PtWinner"] == 2) & (dir_pts["Player 2"] == charted_name))
                serve_dir[direction] = {
                    "frequency": round(len(dir_pts) / len(serving), 3),
                    "win_rate": round(won_dir.sum() / len(dir_pts), 3)
                }
            else:
                serve_dir[direction] = {"frequency": 0, "win_rate": 0}

    # Rally length profile
    rally_profile = {}
    rally_buckets = [("1-3", 1, 3), ("4-6", 4, 6), ("7-9", 7, 9), ("10+", 10, 999)]
    if "rally_length" in player_pts.columns:
        for label, lo, hi in rally_buckets:
            bucket_pts = player_pts[(player_pts["rally_length"] >= lo) & (player_pts["rally_length"] <= hi)]
            if len(bucket_pts) > 0:
                won_bucket = ((bucket_pts["PtWinner"] == 1) & (bucket_pts["Player 1"] == charted_name)) | \
                             ((bucket_pts["PtWinner"] == 2) & (bucket_pts["Player 2"] == charted_name))
                rally_profile[label] = {
                    "points": len(bucket_pts),
                    "win_rate": round(won_bucket.sum() / len(bucket_pts), 3)
                }

    # First strike rate (0-4 shots)
    first_strike = 0.0
    if "rally_length" in player_pts.columns:
        short_pts = player_pts[player_pts["rally_length"] <= 4]
        if len(short_pts) > 0:
            won_short = ((short_pts["PtWinner"] == 1) & (short_pts["Player 1"] == charted_name)) | \
                        ((short_pts["PtWinner"] == 2) & (short_pts["Player 2"] == charted_name))
            first_strike = round(won_short.sum() / len(short_pts), 3)

    # Aggression index (winners / (winners + UE))
    aggression_index = None
    if "point_outcome" in player_pts.columns:
        won_pts = player_pts[won_mask]
        winners = len(won_pts[won_pts["point_outcome"].isin(["winner", "ace"])])
        ue_pts = player_pts[~won_mask]
        ue = len(ue_pts[ue_pts["point_outcome"] == "unforced_error"])
        if winners + ue > 0:
            aggression_index = round(winners / (winners + ue), 3)

    # Defensive win rate (rallies 9+)
    defensive_wr = None
    if "rally_length" in player_pts.columns:
        long_pts = player_pts[player_pts["rally_length"] >= 9]
        if len(long_pts) >= 10:
            won_long = ((long_pts["PtWinner"] == 1) & (long_pts["Player 1"] == charted_name)) | \
                       ((long_pts["PtWinner"] == 2) & (long_pts["Player 2"] == charted_name))
            defensive_wr = round(won_long.sum() / len(long_pts), 3)

    n_matches = player_pts["match_id"].nunique() if "match_id" in player_pts.columns else 0

    result = {
        "available": True,
        "player": canonical,
        "charted_name": charted_name,
        "matches_charted": int(n_matches),
        "top_serve_directions": serve_dir,
        "rally_length_profile": rally_profile,
        "first_strike_rate": first_strike,
        "aggression_index": aggression_index,
        "defensive_win_rate": defensive_wr
    }
    _pattern_cache[name] = result
    return result


@app.get("/player/{name}/matchups")
def player_matchups(
    name: str,
    surface: str = Query("hard", description="hard | clay | grass")
):
    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    grid_data = _get_matchup_grid()
    if not grid_data:
        raise HTTPException(503, "Matchup grid not precomputed. Run scripts/precompute_matchups.py")

    grid = grid_data.get('grid', {})
    player_data = grid.get(canonical)

    if not player_data:
        # Player not in top 100
        return {
            "player": canonical,
            "available": False,
            "reason": f"{canonical} is not in the top 100 by Glicko-2 rating"
        }

    surface = surface.lower()
    if surface not in ('hard', 'clay', 'grass'):
        surface = 'hard'

    surf_data = player_data.get(surface, {"toughest": [], "easiest": []})

    # Determine retirement status for each opponent
    # Use max(last_match_date) from dataset, not today's date
    _latest = engine.latest_data_date
    _retire_days = 548  # 18 months

    def _is_retired(opp_name):
        ratings = engine.glicko.ratings.get(opp_name, {})
        r = ratings.get('all')
        if r is None:
            return True
        if r.last_match_date is None:
            return True
        days_since = (_latest - r.last_match_date).days
        return days_since > _retire_days and r.match_count > 20

    def _annotate(items):
        result = []
        for item in items:
            opp = item.get("opponent", "")
            result.append({
                "opponent": opp,
                "player_win_prob": float(item.get("player_win_prob", 0.5)),
                "is_retired": _is_retired(opp),
            })
        return result

    toughest = _annotate(surf_data.get("toughest", []))
    easiest = _annotate(surf_data.get("easiest", []))
    toughest_active = [x for x in toughest if not x["is_retired"]]
    easiest_active = [x for x in easiest if not x["is_retired"]]

    return {
        "player": canonical,
        "surface": surface,
        "available": True,
        "toughest": toughest,
        "easiest": easiest,
        "toughest_active": toughest_active,
        "easiest_active": easiest_active,
        "top100": grid_data.get("top100", [])
    }


@app.get("/player/{name}/conditions")
def player_conditions(name: str):
    """Return best/worst conditions for a player from historical match data."""
    import pandas as pd

    if name in _conditions_cache:
        return _conditions_cache[name]

    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    SACKMANN = Path(__file__).parent.parent.parent / 'data' / 'sackmann' / 'tennis_atp'

    records = []
    for year in range(2010, 2025):
        csv_path = SACKMANN / f'atp_matches_{year}.csv'
        if not csv_path.exists():
            continue
        try:
            cols = ['winner_name', 'loser_name', 'surface', 'tourney_level', 'round', 'tourney_name']
            df = pd.read_csv(csv_path, usecols=cols, low_memory=False)
            # Filter to this player
            player_rows = df[(df['winner_name'] == canonical) | (df['loser_name'] == canonical)].copy()
            player_rows['won'] = player_rows['winner_name'] == canonical
            records.append(player_rows)
        except Exception:
            continue

    if not records:
        result = {"player": canonical, "best": [], "worst": [], "available": False}
        _conditions_cache[name] = result
        return result

    all_matches = pd.concat(records, ignore_index=True)

    if len(all_matches) < 20:
        result = {"player": canonical, "best": [], "worst": [], "available": False, "reason": "Insufficient match data"}
        _conditions_cache[name] = result
        return result

    conditions = []

    def _make_cond(name_str, subset, category):
        wins = int(subset['won'].sum())
        losses = int(len(subset) - wins)
        return {
            "condition": name_str,
            "win_rate": float(round(wins / len(subset), 3)),
            "wins": wins,
            "losses": losses,
            "matches": int(len(subset)),
            "category": category
        }

    # By surface
    for surface in ['Hard', 'Clay', 'Grass']:
        surf_m = all_matches[all_matches['surface'] == surface]
        if len(surf_m) >= 10:
            conditions.append(_make_cond(f"{surface} Court", surf_m, "surface"))

    # By tournament level
    level_defs = [
        ('G', 'Grand Slam'), ('M', 'Masters 1000'), ('A', 'ATP 500'),
        ('D', 'Davis Cup'), ('F', 'ATP Finals'),
    ]
    for level_code, level_name in level_defs:
        level_m = all_matches[all_matches['tourney_level'] == level_code]
        if len(level_m) >= 10:
            conditions.append(_make_cond(level_name, level_m, "tournament_level"))
    # ATP 250: everything not in the above codes
    other_m = all_matches[~all_matches['tourney_level'].isin(['G', 'M', 'A', 'D', 'F'])]
    if len(other_m) >= 10:
        conditions.append(_make_cond('ATP 250', other_m, "tournament_level"))

    # By round group
    round_groups = {
        'Early Rounds': ['R128', 'R64', 'R32'],
        'Round of 16': ['R16'],
        'Quarterfinal': ['QF'],
        'Semifinal': ['SF'],
        'Final': ['F']
    }
    for group_name, rounds in round_groups.items():
        round_m = all_matches[all_matches['round'].isin(rounds)]
        if len(round_m) >= 10:
            conditions.append(_make_cond(group_name, round_m, "round"))

    conditions.sort(key=lambda x: x['win_rate'], reverse=True)

    # Build by_category grouping
    by_category = {}
    for c in conditions:
        cat = c['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(c)

    result = {
        "player": canonical,
        "available": True,
        "best": conditions[:3],
        "worst": conditions[-3:][::-1] if len(conditions) >= 3 else [],
        "by_category": by_category,
    }
    _conditions_cache[name] = result
    return result


@app.get("/tournament/predict")
def tournament_predict(
    name: str = Query(None, description="Tournament name"),
    year: int = Query(None, description="Year")
):
    data = _get_tournament_predictions()
    if not data:
        raise HTTPException(503, "Tournament predictions not precomputed. Run scripts/precompute_tournament.py")
    return data


# ─────────────────────────────────────────────────────────────
# Scenario patterns endpoint (Session 2A, Task 4)
# ─────────────────────────────────────────────────────────────

_scenarios_cache: dict = {}

@app.get("/player/{name}/scenarios")
def player_scenarios(name: str):
    """
    Analyze how a player's behavior differs in specific match scenarios
    vs their baseline, using parsed_points.parquet charted data.
    """
    import pandas as pd
    import numpy as np
    import math as _math

    if name in _scenarios_cache:
        return _scenarios_cache[name]

    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    PARQUET = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'parsed_points.parquet'
    if not PARQUET.exists():
        return {"player": canonical, "available": False, "reason": "Charted data file not found"}

    pts = pd.read_parquet(PARQUET)

    # Find player in charted data
    all_names = sorted(set(pts["Player 1"].unique()) | set(pts["Player 2"].unique()))
    charted_name = None
    for n in all_names:
        if n.lower() == canonical.lower():
            charted_name = n
            break
    if charted_name is None:
        last = canonical.split()[-1].lower()
        candidates = [n for n in all_names if n.split()[-1].lower() == last]
        if candidates:
            charted_name = candidates[0]
    if charted_name is None:
        candidates = [n for n in all_names if canonical.lower() in n.lower() or n.lower() in canonical.lower()]
        if candidates:
            charted_name = candidates[0]

    if charted_name is None:
        result = {"player": canonical, "available": False,
                  "reason": "No charted match data available"}
        _scenarios_cache[name] = result
        return result

    # Filter to this player's points
    mask = (pts["Player 1"] == charted_name) | (pts["Player 2"] == charted_name)
    player_pts = pts[mask].copy()

    if len(player_pts) < 100:
        result = {"player": canonical, "available": False,
                  "reason": "Insufficient charted data"}
        _scenarios_cache[name] = result
        return result

    # Determine player perspective columns
    player_pts["is_server"] = (
        ((player_pts["Svr"] == 1) & (player_pts["Player 1"] == charted_name)) |
        ((player_pts["Svr"] == 2) & (player_pts["Player 2"] == charted_name))
    )
    player_pts["won_point"] = (
        ((player_pts["PtWinner"] == 1) & (player_pts["Player 1"] == charted_name)) |
        ((player_pts["PtWinner"] == 2) & (player_pts["Player 2"] == charted_name))
    )

    serving = player_pts[player_pts["is_server"]]
    scenarios = []

    # Helper: compute serve direction distribution
    def _serve_dir_dist(subset):
        if len(subset) == 0 or "serve_direction" not in subset.columns:
            return {}
        vc = subset["serve_direction"].value_counts()
        n = len(subset)
        return {d: float(round(vc.get(d, 0) / n, 3)) for d in ["wide", "body", "T"]}

    # ── Scenario 1: Break Point Serving (serve direction shift) ──
    # Detect break points: opponent at 40, player serving
    score = player_pts["Pts"].astype(str)
    bp_serving_mask = (
        player_pts["is_server"] &
        (
            ((player_pts["Svr"] == 1) & (
                (score.str.match(r'^(0|15|30)-40$')) |
                (score == "40-AD")
            )) |
            ((player_pts["Svr"] == 2) & (
                (score.str.match(r'^40-(0|15|30)$')) |
                (score == "AD-40")
            ))
        )
    )
    bp_serving = player_pts[bp_serving_mask]

    if len(bp_serving) >= 30 and len(serving) >= 100:
        baseline_dir = _serve_dir_dist(serving)
        scenario_dir = _serve_dir_dist(bp_serving)
        if baseline_dir and scenario_dir:
            # Check if any direction differs by >3%
            max_diff = max(abs(scenario_dir.get(d, 0) - baseline_dir.get(d, 0)) for d in ["wide", "body", "T"])
            if max_diff > 0.03:
                # Find the direction with biggest change
                biggest_dir = max(["wide", "body", "T"],
                                  key=lambda d: abs(scenario_dir.get(d, 0) - baseline_dir.get(d, 0)))
                base_pct = baseline_dir.get(biggest_dir, 0)
                scen_pct = scenario_dir.get(biggest_dir, 0)
                direction = "more" if scen_pct > base_pct else "less"
                scenarios.append({
                    "scenario": "Break Point Serving",
                    "description": f"Serves {biggest_dir} {int(scen_pct*100)}% on break points vs {int(base_pct*100)}% normally",
                    "baseline": {k: float(v) for k, v in baseline_dir.items()},
                    "scenario_data": {k: float(v) for k, v in scenario_dir.items()},
                    "sample_size": int(len(bp_serving)),
                    "significance": "notable" if max_diff > 0.10 else "moderate",
                })

    # ── Scenario 2: Break Point Returning (win rate shift) ──
    bp_returning_mask = (
        ~player_pts["is_server"] &
        (
            ((player_pts["Svr"] == 2) & (
                (score.str.match(r'^(0|15|30)-40$')) |
                (score == "40-AD")
            )) |
            ((player_pts["Svr"] == 1) & (
                (score.str.match(r'^40-(0|15|30)$')) |
                (score == "AD-40")
            ))
        )
    )
    bp_returning = player_pts[bp_returning_mask]
    returning = player_pts[~player_pts["is_server"]]

    if len(bp_returning) >= 30 and len(returning) >= 100:
        bp_wr = float(bp_returning["won_point"].mean())
        ret_wr = float(returning["won_point"].mean())
        if abs(bp_wr - ret_wr) > 0.03:
            scenarios.append({
                "scenario": "Break Point Returning",
                "description": f"Converts {int(bp_wr*100)}% of break points vs {int(ret_wr*100)}% return points normally",
                "baseline": {"win_rate": float(round(ret_wr, 3))},
                "scenario_data": {"win_rate": float(round(bp_wr, 3))},
                "sample_size": int(len(bp_returning)),
                "significance": "notable" if abs(bp_wr - ret_wr) > 0.10 else "moderate",
            })

    # ── Scenario 3: Tiebreak performance ──
    # Identify tiebreak points by game score 6-6 (TbSet column is unreliable boolean)
    tb_mask = (player_pts["Gm1"] == 6) & (player_pts["Gm2"] == 6)
    tb_pts = player_pts[tb_mask]

    if len(tb_pts) >= 30:
        tb_wr = float(tb_pts["won_point"].mean())
        overall_wr = float(player_pts["won_point"].mean())
        if abs(tb_wr - overall_wr) > 0.03:
            tb_serve_wr = float(tb_pts[tb_pts["is_server"]]["won_point"].mean()) if len(tb_pts[tb_pts["is_server"]]) > 10 else None
            scenarios.append({
                "scenario": "Tiebreak Play",
                "description": f"Wins {int(tb_wr*100)}% of tiebreak points vs {int(overall_wr*100)}% overall",
                "baseline": {"win_rate": float(round(overall_wr, 3))},
                "scenario_data": {
                    "win_rate": float(round(tb_wr, 3)),
                    "serve_win_rate": float(round(tb_serve_wr, 3)) if tb_serve_wr is not None else None,
                },
                "sample_size": int(len(tb_pts)),
                "significance": "notable" if abs(tb_wr - overall_wr) > 0.10 else "moderate",
            })

    # ── Scenario 4: Short rally vs Long rally (win rate by rally length) ──
    if "rally_length" in player_pts.columns:
        short_pts = player_pts[player_pts["rally_length"] <= 3]
        long_pts = player_pts[player_pts["rally_length"] >= 9]

        if len(short_pts) >= 30 and len(long_pts) >= 30:
            short_wr = float(short_pts["won_point"].mean())
            long_wr = float(long_pts["won_point"].mean())
            if abs(short_wr - long_wr) > 0.03:
                style = "short-rally" if short_wr > long_wr else "long-rally"
                scenarios.append({
                    "scenario": "Rally Length Preference",
                    "description": f"Wins {int(short_wr*100)}% of short rallies (1-3) vs {int(long_wr*100)}% of long rallies (9+)",
                    "baseline": {"short_rally_wr": float(round(short_wr, 3)),
                                 "long_rally_wr": float(round(long_wr, 3))},
                    "scenario_data": {"preferred_style": style,
                                      "spread": float(round(abs(short_wr - long_wr), 3))},
                    "sample_size": int(len(short_pts) + len(long_pts)),
                    "significance": "notable" if abs(short_wr - long_wr) > 0.10 else "moderate",
                })

    # ── Scenario 5: First serve vs Second serve performance ──
    first_serve = serving[serving["1st"].notna() & (serving["1st"] != "")]
    # second serve = serving point where 1st missed (2nd column is populated)
    second_serve = serving[serving["2nd"].notna() & (serving["2nd"] != "")]

    if len(first_serve) >= 30 and len(second_serve) >= 30:
        first_wr = float(first_serve["won_point"].mean())
        second_wr = float(second_serve["won_point"].mean())
        drop = first_wr - second_wr
        if drop > 0.03:
            scenarios.append({
                "scenario": "First vs Second Serve",
                "description": f"Wins {int(first_wr*100)}% on 1st serve vs {int(second_wr*100)}% on 2nd serve ({int(drop*100)}pt drop)",
                "baseline": {"first_serve_wr": float(round(first_wr, 3))},
                "scenario_data": {"second_serve_wr": float(round(second_wr, 3)),
                                  "drop": float(round(drop, 3))},
                "sample_size": int(len(second_serve)),
                "significance": "notable" if drop > 0.20 else "moderate",
            })

    # ── Scenario 6: Down a set (Set1 < Set2 or Set2 < Set1 depending on player number) ──
    # Identify when player is down a set
    p1_mask = player_pts["Player 1"] == charted_name
    down_set_mask = (
        (p1_mask & (player_pts["Set1"] < player_pts["Set2"])) |
        (~p1_mask & (player_pts["Set2"] < player_pts["Set1"]))
    )
    down_set_pts = player_pts[down_set_mask]

    if len(down_set_pts) >= 30:
        down_wr = float(down_set_pts["won_point"].mean())
        overall_wr = float(player_pts["won_point"].mean())
        if abs(down_wr - overall_wr) > 0.03:
            scenarios.append({
                "scenario": "Down a Set",
                "description": f"Wins {int(down_wr*100)}% of points when trailing in sets vs {int(overall_wr*100)}% overall",
                "baseline": {"win_rate": float(round(overall_wr, 3))},
                "scenario_data": {"win_rate": float(round(down_wr, 3))},
                "sample_size": int(len(down_set_pts)),
                "significance": "notable" if abs(down_wr - overall_wr) > 0.10 else "moderate",
            })

    n_matches = int(player_pts["match_id"].nunique()) if "match_id" in player_pts.columns else 0

    result = {
        "player": canonical,
        "available": True,
        "matches_analyzed": n_matches,
        "total_points": int(len(player_pts)),
        "scenarios": scenarios,
    }
    _scenarios_cache[name] = result
    return result
