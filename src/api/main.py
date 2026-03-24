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
from datetime import timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
import requests as req_lib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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


_HEADSHOT_CACHE_DIR = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'headshots'

@app.get("/api/player-image/{code}")
def get_player_image(code: str):
    """Proxy ATP headshot images with disk caching."""
    import re
    # Sanitize code to prevent path traversal
    if not re.match(r'^[a-zA-Z0-9]{2,10}$', code):
        return JSONResponse(status_code=400, content={"error": "Invalid code"})

    _HEADSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _HEADSHOT_CACHE_DIR / f"{code}.png"

    # Serve from disk cache if available
    if cached.exists() and cached.stat().st_size > 500:
        logger.debug(f"Headshot cache HIT: {code}")
        return Response(content=cached.read_bytes(), media_type="image/png",
                       headers={"Cache-Control": "public, max-age=604800"})

    logger.info(f"Headshot cache MISS: {code} — fetching from ATP")
    # Fetch from ATP and save to cache
    url = f"https://www.atptour.com/-/media/alias/player-headshot/{code}"
    try:
        r = req_lib.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            cached.write_bytes(r.content)
            return Response(content=r.content, media_type=r.headers["content-type"],
                          headers={"Cache-Control": "public, max-age=604800"})
    except Exception:
        pass
    return JSONResponse(status_code=404, content={"error": "Image not found"})


_headshot_prefetch_started = False

def _prefetch_headshots_background():
    """Pre-fetch all player headshots to disk in a background thread."""
    import time
    headshots = _get_headshots()
    if not headshots:
        return
    _HEADSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    codes = set()
    for url in headshots.values():
        if url:
            code = url.split('/')[-1]
            if code and len(code) >= 2:
                codes.add(code)
    fetched = 0
    for code in codes:
        cached = _HEADSHOT_CACHE_DIR / f"{code}.png"
        if cached.exists() and cached.stat().st_size > 500:
            continue
        try:
            r = req_lib.get(
                f"https://www.atptour.com/-/media/alias/player-headshot/{code}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10
            )
            if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
                cached.write_bytes(r.content)
                fetched += 1
        except Exception:
            pass
        time.sleep(2)  # Rate limit
    logger.info(f"Headshot pre-fetch complete: {fetched} new images cached")


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

    # Accept "overall" as a surface alias — use "hard" (the most common surface)
    surface = req.surface
    if surface.lower() == "overall":
        surface = "hard"

    try:
        result = engine.predict(req.player1, req.player2, surface)
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

    # Start background headshot pre-fetch on first request
    global _headshot_prefetch_started
    if not _headshot_prefetch_started:
        _headshot_prefetch_started = True
        import threading
        threading.Thread(target=_prefetch_headshots_background, daemon=True).start()

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
def player_patterns_new(
    name: str,
    surface: Optional[str] = Query(None, description="Filter by surface: hard | clay | grass"),
):
    """
    Returns play pattern data from parsed_points.parquet.
    Optional ?surface= parameter filters points by Surface column.
    """
    import pandas as pd
    import numpy as np

    cache_key = f"{name}:{surface}" if surface else name
    if cache_key in _pattern_cache:
        return _pattern_cache[cache_key]

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
        _pattern_cache[cache_key] = result
        return result

    mask = (pts["Player 1"] == charted_name) | (pts["Player 2"] == charted_name)
    player_pts = pts[mask].copy()

    # Surface filtering if requested
    if surface and "Surface" in player_pts.columns:
        _surf_map = {"hard": "Hard", "clay": "Clay", "grass": "Grass"}
        _surf_val = _surf_map.get(surface.lower())
        if _surf_val:
            player_pts = player_pts[player_pts["Surface"] == _surf_val]

    if len(player_pts) == 0:
        result = {"available": False, "player": canonical, "reason": "No charted data" + (f" on {surface}" if surface else "")}
        _pattern_cache[cache_key] = result
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
        "surface": surface if surface else "all",
        "charted_name": charted_name,
        "matches_charted": int(n_matches),
        "top_serve_directions": serve_dir,
        "rally_length_profile": rally_profile,
        "first_strike_rate": first_strike,
        "aggression_index": aggression_index,
        "defensive_win_rate": defensive_wr
    }
    _pattern_cache[cache_key] = result
    return result


_matchups_result_cache: dict = {}

