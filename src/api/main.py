"""
TennisIQ — FastAPI Server
src/api/main.py

All endpoints specified in Phase 6.

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

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="TennisIQ API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