@app.get("/player/{name}/matchups")
def player_matchups(
    name: str,
    surface: str = Query("hard", description="hard | clay | grass")
):
    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    cache_key = f"{canonical}:{surface}"
    if cache_key in _matchups_result_cache:
        return _matchups_result_cache[cache_key]

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

    def _generate_reasons(player_name, opp_name, win_prob, surface):
        """Generate 3 short explanations for why this matchup is tough/easy."""
        reasons = []
        # Get Glicko data for both
        p_ratings = engine.glicko.ratings.get(player_name, {})
        o_ratings = engine.glicko.ratings.get(opp_name, {})
        p_all = p_ratings.get('all')
        o_all = o_ratings.get('all')
        p_surf = p_ratings.get(surface)
        o_surf = o_ratings.get(surface)

        p_mu = p_all.mu if p_all else 1500
        o_mu = o_all.mu if o_all else 1500
        elo_diff = p_mu - o_mu

        # Surface-specific ratings
        p_surf_mu = p_surf.mu if (p_surf and p_surf.match_count >= 10) else p_mu
        o_surf_mu = o_surf.mu if (o_surf and o_surf.match_count >= 10) else o_mu
        surf_diff = p_surf_mu - o_surf_mu

        p_first = player_name.split()[0] if player_name.split() else player_name
        o_first = opp_name.split()[0] if opp_name.split() else opp_name
        o_last = opp_name.split()[-1] if opp_name.split() else opp_name

        # Reason 1: Rating comparison
        if abs(elo_diff) < 50:
            reasons.append(f"Similar overall rating makes this unpredictable")
        elif elo_diff > 0:
            reasons.append(f"{p_first}'s higher rating ({int(p_mu)} vs {int(o_mu)}) gives an edge")
        else:
            reasons.append(f"{o_last}'s superior rating ({int(o_mu)} vs {int(p_mu)}) is a challenge")

        # Reason 2: Surface-specific
        if abs(surf_diff) < 40:
            reasons.append(f"Similar {surface}-court rating makes this a coin flip")
        elif surf_diff > 0:
            reasons.append(f"{p_first}'s {surface}-court rating advantage ({int(p_surf_mu)} vs {int(o_surf_mu)})")
        else:
            reasons.append(f"{o_last}'s {surface}-court strength ({int(o_surf_mu)} vs {int(p_surf_mu)})")

        # Reason 3: attributes / H2H / serve comparison
        p_acc = engine.attributes.get(player_name)
        o_acc = engine.attributes.get(opp_name)
        h2h_key = tuple(sorted([player_name, opp_name]))
        h2h_entry = engine.h2h.get(h2h_key)

        if h2h_entry:
            p_wins = h2h_entry['wins'].get(player_name, 0)
            o_wins = h2h_entry['wins'].get(opp_name, 0)
            total = p_wins + o_wins
            if total >= 2:
                reasons.append(f"H2H record: {p_wins}-{o_wins} in {total} meetings")
            elif p_acc and o_acc:
                # Attribute comparison
                try:
                    p_raw = p_acc.compute_raw_attributes()
                    o_raw = o_acc.compute_raw_attributes()
                    # Find biggest attribute difference
                    diffs = {}
                    for attr in ('serve', 'groundstroke', 'endurance', 'mental', 'clutch'):
                        p_v = int(min(99, max(30, 30 + p_raw.get(attr, 0.5) * 69)))
                        o_v = int(min(99, max(30, 30 + o_raw.get(attr, 0.5) * 69)))
                        diffs[attr] = (p_v - o_v, p_v, o_v)
                    best_attr = max(diffs.items(), key=lambda x: abs(x[1][0]))
                    attr_name = best_attr[0]
                    diff_val, p_v, o_v = best_attr[1]
                    if diff_val > 0:
                        reasons.append(f"{p_first}'s {attr_name} advantage ({p_v} vs {o_v})")
                    else:
                        reasons.append(f"{o_last}'s {attr_name} advantage ({o_v} vs {p_v})")
                except Exception:
                    reasons.append(f"Win probability: {int(win_prob*100)}%")
            else:
                reasons.append(f"Win probability: {int(win_prob*100)}%")
        elif p_acc and o_acc:
            try:
                p_raw = p_acc.compute_raw_attributes()
                o_raw = o_acc.compute_raw_attributes()
                p_serve = int(min(99, max(30, 30 + p_raw.get('serve', 0.5) * 69)))
                o_serve = int(min(99, max(30, 30 + o_raw.get('serve', 0.5) * 69)))
                if abs(p_serve - o_serve) > 5:
                    stronger = p_first if p_serve > o_serve else o_last
                    reasons.append(f"{stronger}'s powerful serve limits break opportunities")
                else:
                    reasons.append(f"Win probability: {int(win_prob*100)}%")
            except Exception:
                reasons.append(f"Win probability: {int(win_prob*100)}%")
        else:
            reasons.append(f"Win probability: {int(win_prob*100)}%")

        return reasons[:3]

    def _annotate(items, player_name):
        result = []
        for item in items:
            opp = item.get("opponent", "")
            wp = float(item.get("player_win_prob", 0.5))
            reasons = _generate_reasons(player_name, opp, wp, surface)
            result.append({
                "opponent": opp,
                "player_win_prob": wp,
                "is_retired": _is_retired(opp),
                "reasons": reasons,
            })
        return result

    toughest = _annotate(surf_data.get("toughest", []), canonical)
    easiest = _annotate(surf_data.get("easiest", []), canonical)
    toughest_active = [x for x in toughest if not x["is_retired"]]
    easiest_active = [x for x in easiest if not x["is_retired"]]

    # Get player's own Elo for proximity filtering
    _player_ratings = engine.glicko.ratings.get(canonical, {})
    _player_all = _player_ratings.get('all')
    _player_elo = _player_all.mu if _player_all else 1500
    _recency_cutoff = timedelta(days=730)  # 2 years

    def _is_quality_opponent(opp_name):
        """Check recency (2yr), tour-level (>=100 matches)."""
        opp_ratings = engine.glicko.ratings.get(opp_name, {})
        opp_all = opp_ratings.get('all')
        if opp_all is None:
            return False
        # Recency: last match within 2 years of LATEST_DATA_DATE
        if opp_all.last_match_date is None:
            return False
        if (_latest - opp_all.last_match_date) > _recency_cutoff:
            return False
        # Tour level: at least 100 matches
        if opp_all.match_count < 100:
            return False
        return True

    def _is_elo_proximate(opp_name, elo_threshold):
        """Check opponent is within elo_threshold of player (for EASIEST)."""
        opp_ratings = engine.glicko.ratings.get(opp_name, {})
        opp_all = opp_ratings.get('all')
        if opp_all is None:
            return False
        return opp_all.mu >= (_player_elo - elo_threshold)

    # Filter precomputed easiest_active by quality + Elo proximity
    # Try 400, then relax to 600, then 800
    _filtered_easiest = []
    for threshold in (400, 600, 800):
        _filtered_easiest = [
            x for x in easiest_active
            if _is_quality_opponent(x['opponent']) and _is_elo_proximate(x['opponent'], threshold)
        ]
        if len(_filtered_easiest) >= 5:
            break
    easiest_active = _filtered_easiest

    # Filter precomputed toughest_active by quality (recency + match count only)
    toughest_active = [x for x in toughest_active if _is_quality_opponent(x['opponent'])]

    # On-the-fly prediction to fill active lists when precomputed grid lacks enough active players
    if len(toughest_active) < 10 or len(easiest_active) < 10:
        already_listed = set(
            [x['opponent'] for x in toughest] + [x['opponent'] for x in easiest]
            + [x['opponent'] for x in toughest_active] + [x['opponent'] for x in easiest_active]
        )
        active_players = []
        for pname, surfaces_dict in engine.glicko.ratings.items():
            r = surfaces_dict.get('all')
            if r and r.last_match_date and not _is_retired(pname):
                if pname != canonical and pname not in already_listed:
                    if _is_quality_opponent(pname):
                        active_players.append((pname, r.mu))

        # For toughest: pick highest-rated active players we haven't included
        if len(toughest_active) < 10:
            active_players.sort(key=lambda x: x[1], reverse=True)
            for opp_name, opp_mu in active_players:
                if len(toughest_active) >= 10:
                    break
                if opp_name in [x['opponent'] for x in toughest_active]:
                    continue
                try:
                    result = engine.predict(canonical, opp_name, surface)
                    wp = round(float(result['player1_win_prob']), 3)
                    item = {"opponent": opp_name, "player_win_prob": wp, "is_retired": False}
                    item['reasons'] = _generate_reasons(canonical, opp_name, wp, surface)
                    toughest_active.append(item)
                except Exception:
                    pass

        # For easiest: pick from active players within Elo proximity
        if len(easiest_active) < 10:
            for threshold in (400, 600, 800):
                elo_filtered = [
                    (n, mu) for n, mu in active_players
                    if mu >= (_player_elo - threshold)
                    and n not in [x['opponent'] for x in easiest_active]
                ]
                elo_filtered.sort(key=lambda x: x[1])  # lowest rated first
                for opp_name, opp_mu in elo_filtered:
                    if len(easiest_active) >= 10:
                        break
                    if opp_name in [x['opponent'] for x in easiest_active]:
                        continue
                    try:
                        result = engine.predict(canonical, opp_name, surface)
                        wp = round(float(result['player1_win_prob']), 3)
                        item = {"opponent": opp_name, "player_win_prob": wp, "is_retired": False}
                        item['reasons'] = _generate_reasons(canonical, opp_name, wp, surface)
                        easiest_active.append(item)
                    except Exception:
                        pass
                if len(easiest_active) >= 10:
                    break

        # Re-sort after backfill
        toughest_active.sort(key=lambda x: x['player_win_prob'])
        easiest_active.sort(key=lambda x: x['player_win_prob'], reverse=True)

    result = {
        "player": canonical,
        "surface": surface,
        "available": True,
        "toughest": toughest,
        "easiest": easiest,
        "toughest_active": toughest_active[:10],
        "easiest_active": easiest_active[:10],
        "top100": grid_data.get("top100", [])
    }
    _matchups_result_cache[cache_key] = result
    return result


_similar_cache: dict = {}

@app.get("/player/{name}/similar")
def player_similar(name: str):
    """Return top 5 most similar players based on attribute profiles."""
    if name in _similar_cache:
        return _similar_cache[name]

    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    card = engine.get_player_card(canonical, 'hard')
    attrs = card.get('attributes', {})
    player_elo = float(card.get('elo', 1500))

    # Compare against all players with charted data
    similar = []
    for other_name in engine.player_names:
        if other_name == canonical:
            continue
        other_ratings = engine.glicko.ratings.get(other_name, {}).get('all')
        if not other_ratings or other_ratings.match_count < 50:
            continue
        # Must be within 300 Elo
        if abs(float(other_ratings.mu) - player_elo) > 300:
            continue

        other_card = engine.get_player_card(other_name, 'hard')
        other_attrs = other_card.get('attributes', {})

        # Compute similarity across available attributes
        diffs = []
        reasons = []
        attr_names = ['serve', 'groundstroke', 'endurance', 'durability', 'clutch', 'mental']
        for a in attr_names:
            v1 = attrs.get(a)
            v2 = other_attrs.get(a)
            if v1 is not None and v2 is not None:
                diff = abs(float(v1) - float(v2)) / 99.0
                diffs.append(diff)
                if diff < 0.08:  # Very similar
                    reasons.append(f"Similar {a} ({int(v1)} vs {int(v2)})")

        if len(diffs) < 3:
            continue

        sim_score = 1.0 - (sum(diffs) / len(diffs))
        similar.append({
            'name': other_name,
            'similarity_score': round(float(sim_score), 3),
            'reasons': reasons[:3] if reasons else [f"Similar overall profile (score: {round(float(sim_score)*100)}%)"]
        })

    similar.sort(key=lambda x: x['similarity_score'], reverse=True)

    result = {
        'player': canonical,
        'similar_players': similar[:5]
    }
    _similar_cache[name] = result
    return result


@app.get("/player/{name}/conditions")
def player_conditions(
    name: str,
    surface: Optional[str] = Query(None, description="Filter by surface: hard | clay | grass"),
):
    """Return conditions for a player in exactly 3 categories: climate, court_speed, ball_type.
    Optional ?surface= parameter filters matches to a specific surface before computing."""
    import pandas as pd

    cache_key = f"{name}:{surface}" if surface else name
    if cache_key in _conditions_cache:
        return _conditions_cache[cache_key]

    engine = _get_engine()
    canonical = engine.find_player(name)
    if canonical is None:
        raise HTTPException(404, f"Player not found: {name!r}")

    SACKMANN = Path(__file__).parent.parent.parent / 'data' / 'sackmann' / 'tennis_atp'
    CPI_CSV = Path(__file__).parent.parent.parent / 'data' / 'court_speed.csv'
    SUPPL_CSV = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'supplemental_matches_2025_2026.csv'

    # Tournament -> climate bucket mapping
    TOURNAMENT_CLIMATE = {
        # Hot & Humid
        'australian open': 'Hot & Humid', 'miami': 'Hot & Humid', 'miami open': 'Hot & Humid',
        'us open': 'Hot & Humid', 'canadian open': 'Hot & Humid', 'cincinnati': 'Hot & Humid',
        'cincinnati masters': 'Hot & Humid',
        'western & southern': 'Hot & Humid',
        # Hot & Dry
        'indian wells': 'Hot & Dry', 'indian wells masters': 'Hot & Dry',
        'bnp paribas open': 'Hot & Dry',
        'madrid': 'Hot & Dry (Altitude)', 'madrid masters': 'Hot & Dry (Altitude)',
        'mutua madrid open': 'Hot & Dry (Altitude)',
        # Mild & Mediterranean
        'monte carlo': 'Mild & Mediterranean', 'monte carlo masters': 'Mild & Mediterranean',
        'rome': 'Warm & Mediterranean', 'rome masters': 'Warm & Mediterranean',
        'internazionali': 'Warm & Mediterranean',
        'roland garros': 'Mild & Temperate', 'french open': 'Mild & Temperate',
        'wimbledon': 'Mild & Temperate',
        # Indoor
        'paris masters': 'Indoor', 'atp finals': 'Indoor', 'tour finals': 'Indoor',
        'nextgen finals': 'Indoor', 'next gen finals': 'Indoor',
        'basel': 'Indoor', 'vienna': 'Indoor', 'rotterdam': 'Indoor',
        'marseille': 'Indoor', 'stockholm': 'Indoor', 'antwerp': 'Indoor',
        'moscow': 'Indoor', 'sofia': 'Indoor', 'metz': 'Indoor',
        'st. petersburg': 'Indoor',
        # Shanghai
        'shanghai': 'Warm & Humid', 'shanghai masters': 'Warm & Humid',
    }

    # Known indoor tournaments (lowercase for matching)
    INDOOR_TOURNAMENTS = {
        'paris masters', 'atp finals', 'tour finals', 'basel', 'vienna',
        'st. petersburg', 'marseille', 'rotterdam', 'sofia', 'metz',
        'stockholm', 'antwerp', 'moscow', 'nextgen finals', 'next gen finals',
    }

    records = []
    for year in range(2010, 2025):
        csv_path = SACKMANN / f'atp_matches_{year}.csv'
        if not csv_path.exists():
            continue
        try:
            cols = ['winner_name', 'loser_name', 'surface', 'tourney_level', 'round', 'tourney_name', 'tourney_date']
            df = pd.read_csv(csv_path, usecols=cols, low_memory=False)
            player_rows = df[(df['winner_name'] == canonical) | (df['loser_name'] == canonical)].copy()
            player_rows['won'] = player_rows['winner_name'] == canonical
            player_rows['_court'] = None  # no court column in Sackmann
            records.append(player_rows)
        except Exception:
            continue

    # Also load supplemental matches with name mapping
    if SUPPL_CSV.exists():
        try:
            suppl_name_map = getattr(engine, '_supplemental_name_map', None)
            if suppl_name_map is None:
                from src.api.predict_engine import _build_supplemental_name_map
                suppl_name_map = _build_supplemental_name_map(engine.player_names)

            sup_df = pd.read_csv(SUPPL_CSV)
            sup_df = sup_df.dropna(subset=['winner_name', 'loser_name'])
            # Map names
            sup_df['_w_mapped'] = sup_df['winner_name'].map(suppl_name_map)
            sup_df['_l_mapped'] = sup_df['loser_name'].map(suppl_name_map)
            # Keep only rows where both players mapped
            sup_mapped = sup_df[sup_df['_w_mapped'].notna() & sup_df['_l_mapped'].notna()].copy()
            # Filter to this player
            player_sup = sup_mapped[
                (sup_mapped['_w_mapped'] == canonical) | (sup_mapped['_l_mapped'] == canonical)
            ].copy()
            if len(player_sup) > 0:
                player_sup['winner_name'] = player_sup['_w_mapped']
                player_sup['loser_name'] = player_sup['_l_mapped']
                player_sup['won'] = player_sup['winner_name'] == canonical
                player_sup['_court'] = player_sup.get('court', None)
                # Rename tourney_name if needed
                for col in ['tourney_name', 'tourney_date', 'surface']:
                    if col not in player_sup.columns:
                        player_sup[col] = None
                records.append(player_sup[['winner_name', 'loser_name', 'surface', 'tourney_name', 'tourney_date', 'won', '_court']])
        except Exception:
            pass

    if not records:
        result = {"player": canonical, "available": False,
                  "by_category": {"climate": [], "court_speed": [], "ball_type": []},
                  "missing_categories": ["climate", "court_speed", "ball_type"]}
        _conditions_cache[cache_key] = result
        return result

    all_matches = pd.concat(records, ignore_index=True)

    # Apply surface filter if requested
    if surface:
        surface_norm = surface.strip().capitalize()
        all_matches = all_matches[all_matches['surface'].str.capitalize() == surface_norm]

    if len(all_matches) < 20:
        result = {"player": canonical, "available": False,
                  "by_category": {"climate": [], "court_speed": [], "ball_type": []},
                  "missing_categories": ["climate", "court_speed", "ball_type"],
                  "reason": "Insufficient match data" + (f" on {surface}" if surface else "")}
        _conditions_cache[cache_key] = result
        return result

    conditions = []
    missing_categories = []

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

    # Build CPI map for court speed + ball type from court_speed.csv
    _cpi_map = {}   # (tourney_name_lower, year) -> {cpi, ball_type, indoor}
    if CPI_CSV.exists():
        try:
            cpi_df = pd.read_csv(CPI_CSV)
            _name_map = {
                'ATP Finals': 'ATP Finals', 'Australian Open': 'Australian Open',
                'Canadian Open': 'Canadian Open', 'Cincinnati': 'Cincinnati Masters',
                'Indian Wells': 'Indian Wells Masters', 'Madrid': 'Madrid Masters',
                'Miami': 'Miami Masters', 'Monte Carlo': 'Monte Carlo Masters',
                'Paris': 'Paris Masters', 'Roland Garros': 'Roland Garros',
                'Rome': 'Rome Masters', 'Shanghai': 'Shanghai Masters',
                'US Open': 'Us Open', 'Wimbledon': 'Wimbledon',
            }
            for _, row in cpi_df.iterrows():
                sack_name = _name_map.get(row['tournament'], row['tournament'])
                yr = int(row['year']) if pd.notna(row.get('year')) else 0
                cpi_val = float(row['cpi']) if pd.notna(row.get('cpi')) and row['cpi'] > 0 else None
                ball = str(row.get('ball_type', '')).strip().rstrip('*') if pd.notna(row.get('ball_type')) else None
                indoor = 'Indoor' in str(row.get('surface', ''))
                _cpi_map[(sack_name.lower(), yr)] = {'cpi': cpi_val, 'ball_type': ball, 'indoor': indoor}
        except Exception:
            pass

    # Determine indoor/outdoor and climate bucket for each match
    all_matches['_tname_lower'] = all_matches['tourney_name'].astype(str).str.lower()
    all_matches['_year'] = pd.to_numeric(
        all_matches['tourney_date'].astype(str).str[:4], errors='coerce'
    ).fillna(0).astype(int)

    def _get_climate_bucket(row):
        tname = str(row['_tname_lower'])
        # Check supplemental court column (Indoor/Outdoor)
        court_val = row.get('_court')
        # Direct tournament name lookup
        for key_t, bucket in TOURNAMENT_CLIMATE.items():
            if key_t in tname:
                return bucket
        # If court column says Indoor (from supplemental data)
        if pd.notna(court_val) and str(court_val).strip().lower() == 'indoor':
            return 'Indoor'
        # Check CPI data for indoor flag
        cpi_key = (tname, row['_year'])
        cpi_info = _cpi_map.get(cpi_key, {})
        if cpi_info.get('indoor'):
            return 'Indoor'
        # Check known indoor set
        if any(indoor_t in tname for indoor_t in INDOOR_TOURNAMENTS):
            return 'Indoor'
        return None

    all_matches['_climate'] = all_matches.apply(_get_climate_bucket, axis=1)

    # ── Climate: aggregated weather buckets ──
    has_climate = False
    climate_labeled = all_matches[all_matches['_climate'].notna()]
    if len(climate_labeled) >= 10:
        for bucket_name in sorted(climate_labeled['_climate'].unique()):
            bucket = climate_labeled[climate_labeled['_climate'] == bucket_name]
            if len(bucket) >= 5:
                conditions.append(_make_cond(bucket_name, bucket, "climate"))
                has_climate = True
    if not has_climate:
        missing_categories.append("climate")

    # ── Court Speed (CPI buckets) ──
    if _cpi_map:
        def _get_cpi(row):
            key = (row['_tname_lower'], row['_year'])
            return _cpi_map.get(key, {}).get('cpi')

        all_matches['_cpi'] = all_matches.apply(_get_cpi, axis=1)

        cpi_matches = all_matches[all_matches['_cpi'].notna()]
        has_court_speed = False
        if len(cpi_matches) >= 10:
            for label, lo, hi in [('Slow (CPI < 30)', 0, 30), ('Medium (CPI 30-40)', 30, 40), ('Fast (CPI > 40)', 40, 100)]:
                bucket = cpi_matches[(cpi_matches['_cpi'] >= lo) & (cpi_matches['_cpi'] < hi)]
                if len(bucket) >= 10:
                    conditions.append(_make_cond(label, bucket, "court_speed"))
                    has_court_speed = True

        if not has_court_speed:
            for surf, label in [('Clay', 'Slow (est.)'), ('Hard', 'Medium (est.)'), ('Grass', 'Fast (est.)')]:
                bucket = all_matches[all_matches['surface'] == surf]
                if len(bucket) >= 10:
                    conditions.append(_make_cond(label, bucket, "court_speed"))
                    has_court_speed = True

        if not has_court_speed:
            missing_categories.append("court_speed")

        # ── Ball Type (keep Penn and Head as DISTINCT entries) ──
        def _get_ball(row):
            key = (row['_tname_lower'], row['_year'])
            raw_ball = _cpi_map.get(key, {}).get('ball_type')
            if raw_ball:
                return str(raw_ball).replace('*', '').strip()
            return None

        all_matches['_ball'] = all_matches.apply(_get_ball, axis=1)

        ball_matches = all_matches[all_matches['_ball'].notna() & (all_matches['_ball'] != '')]
        has_ball = False
        if len(ball_matches) >= 10:
            # Split combined ball types like "Penn/Head" into separate entries
            expanded_rows = []
            for idx, row in ball_matches.iterrows():
                ball_val = row['_ball']
                if '/' in ball_val:
                    for sub_ball in ball_val.split('/'):
                        sub_ball = sub_ball.strip()
                        if sub_ball:
                            new_row = row.copy()
                            new_row['_ball'] = sub_ball
                            expanded_rows.append(new_row)
                else:
                    expanded_rows.append(row)
            if expanded_rows:
                ball_expanded = pd.DataFrame(expanded_rows)
                for ball_name in sorted(ball_expanded['_ball'].unique()):
                    bucket = ball_expanded[ball_expanded['_ball'] == ball_name]
                    if len(bucket) >= 10:
                        conditions.append(_make_cond(ball_name, bucket, "ball_type"))
                        has_ball = True

        if not has_ball:
            missing_categories.append("ball_type")

        all_matches.drop(columns=['_year', '_tname_lower', '_cpi', '_ball', '_climate', '_court'], errors='ignore', inplace=True)
    else:
        has_court_speed = False
        for surf, label in [('Clay', 'Slow (est.)'), ('Hard', 'Medium (est.)'), ('Grass', 'Fast (est.)')]:
            bucket = all_matches[all_matches['surface'] == surf]
            if len(bucket) >= 10:
                conditions.append(_make_cond(label, bucket, "court_speed"))
                has_court_speed = True
        if not has_court_speed:
            missing_categories.append("court_speed")
        missing_categories.append("ball_type")
        all_matches.drop(columns=['_year', '_tname_lower', '_climate', '_court'], errors='ignore', inplace=True)

    # Filter out conditions with fewer than 10 matches
    conditions = [c for c in conditions if c['matches'] >= 10]

    # Sort and assign display_mode per category
    by_category = {"climate": [], "court_speed": [], "ball_type": []}
    for c in conditions:
        cat = c['category']
        if cat in by_category:
            by_category[cat].append(c)

    # Sort all categories by win_rate descending
    by_category["climate"].sort(key=lambda x: x['win_rate'], reverse=True)
    by_category["court_speed"].sort(key=lambda x: x['win_rate'], reverse=True)
    by_category["ball_type"].sort(key=lambda x: x['win_rate'], reverse=True)

    # Add display_mode to each category
    _display_modes = {"climate": "best_worst", "court_speed": "ranked", "ball_type": "ranked"}
    for cat_name, items in by_category.items():
        for item in items:
            item["display_mode"] = _display_modes[cat_name]

    result = {
        "player": canonical,
        "available": True,
        "surface_filter": surface if surface else None,
        "by_category": by_category,
        "missing_categories": missing_categories,
    }
    _conditions_cache[cache_key] = result
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
# Live tournament endpoint
# ─────────────────────────────────────────────────────────────

@app.get("/api/live-tournament")
def live_tournament():
    """Return finished and current tournament data."""
    live_path = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'live_tournament.json'
    suppl_csv = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'supplemental_matches_2025_2026.csv'

    # Load finished tournament (BNP Paribas Open)
    finished_data = {}
    if live_path.exists():
        try:
            finished_data = json.loads(live_path.read_text())
        except Exception:
            pass

    # Check supplemental CSV for Miami Open 2026 data
    miami_results = []
    if suppl_csv.exists():
        try:
            import pandas as pd
            sup_df = pd.read_csv(suppl_csv)
            miami_rows = sup_df[sup_df['tourney_name'].str.contains('Miami', case=False, na=False)]
            # Check if any are 2026 data (tourney_date >= 20260101)
            miami_2026 = miami_rows[miami_rows['tourney_date'].astype(int) >= 20260101]
            if len(miami_2026) == 0:
                # Fall back to most recent Miami data
                miami_2026 = miami_rows
            for _, row in miami_2026.iterrows():
                miami_results.append({
                    "winner": str(row.get('winner_name', '')),
                    "loser": str(row.get('loser_name', '')),
                    "score": str(row.get('score', '')),
                    "round": str(row.get('round', '')),
                })
        except Exception:
            pass

    current_data = {
        "tournament": "Miami Open",
        "year": 2026,
        "dates": "Mar 19-30, 2026",
        "location": "Miami, FL",
        "surface": "Hard",
        "level": "Masters 1000",
        "status": "In Progress",
        "data_available": len(miami_results) > 0,
        "results": miami_results[-20:] if miami_results else [],
    }

    return {
        "finished": finished_data,
        "current": current_data,
    }


# ─────────────────────────────────────────────────────────────
# Tournament predictions endpoint
# ─────────────────────────────────────────────────────────────

_tournament_pred_cache: dict = {}

@app.get("/api/tournament-predictions")
def tournament_predictions():
    """
    Return favorites and dark horses for the current tournament (Miami Open 2026, Hard).
    Computed from Glicko-2 hard-court ratings, filtered to players in the draw.
    """
    if _tournament_pred_cache:
        return _tournament_pred_cache

    import math as _math
    import csv as _csv

    engine = _get_engine()
    _latest = engine.latest_data_date
    _retire_days = 548

    # ── Load draw from supplemental CSV ──────────────────────────
    _suppl_path = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'supplemental_matches_2025_2026.csv'
    draw_canonical = set()
    draw_available = False
    if _suppl_path.exists():
        # Use the engine's supplemental name map (abbrev -> canonical)
        _name_map = getattr(engine, '_supplemental_name_map', {})
        if not _name_map:
            # Engine may not have built it yet; build on demand
            from src.api.predict_engine import _build_supplemental_name_map
            _name_map = _build_supplemental_name_map(engine.player_names)
        try:
            import pandas as _pd
            _suppl_df = _pd.read_csv(_suppl_path)
            _miami = _suppl_df[_suppl_df['tourney_name'].str.contains('Miami', case=False, na=False)]
            if len(_miami) > 0:
                draw_available = True
                for _col in ['winner_name', 'loser_name']:
                    for _abbrev in _miami[_col].dropna().unique():
                        _canon = _name_map.get(str(_abbrev).strip())
                        if _canon:
                            draw_canonical.add(_canon)
        except Exception as _e:
            logger.warning(f"Could not load draw from supplemental CSV: {_e}")

    # Collect active players with hard court ratings
    active_hard = []
    for pname, surfaces_dict in engine.glicko.ratings.items():
        r_all = surfaces_dict.get('all')
        if r_all is None or r_all.last_match_date is None:
            continue
        days_since = (_latest - r_all.last_match_date).days
        if days_since > _retire_days and r_all.match_count > 20:
            continue  # retired
        r_hard = surfaces_dict.get('hard')
        if r_hard and r_hard.match_count >= 10:
            hard_mu = r_hard.mu
        else:
            hard_mu = r_all.mu
        active_hard.append((pname, hard_mu, r_all.mu))

    active_hard.sort(key=lambda x: x[1], reverse=True)

    # If draw is available, filter to draw players only
    if draw_available and draw_canonical:
        active_hard_in_draw = [(n, h, o) for n, h, o in active_hard if n in draw_canonical]
    else:
        active_hard_in_draw = active_hard

    # Top 20 for normalization (from draw-filtered list)
    top20 = active_hard_in_draw[:20]
    total_exp = sum(_math.exp(mu / 100) for _, mu, _ in top20)

    # Favorites: top 5
    favorites = []
    for name, hard_mu, overall_mu in top20[:5]:
        win_prob = round(_math.exp(hard_mu / 100) / total_exp, 3)
        form_data = engine.player_form.get(name, {})
        form_3 = form_data.get('form_3', 0.5)
        surf_form = form_data.get('surface_form_hard', 0.5)

        reasons = []
        reasons.append(f"Hard court rating: {int(hard_mu)}")
        if surf_form > 0.65:
            reasons.append(f"Strong recent hard court form ({int(surf_form*100)}% win rate)")
        elif surf_form < 0.45:
            reasons.append(f"Struggling on hard courts recently ({int(surf_form*100)}% win rate)")
        else:
            reasons.append(f"Solid hard court form ({int(surf_form*100)}% win rate)")
        if form_3 >= 0.67:
            reasons.append("Hot streak: won recent matches")
        elif form_3 <= 0.33:
            reasons.append("Cold streak: lost recent matches")
        else:
            reasons.append(f"Overall rating: {int(overall_mu)}")

        favorites.append({
            "player": name,
            "hard_rating": round(hard_mu, 1),
            "overall_rating": round(overall_mu, 1),
            "win_prob": win_prob,
            "reasons": reasons,
        })

    # Dark horses: players ranked 15-30 by hard court rating (draw-filtered) with strong recent form
    # Build field average attributes for comparison
    _field_attr_avgs = {}
    _field_attr_counts = {}
    for _fn, _fhmu, _fomu in active_hard_in_draw[:30]:
        _facc = engine.attributes.get(_fn)
        if _facc:
            try:
                _fraw = _facc.compute_raw_attributes()
                for _fa_name, _fa_val in _fraw.items():
                    _mapped = int(min(99, max(30, 30 + _fa_val * 69)))
                    if _mapped > 35:
                        _field_attr_avgs[_fa_name] = _field_attr_avgs.get(_fa_name, 0) + _mapped
                        _field_attr_counts[_fa_name] = _field_attr_counts.get(_fa_name, 0) + 1
            except Exception:
                pass
    for _fa_name in _field_attr_avgs:
        if _field_attr_counts.get(_fa_name, 0) > 0:
            _field_attr_avgs[_fa_name] = round(float(_field_attr_avgs[_fa_name]) / _field_attr_counts[_fa_name], 1)

    # Determine surface rating rank cutoffs for projected_round
    _surf_rank_lookup = {n: i + 1 for i, (n, _, _) in enumerate(active_hard_in_draw)}

    dark_horses = []
    for name, hard_mu, overall_mu in active_hard_in_draw[14:30]:
        form_data = engine.player_form.get(name, {})
        form_3 = form_data.get('form_3', 0.5)
        form_5 = form_data.get('form_5', 0.5)
        surf_form = form_data.get('surface_form_hard', 0.5)
        # Require strong recent form
        if form_5 >= 0.6 or surf_form >= 0.6:
            # Generate detailed reasons
            reasons = []

            # 1. Serve attribute vs field average
            _dh_acc = engine.attributes.get(name)
            _dh_serve = None
            _dh_endurance = None
            _dh_clutch = None
            if _dh_acc:
                try:
                    _dh_raw = _dh_acc.compute_raw_attributes()
                    _dh_serve = int(min(99, max(30, 30 + _dh_raw.get('serve', 0.5) * 69)))
                    _dh_endurance = int(min(99, max(30, 30 + _dh_raw.get('endurance', 0.5) * 69)))
                    _dh_clutch = int(min(99, max(30, 30 + _dh_raw.get('clutch', 0.5) * 69)))
                except Exception:
                    pass

            if _dh_serve is not None and _dh_serve > 35:
                _f_avg_serve = _field_attr_avgs.get('serve', 60)
                if _dh_serve > _f_avg_serve + 3:
                    reasons.append(f"Serve ({_dh_serve}) above field average ({int(_f_avg_serve)})")
                elif _dh_serve >= _f_avg_serve - 3:
                    reasons.append(f"Serve ({_dh_serve}) matches field average ({int(_f_avg_serve)})")

            # 2. Surface-specific rating vs overall rating
            surf_boost = hard_mu - overall_mu
            if surf_boost > 20:
                reasons.append(f"Hard court specialist (+{int(surf_boost)} rating vs overall)")
            elif surf_boost > 0:
                reasons.append(f"Slightly better on hard courts (+{int(surf_boost)} vs overall)")

            # 3. Recent form
            if float(form_3) >= 0.67:
                reasons.append(f"Hot streak: {int(float(form_3)*100)}% win rate in last 3 matches")
            elif float(form_5) >= 0.6:
                reasons.append(f"Good recent form: {int(float(form_5)*100)}% in last 5 matches")

            # 4. Endurance/clutch attributes
            if _dh_endurance is not None and _dh_endurance > 70:
                reasons.append(f"High endurance ({_dh_endurance}) for deep tournament runs")
            if _dh_clutch is not None and _dh_clutch > 70:
                reasons.append(f"Clutch performer ({_dh_clutch}) in tight moments")

            # Surface form as fallback
            if float(surf_form) >= 0.65:
                reasons.append(f"Strong hard court form ({int(float(surf_form)*100)}% win rate)")

            reasons.append(f"Hard court rating: {int(hard_mu)}")

            # Ensure exactly 3 reasons (trim or pad)
            reasons = reasons[:3]
            _generic_reasons = [
                f"Consistent performer at Masters 1000 level",
                f"Experienced on outdoor hard courts",
                f"Dangerous floater in the draw",
            ]
            _gi = 0
            while len(reasons) < 3 and _gi < len(_generic_reasons):
                if _generic_reasons[_gi] not in reasons:
                    reasons.append(_generic_reasons[_gi])
                _gi += 1

            # Projected round based on surface rating rank
            _rank = _surf_rank_lookup.get(name, 99)
            if _rank <= 15:
                projected_round = "Semifinal"
            elif _rank <= 25:
                projected_round = "Quarterfinal"
            else:
                projected_round = "Round of 16"

            # Build reason_summary
            _summary_parts = []
            if float(form_5) >= 0.8:
                _summary_parts.append("excellent form")
            elif float(form_5) >= 0.6:
                _summary_parts.append("strong form")
            if surf_boost > 20:
                _summary_parts.append("hard court specialist")
            elif float(surf_form) >= 0.65:
                _summary_parts.append("hard court strength")
            if _dh_clutch is not None and _dh_clutch > 70:
                _summary_parts.append("clutch ability")
            elif _dh_serve is not None and _dh_serve > _field_attr_avgs.get('serve', 60):
                _summary_parts.append("big serve")
            reason_summary = f"Dark horse due to {', '.join(_summary_parts[:2]) if _summary_parts else 'overall profile'}"

            dark_horses.append({
                "player": name,
                "hard_rating": round(float(hard_mu), 1),
                "overall_rating": round(float(overall_mu), 1),
                "reasons": reasons,
                "projected_round": projected_round,
                "reason_summary": reason_summary,
            })
    dark_horses = dark_horses[:5]

    result = {
        "tournament": "Miami Open",
        "year": 2026,
        "surface": "Hard",
        "draw_available": draw_available,
        "draw_size": len(draw_canonical) if draw_available else None,
        "favorites": favorites,
        "dark_horses": dark_horses,
    }
    _tournament_pred_cache.update(result)
    return result


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
    returning = player_pts[~player_pts["is_server"]]
    overall_wr = float(player_pts["won_point"].mean())
    p1_mask = player_pts["Player 1"] == charted_name
    scenarios = []

    def _sig(diff):
        d = abs(diff)
        if d > 0.10: return "notable"
        if d > 0.05: return "moderate"
        return "minor"

    # Helper: compute serve direction distribution
    def _serve_dir_dist(subset):
        if len(subset) == 0 or "serve_direction" not in subset.columns:
            return {}
        vc = subset["serve_direction"].value_counts()
        n = len(subset)
        return {d: float(round(vc.get(d, 0) / n, 3)) for d in ["wide", "body", "T"]}

    # ── Break point detection helpers ──
    score = player_pts["Pts"].astype(str)
    bp_serving_mask = (
        player_pts["is_server"] &
        (
            ((player_pts["Svr"] == 1) & (
                (score.str.match(r'^(0|15|30)-40$')) | (score == "40-AD")
            )) |
            ((player_pts["Svr"] == 2) & (
                (score.str.match(r'^40-(0|15|30)$')) | (score == "AD-40")
            ))
        )
    )
    bp_serving = player_pts[bp_serving_mask]

    bp_returning_mask = (
        ~player_pts["is_server"] &
        (
            ((player_pts["Svr"] == 2) & (
                (score.str.match(r'^(0|15|30)-40$')) | (score == "40-AD")
            )) |
            ((player_pts["Svr"] == 1) & (
                (score.str.match(r'^40-(0|15|30)$')) | (score == "AD-40")
            ))
        )
    )
    bp_returning = player_pts[bp_returning_mask]

    # ── S1: Break Point Serving (serve direction shift) ──
    if len(bp_serving) >= 30 and len(serving) >= 100:
        baseline_dir = _serve_dir_dist(serving)
        scenario_dir = _serve_dir_dist(bp_serving)
        if baseline_dir and scenario_dir:
            max_diff = max(abs(scenario_dir.get(d, 0) - baseline_dir.get(d, 0)) for d in ["wide", "body", "T"])
            if max_diff > 0.03:
                biggest_dir = max(["wide", "body", "T"],
                                  key=lambda d: abs(scenario_dir.get(d, 0) - baseline_dir.get(d, 0)))
                scenarios.append({
                    "scenario": "Break Point Serving",
                    "category": "serve_pressure",
                    "description": f"Serves {biggest_dir} {int(scenario_dir.get(biggest_dir,0)*100)}% on break points vs {int(baseline_dir.get(biggest_dir,0)*100)}% normally",
                    "baseline": {k: float(v) for k, v in baseline_dir.items()},
                    "scenario_data": {k: float(v) for k, v in scenario_dir.items()},
                    "sample_size": int(len(bp_serving)),
                    "significance": _sig(max_diff),
                    "surface": None,
                })

    # ── S2: Break Point Returning ──
    if len(bp_returning) >= 30 and len(returning) >= 100:
        bp_wr = float(bp_returning["won_point"].mean())
        ret_wr = float(returning["won_point"].mean())
        diff = bp_wr - ret_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Break Point Returning",
                "category": "serve_pressure",
                "description": f"Converts {int(bp_wr*100)}% of break points vs {int(ret_wr*100)}% return points normally",
                "baseline": {"win_rate": float(round(ret_wr, 3))},
                "scenario_data": {"win_rate": float(round(bp_wr, 3))},
                "sample_size": int(len(bp_returning)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S3: First serve % under pressure ──
    if len(bp_serving) >= 30 and len(serving) >= 100:
        bp_first_pct = float((bp_serving["2nd"].isna() | (bp_serving["2nd"] == "")).mean())
        all_first_pct = float((serving["2nd"].isna() | (serving["2nd"] == "")).mean())
        diff_pct = bp_first_pct - all_first_pct
        if abs(diff_pct) > 0.03:
            scenarios.append({
                "scenario": "First Serve % Under Pressure",
                "category": "serve_pressure",
                "description": f"Lands {int(bp_first_pct*100)}% first serves on break points vs {int(all_first_pct*100)}% normally",
                "baseline": {"first_serve_pct": float(round(all_first_pct, 3))},
                "scenario_data": {"first_serve_pct": float(round(bp_first_pct, 3))},
                "sample_size": int(len(bp_serving)),
                "significance": _sig(diff_pct),
                "surface": None,
            })

    # ── S4: Tiebreak performance ──
    tb_mask = (player_pts["Gm1"] == 6) & (player_pts["Gm2"] == 6)
    tb_pts = player_pts[tb_mask]
    if len(tb_pts) >= 30:
        tb_wr = float(tb_pts["won_point"].mean())
        diff = tb_wr - overall_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Tiebreak Play",
                "category": "match_situation",
                "description": f"Wins {int(tb_wr*100)}% of tiebreak points vs {int(overall_wr*100)}% overall",
                "baseline": {"win_rate": float(round(overall_wr, 3))},
                "scenario_data": {"win_rate": float(round(tb_wr, 3))},
                "sample_size": int(len(tb_pts)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S5: Rally Length (4-band) ──
    if "rally_length" in player_pts.columns:
        bands = [("Short (1-3)", 1, 3), ("Medium (4-6)", 4, 6), ("Long (7-9)", 7, 9), ("Very Long (10+)", 10, 999)]
        band_results = []
        for label, lo, hi in bands:
            b = player_pts[(player_pts["rally_length"] >= lo) & (player_pts["rally_length"] <= hi)]
            if len(b) >= 30:
                band_results.append((label, float(b["won_point"].mean()), int(len(b))))
        if len(band_results) >= 2:
            best = max(band_results, key=lambda x: x[1])
            worst = min(band_results, key=lambda x: x[1])
            spread = best[1] - worst[1]
            if spread > 0.03:
                scenarios.append({
                    "scenario": "Rally Length Preference",
                    "category": "rally_patterns",
                    "description": f"Strongest in {best[0]} rallies ({int(best[1]*100)}%), weakest in {worst[0]} ({int(worst[1]*100)}%)",
                    "baseline": {r[0]: float(round(r[1], 3)) for r in band_results},
                    "scenario_data": {"best_band": best[0], "best_wr": float(round(best[1], 3)),
                                      "worst_band": worst[0], "worst_wr": float(round(worst[1], 3))},
                    "sample_size": int(sum(r[2] for r in band_results)),
                    "significance": _sig(spread),
                    "surface": None,
                })

    # ── S6: Net approach success ──
    if "last_shot_type" in player_pts.columns:
        volley_types = ['fh_volley', 'bh_volley', 'fh_half_volley']
        net_pts = player_pts[player_pts["last_shot_type"].isin(volley_types)]
        non_net = player_pts[~player_pts["last_shot_type"].isin(volley_types)]
        if len(net_pts) >= 30 and len(non_net) >= 100:
            net_wr = float(net_pts["won_point"].mean())
            base_wr = float(non_net["won_point"].mean())
            diff = net_wr - base_wr
            if abs(diff) > 0.03:
                scenarios.append({
                    "scenario": "Net Approach",
                    "category": "rally_patterns",
                    "description": f"Wins {int(net_wr*100)}% of net points vs {int(base_wr*100)}% at baseline",
                    "baseline": {"win_rate": float(round(base_wr, 3))},
                    "scenario_data": {"win_rate": float(round(net_wr, 3)),
                                      "net_points": int(len(net_pts))},
                    "sample_size": int(len(net_pts)),
                    "significance": _sig(diff),
                    "surface": None,
                })

    # ── S7: First vs Second Serve ──
    first_serve = serving[serving["2nd"].isna() | (serving["2nd"] == "")]
    second_serve = serving[serving["2nd"].notna() & (serving["2nd"] != "")]
    if len(first_serve) >= 30 and len(second_serve) >= 30:
        first_wr = float(first_serve["won_point"].mean())
        second_wr_val = float(second_serve["won_point"].mean())
        drop = first_wr - second_wr_val
        if drop > 0.03:
            scenarios.append({
                "scenario": "First vs Second Serve",
                "category": "serve_pressure",
                "description": f"Wins {int(first_wr*100)}% on 1st serve vs {int(second_wr_val*100)}% on 2nd ({int(drop*100)}pt drop)",
                "baseline": {"first_serve_wr": float(round(first_wr, 3))},
                "scenario_data": {"second_serve_wr": float(round(second_wr_val, 3)),
                                  "drop": float(round(drop, 3))},
                "sample_size": int(len(second_serve)),
                "significance": _sig(drop),
                "surface": None,
            })

    # ── S8: Return performance (1st vs 2nd serve return) ──
    # When returning, check if opponent was on 1st or 2nd serve
    ret_vs_first = returning[returning["2nd"].isna() | (returning["2nd"] == "")]
    ret_vs_second = returning[returning["2nd"].notna() & (returning["2nd"] != "")]
    if len(ret_vs_first) >= 30 and len(ret_vs_second) >= 30:
        r1_wr = float(ret_vs_first["won_point"].mean())
        r2_wr = float(ret_vs_second["won_point"].mean())
        diff = r2_wr - r1_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Return Game",
                "category": "rally_patterns",
                "description": f"Wins {int(r2_wr*100)}% vs 2nd serves vs {int(r1_wr*100)}% vs 1st serves",
                "baseline": {"vs_first_serve_wr": float(round(r1_wr, 3))},
                "scenario_data": {"vs_second_serve_wr": float(round(r2_wr, 3))},
                "sample_size": int(len(ret_vs_first) + len(ret_vs_second)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S9: Down a set resilience ──
    down_set_mask = (
        (p1_mask & (player_pts["Set1"] < player_pts["Set2"])) |
        (~p1_mask & (player_pts["Set2"] < player_pts["Set1"]))
    )
    down_set_pts = player_pts[down_set_mask]
    if len(down_set_pts) >= 30:
        down_wr = float(down_set_pts["won_point"].mean())
        diff = down_wr - overall_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Down a Set",
                "category": "match_situation",
                "description": f"Wins {int(down_wr*100)}% of points when trailing in sets vs {int(overall_wr*100)}% overall",
                "baseline": {"win_rate": float(round(overall_wr, 3))},
                "scenario_data": {"win_rate": float(round(down_wr, 3))},
                "sample_size": int(len(down_set_pts)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S10: First set advantage ──
    # Points where player won the first set (Set1>Set2 if p1, Set2>Set1 if p2)
    # and we're in set 2+ (Set1+Set2 >= 1)
    won_first_set = (
        (p1_mask & (player_pts["Set1"] > player_pts["Set2"]) & (player_pts["Set1"] + player_pts["Set2"] >= 1)) |
        (~p1_mask & (player_pts["Set2"] > player_pts["Set1"]) & (player_pts["Set1"] + player_pts["Set2"] >= 1))
    )
    lost_first_set = (
        (p1_mask & (player_pts["Set1"] < player_pts["Set2"]) & (player_pts["Set1"] + player_pts["Set2"] >= 1)) |
        (~p1_mask & (player_pts["Set2"] < player_pts["Set1"]) & (player_pts["Set1"] + player_pts["Set2"] >= 1))
    )
    wf = player_pts[won_first_set]
    lf = player_pts[lost_first_set]
    if len(wf) >= 30 and len(lf) >= 30:
        wf_wr = float(wf["won_point"].mean())
        lf_wr = float(lf["won_point"].mean())
        diff = wf_wr - lf_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "First Set Impact",
                "category": "match_situation",
                "description": f"Wins {int(wf_wr*100)}% when ahead in sets vs {int(lf_wr*100)}% when behind",
                "baseline": {"ahead_wr": float(round(wf_wr, 3))},
                "scenario_data": {"behind_wr": float(round(lf_wr, 3))},
                "sample_size": int(len(wf) + len(lf)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S11: Final set performance ──
    best_of_3 = player_pts["Best of"].astype(str) == "3"
    best_of_5 = player_pts["Best of"].astype(str) == "5"
    decider_mask = (
        (best_of_3 & (player_pts["Set1"] == 1) & (player_pts["Set2"] == 1)) |
        (best_of_5 & (player_pts["Set1"] == 2) & (player_pts["Set2"] == 2))
    )
    decider_pts = player_pts[decider_mask]
    if len(decider_pts) >= 30:
        dec_wr = float(decider_pts["won_point"].mean())
        diff = dec_wr - overall_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Deciding Set",
                "category": "match_situation",
                "description": f"Wins {int(dec_wr*100)}% of points in deciding sets vs {int(overall_wr*100)}% overall",
                "baseline": {"win_rate": float(round(overall_wr, 3))},
                "scenario_data": {"win_rate": float(round(dec_wr, 3))},
                "sample_size": int(len(decider_pts)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S12: Surface-specific serve patterns ──
    if "Surface" in player_pts.columns and "serve_direction" in player_pts.columns:
        baseline_dir = _serve_dir_dist(serving)
        if baseline_dir:
            for surf_name in ["Hard", "Clay", "Grass"]:
                surf_serving = serving[player_pts.loc[serving.index, "Surface"] == surf_name]
                if len(surf_serving) >= 30:
                    surf_dir = _serve_dir_dist(surf_serving)
                    if surf_dir:
                        max_diff = max(abs(surf_dir.get(d, 0) - baseline_dir.get(d, 0)) for d in ["wide", "body", "T"])
                        if max_diff > 0.03:
                            biggest_dir = max(["wide", "body", "T"],
                                              key=lambda d: abs(surf_dir.get(d, 0) - baseline_dir.get(d, 0)))
                            scenarios.append({
                                "scenario": f"Serve Pattern on {surf_name}",
                                "category": "surface_patterns",
                                "description": f"Serves {biggest_dir} {int(surf_dir.get(biggest_dir,0)*100)}% on {surf_name.lower()} vs {int(baseline_dir.get(biggest_dir,0)*100)}% overall",
                                "baseline": {k: float(v) for k, v in baseline_dir.items()},
                                "scenario_data": {k: float(v) for k, v in surf_dir.items()},
                                "sample_size": int(len(surf_serving)),
                                "significance": _sig(max_diff),
                                "surface": surf_name,
                            })

    # ── S13: Serve+1 pattern (what shot follows the serve?) ──
    if "shot_sequence" in player_pts.columns:
        srv_seqs = serving["shot_sequence"].dropna()
        srv_seqs = srv_seqs[srv_seqs.str.len() >= 2]
        if len(srv_seqs) >= 50:
            # 3rd character is the server's first shot after serve (serve+1)
            # Sequence: S = serve, 2nd = return, 3rd = serve+1
            srv_plus1 = srv_seqs[srv_seqs.str.len() >= 3].str[2]
            if len(srv_plus1) >= 50:
                from collections import Counter as _Counter
                sp1_counts = _Counter(srv_plus1)
                sp1_total = sum(sp1_counts.values())
                sp1_dist = {k: round(v / sp1_total, 3) for k, v in sp1_counts.most_common(5)}

                # Compare on break points
                bp_seqs = bp_serving["shot_sequence"].dropna() if len(bp_serving) >= 30 else pd.Series(dtype=str)
                bp_seqs = bp_seqs[bp_seqs.str.len() >= 3]
                if len(bp_seqs) >= 30:
                    bp_sp1 = bp_seqs.str[2]
                    bp_sp1_counts = _Counter(bp_sp1)
                    bp_sp1_total = sum(bp_sp1_counts.values())
                    bp_sp1_dist = {k: round(v / bp_sp1_total, 3) for k, v in bp_sp1_counts.most_common(5)}

                    # Check for shift in forehand/backhand ratio
                    baseline_fh = sp1_dist.get('F', 0)
                    bp_fh = bp_sp1_dist.get('F', 0)
                    diff = abs(bp_fh - baseline_fh)
                    if diff > 0.03:
                        scenarios.append({
                            "scenario": "Serve+1 Under Pressure",
                            "category": "serve_pressure",
                            "description": f"Hits forehand serve+1 {int(bp_fh*100)}% on break points vs {int(baseline_fh*100)}% normally",
                            "baseline": {k: float(v) for k, v in sp1_dist.items()},
                            "scenario_data": {k: float(v) for k, v in bp_sp1_dist.items()},
                            "sample_size": int(len(bp_seqs)),
                            "significance": _sig(diff),
                            "surface": None,
                        })

    # ── S14: Comfort zone (leading by 2+ games in current set) ──
    leading_mask = (
        (p1_mask & (player_pts["Gm1"] >= player_pts["Gm2"] + 2)) |
        (~p1_mask & (player_pts["Gm2"] >= player_pts["Gm1"] + 2))
    )
    trailing_mask = (
        (p1_mask & (player_pts["Gm2"] >= player_pts["Gm1"] + 2)) |
        (~p1_mask & (player_pts["Gm1"] >= player_pts["Gm2"] + 2))
    )
    leading_pts = player_pts[leading_mask]
    trailing_pts = player_pts[trailing_mask]
    if len(leading_pts) >= 30 and len(trailing_pts) >= 30:
        lead_wr = float(leading_pts["won_point"].mean())
        trail_wr = float(trailing_pts["won_point"].mean())
        diff = lead_wr - trail_wr
        if abs(diff) > 0.03:
            scenarios.append({
                "scenario": "Comfort Zone",
                "category": "match_situation",
                "description": f"Wins {int(lead_wr*100)}% when up 2+ games vs {int(trail_wr*100)}% when down 2+",
                "baseline": {"leading_wr": float(round(lead_wr, 3)),
                              "trailing_wr": float(round(trail_wr, 3))},
                "scenario_data": {"spread": float(round(diff, 3))},
                "sample_size": int(len(leading_pts) + len(trailing_pts)),
                "significance": _sig(diff),
                "surface": None,
            })

    # ── S15: Deuce point serve direction ──
    if "serve_direction" in player_pts.columns:
        # Deuce points: score is 40-40 or AD-40/40-AD
        deuce_mask = player_pts["is_server"] & (score == "40-40")
        deuce_serving = player_pts[deuce_mask]
        if len(deuce_serving) >= 30 and len(serving) >= 100:
            deuce_dir = _serve_dir_dist(deuce_serving)
            baseline_dir = _serve_dir_dist(serving)
            if deuce_dir and baseline_dir:
                max_diff = max(abs(deuce_dir.get(d, 0) - baseline_dir.get(d, 0)) for d in ["wide", "body", "T"])
                if max_diff > 0.03:
                    biggest_dir = max(["wide", "body", "T"],
                                      key=lambda d: abs(deuce_dir.get(d, 0) - baseline_dir.get(d, 0)))
                    scenarios.append({
                        "scenario": "Deuce Point Serving",
                        "category": "serve_pressure",
                        "description": f"Serves {biggest_dir} {int(deuce_dir.get(biggest_dir,0)*100)}% on deuce points vs {int(baseline_dir.get(biggest_dir,0)*100)}% normally",
                        "baseline": {k: float(v) for k, v in baseline_dir.items()},
                        "scenario_data": {k: float(v) for k, v in deuce_dir.items()},
                        "sample_size": int(len(deuce_serving)),
                        "significance": _sig(max_diff),
                        "surface": None,
                    })

    # ── S16: Closing Out Sets (up 5-x in games) ──
    if "Gm1" in player_pts.columns and "Gm2" in player_pts.columns:
        closing_mask = (
            (p1_mask & (player_pts["Gm1"] == 5) & (player_pts["Gm2"] < 5)) |
            (~p1_mask & (player_pts["Gm2"] == 5) & (player_pts["Gm1"] < 5))
        )
        closing_pts = player_pts[closing_mask]
        if len(closing_pts) >= 30:
            close_wr = float(closing_pts["won_point"].mean())
            diff = close_wr - overall_wr
            if abs(diff) > 0.02:
                scenarios.append({
                    "scenario": "Closing Out Sets",
                    "category": "match_situation",
                    "description": f"Wins {int(close_wr*100)}% of points when serving for the set (up 5-x) vs {int(overall_wr*100)}% overall",
                    "baseline": {"win_rate": float(round(overall_wr, 3))},
                    "scenario_data": {"win_rate": float(round(close_wr, 3))},
                    "sample_size": int(len(closing_pts)),
                    "significance": _sig(diff),
                    "surface": None,
                })

    # ── S17: Surface-specific rally length ──
    if "rally_length" in player_pts.columns and "Surface" in player_pts.columns:
        for surf_name in ["Hard", "Clay", "Grass"]:
            surf_pts = player_pts[player_pts["Surface"] == surf_name]
            if len(surf_pts) < 100:
                continue
            bands = [("Short (1-3)", 1, 3), ("Medium (4-6)", 4, 6), ("Long (7-9)", 7, 9), ("Very Long (10+)", 10, 999)]
            band_results = []
            for label, lo, hi in bands:
                b = surf_pts[(surf_pts["rally_length"] >= lo) & (surf_pts["rally_length"] <= hi)]
                if len(b) >= 20:
                    band_results.append((label, float(b["won_point"].mean()), int(len(b))))
            if len(band_results) >= 2:
                best = max(band_results, key=lambda x: x[1])
                worst = min(band_results, key=lambda x: x[1])
                spread = best[1] - worst[1]
                if spread > 0.03:
                    scenarios.append({
                        "scenario": f"Rally Length on {surf_name}",
                        "category": "surface_patterns",
                        "description": f"On {surf_name.lower()}: strongest in {best[0]} ({int(best[1]*100)}%), weakest in {worst[0]} ({int(worst[1]*100)}%)",
                        "baseline": {r[0]: float(round(r[1], 3)) for r in band_results},
                        "scenario_data": {"best_band": best[0], "best_wr": float(round(best[1], 3)),
                                          "worst_band": worst[0], "worst_wr": float(round(worst[1], 3))},
                        "sample_size": int(sum(r[2] for r in band_results)),
                        "significance": _sig(spread),
                        "surface": surf_name,
                    })

    # ── Filter obvious / uninteresting scenarios ──
    filtered = []
    for s in scenarios:
        sn = s["scenario"]
        sd = s.get("scenario_data", {})
        bl = s.get("baseline", {})

        if sn == "First vs Second Serve":
            # Only include if drop is unusually large (>15%) or unusually small (<5%)
            drop = sd.get("drop", 0)
            if not (drop > 0.15 or drop < 0.05):
                continue

        elif sn == "Return Game":
            # Only include if return win rate vs 2nd serve is >55% or <40%
            r2_wr = sd.get("vs_second_serve_wr", 0.5)
            if not (r2_wr > 0.55 or r2_wr < 0.40):
                continue

        elif sn == "Net Approach":
            # Only include if deviation > 5%
            net_wr = sd.get("win_rate", 0)
            base_wr = bl.get("win_rate", 0)
            if abs(net_wr - base_wr) <= 0.05:
                continue

        elif sn == "First Set Impact":
            # Only include if deviation > 5%
            ahead_wr = bl.get("ahead_wr", 0)
            behind_wr = sd.get("behind_wr", 0)
            if abs(ahead_wr - behind_wr) <= 0.05:
                continue

        # Keep all others: Serve+1 Under Pressure, Tiebreak, Down a Set,
        # Comfort Zone, surface-specific patterns, etc.
        filtered.append(s)
    scenarios = filtered

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
