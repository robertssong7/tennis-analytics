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
from datetime import datetime, date, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
import requests as req_lib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
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
    allow_origins=["*"],
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

# ── ATP 2026 Calendar ────────────────────────────────────────
_CALENDAR_PATH = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'atp_calendar_2026.json'
_calendar_cache: list = []


def _load_calendar() -> list:
    """Load ATP 2026 calendar from disk. Cached per process."""
    global _calendar_cache
    if not _calendar_cache and _CALENDAR_PATH.exists():
        try:
            _calendar_cache = json.loads(_CALENDAR_PATH.read_text())
        except Exception as e:
            logger.warning(f"Failed to load atp_calendar_2026.json: {e}")
    return _calendar_cache


def get_live_tournament(today=None):
    """Return the calendar entry for the tournament being played today, or None."""
    today = today or date.today()
    for t in _load_calendar():
        try:
            if datetime.fromisoformat(t["start"]).date() <= today <= datetime.fromisoformat(t["end"]).date():
                return t
        except Exception:
            continue
    return None


def get_just_finished(today=None):
    """Most recent Masters 1000 / Slam / Finals that has ended."""
    today = today or date.today()
    finished = []
    for t in _load_calendar():
        try:
            end_d = datetime.fromisoformat(t["end"]).date()
        except Exception:
            continue
        if end_d < today and t.get("category") in ("Masters 1000", "Grand Slam", "ATP Finals"):
            finished.append(t)
    if not finished:
        return None
    return max(finished, key=lambda t: t["end"])


def get_next_upcoming(today=None):
    """Next tournament whose start is in the future."""
    today = today or date.today()
    upcoming = []
    for t in _load_calendar():
        try:
            if datetime.fromisoformat(t["start"]).date() > today:
                upcoming.append(t)
        except Exception:
            continue
    return min(upcoming, key=lambda t: t["start"]) if upcoming else None


# ── Open-Meteo weather + court speed badge ───────────────────
TOURNAMENT_GEO = {
    "Melbourne": (-37.8136, 144.9631),
    "Indian Wells": (33.7175, -116.2156),
    "Miami": (25.7617, -80.1918),
    "Monte Carlo": (43.7384, 7.4246),
    "Madrid": (40.4168, -3.7038),
    "Rome": (41.9028, 12.4964),
    "Paris": (48.8566, 2.3522),
    "London": (51.5074, -0.1278),
    "Toronto": (43.6532, -79.3832),
    "Cincinnati": (39.1031, -84.5120),
    "New York": (40.7128, -74.0060),
    "Shanghai": (31.2304, 121.4737),
    "Turin": (45.0703, 7.6869),
}

# Per-city CPI baselines (avg from data/processed/court_speed.csv recent years).
# Used when no precise CPI lookup is available for a tournament-year.
CITY_CPI_BASELINE = {
    "Melbourne": 36.0,
    "Indian Wells": 33.0,
    "Miami": 38.0,
    "Monte Carlo": 23.0,
    "Madrid": 28.0,
    "Rome": 25.0,
    "Paris": 38.0,
    "London": 38.5,
    "Toronto": 39.0,
    "Cincinnati": 41.0,
    "New York": 39.0,
    "Shanghai": 38.0,
    "Turin": 42.0,
}

_weather_cache: dict = {}  # city -> (timestamp, payload)


def _fetch_weather(city: str):
    """Open-Meteo current + 3-day forecast. 1-hour in-memory cache."""
    if city not in TOURNAMENT_GEO:
        return {"available": False, "reason": f"No coordinates for {city}"}
    import time
    now = time.time()
    cached = _weather_cache.get(city)
    if cached and now - cached[0] < 3600:
        return cached[1]
    lat, lon = TOURNAMENT_GEO[city]
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"
        f"&forecast_days=3&timezone=auto"
    )
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        cur = data.get("current", {}) or {}
        daily = data.get("daily", {}) or {}
        forecast = []
        days = daily.get("time", []) or []
        for i in range(min(3, len(days))):
            forecast.append({
                "date": daily["time"][i],
                "high_c": float(daily["temperature_2m_max"][i] or 0),
                "low_c": float(daily["temperature_2m_min"][i] or 0),
                "rain_pct": int(daily["precipitation_probability_max"][i] or 0),
                "wind_kmh": float(daily["wind_speed_10m_max"][i] or 0),
            })
        payload = {
            "available": True,
            "city": city,
            "current": {
                "temp_c": float(cur.get("temperature_2m") or 0),
                "humidity": int(cur.get("relative_humidity_2m") or 0),
                "wind_kmh": float(cur.get("wind_speed_10m") or 0),
                "weather_code": int(cur.get("weather_code") or 0),
            },
            "forecast": forecast,
        }
        _weather_cache[city] = (now, payload)
        return payload
    except Exception as e:
        payload = {"available": False, "reason": str(e)}
        _weather_cache[city] = (now, payload)
        return payload


@app.get("/api/tournament-weather")
def tournament_weather(city: str):
    """Live weather for a tournament city (Open-Meteo, no API key)."""
    return _fetch_weather(city)


def get_court_speed_label(cpi_base: float, weather: dict | None) -> dict:
    """Combine baseline CPI with weather adjustments to return slow/medium/fast badge.
    Hot air → faster ball flight; humidity → slower; wind → unpredictable but
    biased fast. Returns {label, cpi, color}."""
    cpi = float(cpi_base) if cpi_base is not None else 36.0
    if weather and weather.get("available"):
        c = weather.get("current") or {}
        try:
            if float(c.get("temp_c") or 0) > 28:
                cpi += 3
            if float(c.get("humidity") or 0) > 75:
                cpi -= 3
            if float(c.get("wind_kmh") or 0) > 25:
                cpi += 2
        except Exception:
            pass
    if cpi < 35:
        return {"label": "Slow", "cpi": round(cpi, 1), "color": "#D4724E"}
    if cpi < 45:
        return {"label": "Medium", "cpi": round(cpi, 1), "color": "#DAA520"}
    return {"label": "Fast", "cpi": round(cpi, 1), "color": "#4A90D9"}

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

_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<rect width="100" height="100" fill="#F5F0EB"/>'
    '<circle cx="50" cy="38" r="18" fill="#D0C9C0"/>'
    '<path d="M 18 92 Q 18 60 50 60 Q 82 60 82 92 Z" fill="#D0C9C0"/>'
    '</svg>'
)


@app.get("/api/player-image/{code}")
def get_player_image(code: str):
    """Proxy ATP headshot images with disk caching. On upstream failure,
    return a silhouette SVG placeholder so the frontend never sees broken images."""
    import re
    # Sanitize code to prevent path traversal
    if not re.match(r'^[a-zA-Z0-9]{2,10}$', code):
        return JSONResponse(status_code=400, content={"error": "Invalid code"})

    # Disk cache files are stored lowercase. Normalize to match.
    code_lc = code.lower()
    _HEADSHOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _HEADSHOT_CACHE_DIR / f"{code_lc}.png"

    # Serve from disk cache if available
    if cached.exists() and cached.stat().st_size > 500:
        return Response(content=cached.read_bytes(), media_type="image/png",
                       headers={"Cache-Control": "public, max-age=604800"})

    logger.info(f"Headshot cache MISS: {code_lc} — fetching from ATP")
    url = f"https://www.atptour.com/-/media/alias/player-headshot/{code_lc}"
    try:
        r = req_lib.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            cached.write_bytes(r.content)
            return Response(content=r.content, media_type=r.headers["content-type"],
                          headers={"Cache-Control": "public, max-age=604800"})
    except Exception:
        pass
    # Upstream failure (404, 403 Cloudflare challenge, network) → silhouette placeholder.
    return Response(
        content=_PLACEHOLDER_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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

    surface = req.surface
    is_overall = surface.lower() == "overall" or surface.lower() == "none" or not surface

    if is_overall:
        # Weighted average across surfaces (ATP calendar: ~50% hard, ~30% clay, ~20% grass)
        try:
            hard_result = engine.predict(req.player1, req.player2, "hard")
            clay_result = engine.predict(req.player1, req.player2, "clay")
            grass_result = engine.predict(req.player1, req.player2, "grass")
        except ValueError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise HTTPException(404, detail={"error": msg, "hint": "Try the full name"})
            raise HTTPException(400, detail=str(e))

        hard_p = float(hard_result['player1_win_prob'])
        clay_p = float(clay_result['player1_win_prob'])
        grass_p = float(grass_result['player1_win_prob'])
        overall_p = 0.50 * hard_p + 0.30 * clay_p + 0.20 * grass_p

        result = dict(hard_result)  # Copy structure from hard result
        result['surface'] = 'overall'
        result['player1_win_prob'] = float(round(overall_p, 4))
        result['player2_win_prob'] = float(round(1.0 - overall_p, 4))
        result['surface_breakdown'] = {
            'hard': {'p1_win_prob': float(round(hard_p, 4))},
            'clay': {'p1_win_prob': float(round(clay_p, 4))},
            'grass': {'p1_win_prob': float(round(grass_p, 4))},
        }
        result['weights'] = {'hard': 0.50, 'clay': 0.30, 'grass': 0.20}
        return result

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
    for year in range(1968, 2030):
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

    # Sample-size cutoffs: previously 10. Halved so partial coverage shows with
    # a low_sample flag. Anything below MIN_TOTAL is still dropped.
    MIN_TOTAL = 5
    LOW_SAMPLE_CUTOFF = 10

    def _make_cond(name_str, subset, category):
        wins = int(subset['won'].sum())
        n = int(len(subset))
        losses = n - wins
        return {
            "condition": name_str,
            "win_rate": float(round(wins / n, 3)),
            "wins": wins,
            "losses": losses,
            "matches": n,
            "category": category,
            "low_sample": n < LOW_SAMPLE_CUTOFF,
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
    if len(climate_labeled) >= MIN_TOTAL:
        for bucket_name in sorted(climate_labeled['_climate'].unique()):
            bucket = climate_labeled[climate_labeled['_climate'] == bucket_name]
            if len(bucket) >= MIN_TOTAL:
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
        if len(cpi_matches) >= MIN_TOTAL:
            for label, lo, hi in [('Slow (CPI < 30)', 0, 30), ('Medium (CPI 30-40)', 30, 40), ('Fast (CPI > 40)', 40, 100)]:
                bucket = cpi_matches[(cpi_matches['_cpi'] >= lo) & (cpi_matches['_cpi'] < hi)]
                if len(bucket) >= MIN_TOTAL:
                    conditions.append(_make_cond(label, bucket, "court_speed"))
                    has_court_speed = True

        if not has_court_speed:
            for surf, label in [('Clay', 'Slow (est.)'), ('Hard', 'Medium (est.)'), ('Grass', 'Fast (est.)')]:
                bucket = all_matches[all_matches['surface'] == surf]
                if len(bucket) >= MIN_TOTAL:
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
        if len(ball_matches) >= MIN_TOTAL:
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
                    if len(bucket) >= MIN_TOTAL:
                        conditions.append(_make_cond(ball_name, bucket, "ball_type"))
                        has_ball = True

        if not has_ball:
            missing_categories.append("ball_type")

        all_matches.drop(columns=['_year', '_tname_lower', '_cpi', '_ball', '_climate', '_court'], errors='ignore', inplace=True)
    else:
        has_court_speed = False
        for surf, label in [('Clay', 'Slow (est.)'), ('Hard', 'Medium (est.)'), ('Grass', 'Fast (est.)')]:
            bucket = all_matches[all_matches['surface'] == surf]
            if len(bucket) >= MIN_TOTAL:
                conditions.append(_make_cond(label, bucket, "court_speed"))
                has_court_speed = True
        if not has_court_speed:
            missing_categories.append("court_speed")
        missing_categories.append("ball_type")
        all_matches.drop(columns=['_year', '_tname_lower', '_climate', '_court'], errors='ignore', inplace=True)

    # Drop only those below the new (lower) MIN_TOTAL cutoff. Anything between
    # MIN_TOTAL and LOW_SAMPLE_CUTOFF stays but carries the low_sample flag.
    conditions = [c for c in conditions if c['matches'] >= MIN_TOTAL]

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

def _calendar_to_feed(t: dict, status: str) -> dict:
    """Convert a calendar entry to the feed dict shape (tournament/dates/location/...)."""
    if not t:
        return None
    try:
        start_d = datetime.fromisoformat(t["start"]).date()
        end_d = datetime.fromisoformat(t["end"]).date()
        # "May 6-17, 2026" or "Aug 31 - Sep 13, 2026"
        if start_d.month == end_d.month:
            dates_str = f"{start_d.strftime('%b %-d')}-{end_d.day}, {end_d.year}"
        else:
            dates_str = f"{start_d.strftime('%b %-d')} - {end_d.strftime('%b %-d')}, {end_d.year}"
    except Exception:
        dates_str = ""
    return {
        "tournament": t.get("name"),
        "year": int(t["start"][:4]) if t.get("start") else None,
        "dates": dates_str,
        "location": f"{t.get('city', '')}, {t.get('country', '')}".strip(", "),
        "surface": (t.get("surface") or "").capitalize(),
        "level": t.get("category"),
        "indoor_outdoor": "Indoor" if t.get("indoor") else "Outdoor",
        "status": status,
        "draw_size": t.get("draw_size"),
    }


def _scrape_results_for_tournament(tournament_name: str, year: int):
    """Pull results for a tournament+year from the supplemental CSV."""
    suppl_csv = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'supplemental_matches_2025_2026.csv'
    if not suppl_csv.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_csv(suppl_csv)
        df = df.dropna(subset=['winner_name', 'loser_name', 'tourney_name'])
        df['tourney_date'] = pd.to_numeric(df['tourney_date'], errors='coerce').fillna(0).astype(int)
        # Token-match: try a few aliases
        name_lower = tournament_name.lower()
        aliases = [name_lower]
        if 'italian' in name_lower or 'rome' in name_lower:
            aliases += ['italian', 'rome', 'internazionali']
        if 'madrid' in name_lower:
            aliases += ['madrid']
        if 'monte carlo' in name_lower:
            aliases += ['monte carlo']
        if 'indian wells' in name_lower or 'bnp' in name_lower:
            aliases += ['indian wells', 'bnp']
        if 'miami' in name_lower:
            aliases += ['miami']
        mask = False
        for alias in aliases:
            mask = mask | df['tourney_name'].str.lower().str.contains(alias, na=False)
        sub = df[mask & (df['tourney_date'] >= year * 10000) & (df['tourney_date'] < (year + 1) * 10000)]
        if len(sub) == 0:
            sub = df[mask]  # fallback to any year
        sub = sub.sort_values('tourney_date')
        return [
            {
                "winner": str(r.get('winner_name', '')),
                "loser": str(r.get('loser_name', '')),
                "score": str(r.get('score', '')),
                "round": str(r.get('round', '')),
            }
            for _, r in sub.iterrows()
        ]
    except Exception as e:
        logger.warning(f"Could not scrape results for {tournament_name}: {e}")
        return []


@app.get("/api/live-tournament")
def live_tournament():
    """Return live, just_finished, and next_upcoming tournament metadata
    derived from the ATP 2026 calendar. Match-level results inside each
    section are pulled from the supplemental CSV when available.
    Backward-compat: also returns 'finished' and 'current' keys for older
    frontends."""
    today = date.today()
    live = get_live_tournament(today)
    finished = get_just_finished(today)
    upcoming = get_next_upcoming(today)

    live_feed = _calendar_to_feed(live, status="Live") if live else None
    finished_feed = _calendar_to_feed(finished, status="Complete") if finished else None
    upcoming_feed = _calendar_to_feed(upcoming, status="Upcoming") if upcoming else None

    if live_feed:
        live_results = _scrape_results_for_tournament(live["name"], int(live["start"][:4]))
        live_feed["results"] = live_results[-20:]
        live_feed["data_available"] = len(live_results) > 0
        city = live.get("city")
        cpi_base = CITY_CPI_BASELINE.get(city, 36.0)
        weather = _fetch_weather(city) if city else None
        live_feed["weather"] = weather
        live_feed["court_speed"] = get_court_speed_label(cpi_base, weather)
        live_feed["cpi_base"] = cpi_base
    if finished_feed:
        fr = _scrape_results_for_tournament(finished["name"], int(finished["start"][:4]))
        finished_feed["results"] = fr[-20:]
        finished_feed["data_available"] = len(fr) > 0
        city = finished.get("city")
        cpi_base = CITY_CPI_BASELINE.get(city, 36.0)
        finished_feed["court_speed"] = get_court_speed_label(cpi_base, None)
        finished_feed["cpi_base"] = cpi_base

    return {
        # New keys (Session 10)
        "live": live_feed,
        "just_finished": finished_feed,
        "next_upcoming": upcoming_feed,
        # Backward compat for older frontends
        "finished": finished_feed,
        "current": live_feed or upcoming_feed,
    }


# ─────────────────────────────────────────────────────────────
# Tournament predictions endpoint
# ─────────────────────────────────────────────────────────────

@app.post("/api/match-insight")
async def match_insight(request: Request):
    """Deep match analysis: prediction + reasons + x-factor + upset indicators."""
    body = await request.json()
    p1_raw = body.get("player1", "")
    p2_raw = body.get("player2", "")
    surface = body.get("surface", "hard")

    engine = _get_engine()
    p1 = engine.find_player(p1_raw)
    p2 = engine.find_player(p2_raw)
    if not p1 or not p2:
        return {"available": False, "reason": "Player not found"}

    if surface.lower() == "overall":
        surface = "hard"

    try:
        pred = engine.predict(p1, p2, surface)
    except Exception as e:
        return {"available": False, "reason": str(e)}

    p1_prob = float(pred.get("player1_win_prob", 0.5))
    p2_prob = float(1 - p1_prob)

    card1 = engine.get_player_card(p1, surface)
    card2 = engine.get_player_card(p2, surface)
    if not card1 or not card2:
        return {"available": False, "reason": "Player data unavailable"}

    favorite = p1 if p1_prob >= 0.5 else p2
    underdog = p2 if p1_prob >= 0.5 else p1
    fav_prob = float(max(p1_prob, p2_prob))
    dog_prob = float(min(p1_prob, p2_prob))
    fav_card = card1 if favorite == p1 else card2
    dog_card = card2 if favorite == p1 else card1

    elo1 = float(card1.get("elo", 1500))
    elo2 = float(card2.get("elo", 1500))
    elo_diff = abs(elo1 - elo2)
    higher_elo = p1 if elo1 > elo2 else p2

    reasons = []

    # 1. Rating gap
    if elo_diff > 200:
        reasons.append({"type": "rating_gap", "title": "Significant rating advantage",
            "detail": f"{higher_elo} holds a {int(elo_diff)}-point Elo edge ({int(max(elo1,elo2))} vs {int(min(elo1,elo2))}). Gaps this large typically decide matches.",
            "favors": higher_elo, "weight": "high"})
    elif elo_diff > 80:
        reasons.append({"type": "rating_gap", "title": "Moderate rating edge",
            "detail": f"{higher_elo} leads by {int(elo_diff)} Elo points. Meaningful but not decisive.",
            "favors": higher_elo, "weight": "medium"})
    elif elo_diff < 40:
        reasons.append({"type": "rating_gap", "title": "Razor-thin ratings gap",
            "detail": f"Only {int(elo_diff)} Elo points separate them. A true coin-flip on ratings alone.",
            "favors": "neither", "weight": "low"})

    # 2. Surface edge
    surf_r = {}
    for p, card in [(p1, card1), (p2, card2)]:
        s = card.get("surfaces", {})
        surf_r[p] = float(s.get(surface, card.get("overall", 80)) or card.get("overall", 80))
    surf_diff = abs(surf_r[p1] - surf_r[p2])
    better_surf = p1 if surf_r[p1] > surf_r[p2] else p2
    worse_surf = p2 if better_surf == p1 else p1
    if surf_diff > 5:
        reasons.append({"type": "surface_edge", "title": f"{surface.capitalize()} court specialist advantage",
            "detail": f"{better_surf} rates {surf_r[better_surf]:.1f} on {surface} vs {surf_r[worse_surf]:.1f}. This surface amplifies the gap.",
            "favors": better_surf, "weight": "high"})
    elif surf_diff > 2:
        reasons.append({"type": "surface_edge", "title": f"Slight {surface} court edge",
            "detail": f"{better_surf} has a small edge on {surface} ({surf_r[better_surf]:.1f} vs {surf_r[worse_surf]:.1f}).",
            "favors": better_surf, "weight": "medium"})

    # 3. Attribute mismatches
    attrs1, attrs2 = card1.get("attributes", {}), card2.get("attributes", {})
    dog_adv, fav_adv = [], []
    for attr in ["serve", "groundstroke", "volley", "footwork", "endurance", "durability", "clutch", "mental"]:
        v1 = attrs1.get(attr); v2 = attrs2.get(attr)
        if v1 is None or v2 is None: continue
        v1, v2 = float(v1), float(v2)
        diff = (v1 - v2) if underdog == p1 else (v2 - v1)
        if diff > 8:
            dog_adv.append({"attr": attr, "diff": int(abs(diff)), "dog_val": int(v1 if underdog == p1 else v2), "fav_val": int(v2 if underdog == p1 else v1)})
        elif diff < -8:
            fav_adv.append({"attr": attr, "diff": int(abs(diff)), "fav_val": int(v1 if favorite == p1 else v2), "dog_val": int(v2 if favorite == p1 else v1)})

    if dog_adv:
        top = sorted(dog_adv, key=lambda x: x["diff"], reverse=True)[:2]
        txt = ", ".join([f"{a['attr']} ({a['dog_val']} vs {a['fav_val']})" for a in top])
        reasons.append({"type": "attribute_mismatch", "title": f"{underdog}'s hidden edges",
            "detail": f"Despite being the underdog, {underdog} is stronger in: {txt}. If the match flows toward these skills, the upset window opens.",
            "favors": underdog, "weight": "medium"})
    if fav_adv:
        top = sorted(fav_adv, key=lambda x: x["diff"], reverse=True)[:2]
        txt = ", ".join([f"{a['attr']} ({a['fav_val']} vs {a['dog_val']})" for a in top])
        reasons.append({"type": "attribute_dominance", "title": f"{favorite} dominates key skills",
            "detail": f"{favorite} holds clear edges in: {txt}. These advantages are hard to overcome.",
            "favors": favorite, "weight": "high"})

    # 4. H2H
    h2h_key = tuple(sorted([p1, p2]))
    h2h_entry = engine.h2h.get(h2h_key)
    if h2h_entry:
        w1 = int(h2h_entry['wins'].get(p1, 0))
        w2 = int(h2h_entry['wins'].get(p2, 0))
        total = w1 + w2
        if total > 0:
            leader = p1 if w1 > w2 else p2
            bigger, smaller = max(w1, w2), min(w1, w2)
            reasons.append({"type": "h2h", "title": f"Head-to-head: {leader} leads {bigger}-{smaller}",
                "detail": f"Historical matchup favors {leader}. Past results indicate a stylistic edge.",
                "favors": leader, "weight": "medium" if bigger - smaller >= 2 else "low"})

    # X-Factor
    x_factor = None
    if dog_prob > 0.35 and elo_diff > 100:
        x_factor = {"title": f"Upset alert: {underdog} at {dog_prob*100:.0f}%",
            "detail": f"The model gives {underdog} a {dog_prob*100:.0f}% chance despite a {int(elo_diff)}-point Elo deficit. ", "type": "upset_potential"}
        if dog_adv:
            x_factor["detail"] += f"Key driver: {underdog}'s {dog_adv[0]['attr']} ({dog_adv[0]['dog_val']}) outclasses {favorite}'s ({dog_adv[0]['fav_val']}). "
    elif fav_prob > 0.75:
        x_factor = {"title": f"{favorite} heavily favored at {fav_prob*100:.0f}%",
            "detail": f"Almost everything points to {favorite}. ", "type": "dominant_favorite"}
        if dog_adv:
            x_factor["detail"] += f"The only crack: {underdog}'s {dog_adv[0]['attr']} ({dog_adv[0]['dog_val']}) is actually better."
        else:
            x_factor["detail"] += f"{favorite} holds advantages across virtually every dimension."
    else:
        x_factor = {"title": "Competitive match — here's the tipping point", "detail": "", "type": "competitive"}
        if surf_diff > 3:
            x_factor["detail"] = f"The {surface} surface slightly favors {better_surf}. In a tight match, surface comfort could be the decider."
        else:
            x_factor["detail"] = f"Remarkably evenly matched on {surface}. Mental toughness in the big moments will separate the winner."

    # Sort and take top 3
    wo = {"high": 0, "medium": 1, "low": 2}
    reasons.sort(key=lambda r: wo.get(r.get("weight", "low"), 2))

    # Predicted score
    if fav_prob > 0.72:
        ps, sd = "Straight sets (2-0)", f"{favorite} likely wins in two sets."
    elif fav_prob > 0.58:
        ps, sd = "Three sets likely (2-1)", f"Expect a competitive match. {underdog} should take a set."
    else:
        ps, sd = "Could go either way (2-1)", f"Going the distance. Both have legitimate paths to winning."

    # ── Upset Risk Score (0-100) ──
    base_risk = dog_prob * 100
    adjustments = 0

    # 1. Close Elo = higher risk
    if elo_diff < 50:
        adjustments += 15
    elif elo_diff < 100:
        adjustments += 10
    elif elo_diff < 200:
        adjustments += 5

    # 2. H2H favors underdog
    h2h_key_ur = tuple(sorted([p1, p2]))
    h2h_entry_ur = engine.h2h.get(h2h_key_ur)
    if h2h_entry_ur:
        dog_h2h = int(h2h_entry_ur['wins'].get(underdog, 0))
        fav_h2h = int(h2h_entry_ur['wins'].get(favorite, 0))
        if dog_h2h + fav_h2h > 0:
            if dog_h2h > fav_h2h:
                adjustments += 12
            elif dog_h2h == fav_h2h:
                adjustments += 6

    # 3. Underdog has better form
    f3_dog = float(engine.player_form.get(underdog, {}).get("form_3", 0.5))
    f3_fav = float(engine.player_form.get(favorite, {}).get("form_3", 0.5))
    if f3_dog > f3_fav + 0.2:
        adjustments += 8
    elif f3_dog > f3_fav:
        adjustments += 4

    # 4. Surface favors underdog
    dog_surf = float(surf_r.get(underdog, 80))
    fav_surf = float(surf_r.get(favorite, 80))
    if dog_surf > fav_surf:
        adjustments += 10

    # 5. Attribute advantages for underdog
    if len(dog_adv) >= 2:
        adjustments += 8
    elif len(dog_adv) >= 1:
        adjustments += 4

    upset_risk = min(100, int(base_risk * 1.5 + adjustments))

    if upset_risk >= 75:
        risk_label = "High upset potential"
        risk_detail = f"{underdog} has multiple edges that could flip this match."
    elif upset_risk >= 50:
        risk_label = "Moderate upset risk"
        risk_detail = f"{underdog} has real paths to winning — don't sleep on this one."
    elif upset_risk >= 30:
        risk_label = "Low but possible"
        risk_detail = f"{favorite} is clearly favored, but {underdog} isn't helpless."
    else:
        risk_label = "Heavy favorite"
        risk_detail = f"{favorite} dominates across nearly every dimension."

    if dog_adv:
        best = dog_adv[0]
        risk_detail += f" Watch for {underdog}'s {best['attr']}."

    return {
        "available": True, "player1": p1, "player2": p2, "surface": surface,
        "p1_win_prob": float(p1_prob), "p2_win_prob": float(p2_prob),
        "favorite": favorite, "underdog": underdog,
        "fav_prob": float(fav_prob), "dog_prob": float(dog_prob),
        "predicted_score": ps, "score_detail": sd,
        "reasons": reasons[:3], "x_factor": x_factor,
        "upset_risk": {
            "score": int(upset_risk),
            "label": risk_label,
            "detail": risk_detail,
        },
        "dog_advantages": [{"attr": a["attr"], "dog_val": int(a["dog_val"]), "fav_val": int(a["fav_val"])} for a in dog_adv[:3]],
        "fav_advantages": [{"attr": a["attr"], "fav_val": int(a["fav_val"]), "dog_val": int(a["dog_val"])} for a in fav_adv[:3]],
        "cards": {
            "p1": {"name": p1, "overall": float(card1.get("overall", 0)), "tier": card1.get("tier", ""), "elo": float(elo1)},
            "p2": {"name": p2, "overall": float(card2.get("overall", 0)), "tier": card2.get("tier", ""), "elo": float(elo2)}
        }
    }


# ─────────────────────────────────────────────────────────────
# Percentile Outliers — "What Makes Them Different"
# ─────────────────────────────────────────────────────────────

_PERCENTILE_PATH = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'percentile_rankings.json'
_percentile_cache: dict = {}

_STAT_LABELS = {
    "tiebreak_win_rate": "Tiebreak Win Rate",
    "deciding_set_wr": "Deciding Set Win Rate",
    "three_set_wr": "3-Set Match Win Rate",
    "vs_top10_wr": "vs Top-10 Win Rate",
    "vs_top20_wr": "vs Top-20 Win Rate",
    "comeback_rate": "Comeback Rate",
    "first_set_winner_conv": "First-Set Conversion",
    "bagels_per_match": "Bagels Delivered per Match",
    "bagels_conceded_per_match": "Bagels Conceded per Match",
    "hold_pct": "Hold %",
    "break_pct": "Break %",
    "bp_save_pct": "Break Point Save %",
    "bp_convert_pct": "Break Point Convert %",
    "first_serve_win_pct": "1st Serve Win %",
    "second_serve_win_pct": "2nd Serve Win %",
    "aces_per_match": "Aces per Match",
    "df_per_match": "Double Faults per Match",
}


def _get_percentiles() -> dict:
    global _percentile_cache
    if not _percentile_cache and _PERCENTILE_PATH.exists():
        try:
            _percentile_cache = json.loads(_PERCENTILE_PATH.read_text())
        except Exception as e:
            logger.warning(f"Failed to load percentile_rankings.json: {e}")
    return _percentile_cache


def _outlier_narrative(stat: str, val: float, percentile: float, rank: int,
                       total: int, sample_size: int, direction: str) -> str:
    label = _STAT_LABELS.get(stat, stat)
    pct_label = f"{int(percentile)}th percentile"
    if direction == "top":
        if rank == 1:
            return f"#1 of {total} qualifying players in {label} ({val:.1f}, n={sample_size})."
        if rank <= 5:
            return f"Top {rank} all-time in {label} ({val:.1f}, {pct_label}, n={sample_size})."
        return f"{pct_label} in {label} ({val:.1f}, ranked #{rank} of {total}, n={sample_size})."
    return f"Bottom {int(100 - percentile)}% in {label} ({val:.1f}, ranked #{rank} of {total}). A genuine weakness."


@app.get("/player/{name}/outliers")
def player_outliers(name: str):
    """Return the 5 most-extreme percentile rankings for this player.
    Stats above 90th percentile (top tier) or below 10th (genuine weakness)
    are surfaced. Sorted by abs(percentile - 50) descending."""
    engine = _get_engine()
    canonical = engine.find_player(name)
    if not canonical:
        return {"available": False, "reason": "Player not found"}

    pcts = _get_percentiles()
    if not pcts:
        return {"available": False, "reason": "Percentile data unavailable"}

    entry = pcts.get(canonical)
    if not entry:
        return {
            "available": False,
            "player": canonical,
            "reason": "Not enough match history for percentile analysis (need 20+ matches)",
        }

    candidates = []
    for stat, e in entry.items():
        pct = e.get("percentile")
        if pct is None:
            continue
        if pct >= 90:
            direction = "top"
        elif pct <= 10:
            direction = "bottom"
        else:
            continue
        candidates.append({
            "stat": stat,
            "label": _STAT_LABELS.get(stat, stat),
            "value": float(e["value"]),
            "percentile": float(pct),
            "rank": int(e["rank"]) if e.get("rank") else None,
            "total_qualifying": int(e["total_qualifying"]),
            "sample_size": int(e["sample_size"]),
            "direction": direction,
            "narrative": _outlier_narrative(
                stat, float(e["value"]), float(pct),
                int(e["rank"]) if e.get("rank") else 0,
                int(e["total_qualifying"]),
                int(e["sample_size"]),
                direction,
            ),
        })

    candidates.sort(key=lambda x: abs(x["percentile"] - 50), reverse=True)
    out = candidates[:5]

    return {
        "available": len(out) > 0,
        "player": canonical,
        "outliers": out,
    }


# ─────────────────────────────────────────────────────────────
# Surface DNA Profile
# ─────────────────────────────────────────────────────────────

@app.get("/player/{name}/surface-dna")
async def player_surface_dna(name: str):
    """Per-surface identity analysis — how a player's game changes across surfaces."""
    try:
        engine = _get_engine()
        matched = engine.find_player(name)
        if not matched:
            return {"available": False, "reason": "Player not found"}

        card = engine.get_player_card(matched, "hard")
        if not card:
            return {"available": False, "reason": "Player data unavailable"}

        surfaces_data = card.get("surfaces") or {}
        attributes = card.get("attributes") or {}
        overall = float(card.get("overall") or 0)

        # Filter out None surface values
        surfaces_data = {k: v for k, v in surfaces_data.items() if v is not None}
        if not surfaces_data:
            return {"available": False, "reason": "No surface data available"}

        surface_names = {"hard": "Hard Court", "clay": "Clay Court", "grass": "Grass Court"}
        surface_colors = {"hard": "#4A90D9", "clay": "#D4724E", "grass": "#5AA469"}

        profiles = {}
        best_surface = max(surfaces_data.items(), key=lambda x: float(x[1]))
        worst_surface = min(surfaces_data.items(), key=lambda x: float(x[1]))
        last_name = matched.split()[-1] if len(matched.split()) > 1 else matched

        for surf, rating in surfaces_data.items():
            rating = float(rating)
            diff_from_overall = rating - overall

            if diff_from_overall > 3:
                identity = "thrives"
                narrative = f"This is where {last_name} elevates. "
            elif diff_from_overall > 0:
                identity = "comfortable"
                narrative = f"A solid surface that suits {last_name}'s game. "
            elif diff_from_overall > -3:
                identity = "neutral"
                narrative = "Neither an advantage nor a liability. "
            else:
                identity = "vulnerable"
                narrative = "A surface that exposes weaknesses. "

            def _attr(name):
                v = attributes.get(name)
                return float(v) if v is not None else 50.0

            if surf == "clay":
                endurance = _attr("endurance")
                groundstroke = _attr("groundstroke")
                if endurance > 80:
                    narrative += f"High endurance ({int(endurance)}) helps grind through long clay rallies. "
                if groundstroke > 75:
                    narrative += f"Strong groundstrokes ({int(groundstroke)}) provide the heavy topspin clay demands."
                elif groundstroke < 55:
                    narrative += f"Groundstroke rating ({int(groundstroke)}) may struggle against clay-court baseliners."
            elif surf == "grass":
                serve = _attr("serve")
                volley = _attr("volley")
                if serve > 75:
                    narrative += f"Big serve ({int(serve)}) translates well to fast grass conditions. "
                if volley > 60:
                    narrative += f"Net skills ({int(volley)}) allow effective serve-and-volley tactics."
                elif volley < 40:
                    narrative += f"Limited net game ({int(volley)}) means relying on baseline play even on grass."
            elif surf == "hard":
                mental = _attr("mental")
                clutch = _attr("clutch")
                serve = _attr("serve")
                if mental > 75 and clutch > 75:
                    narrative += f"Mental strength ({int(mental)}) and clutch play ({int(clutch)}) thrive in hard-court pressure points."
                elif serve > 70:
                    narrative += f"Serve ({int(serve)}) anchors the game on the sport's most common surface."

            profiles[surf] = {
                "surface": surf,
                "surface_name": surface_names.get(surf, surf),
                "rating": float(rating),
                "diff_from_overall": round(float(diff_from_overall), 1),
                "identity": identity,
                "narrative": narrative.strip(),
                "color": surface_colors.get(surf, "#A8A9AD"),
            }

        spread = float(best_surface[1]) - float(worst_surface[1])
        if spread < 3:
            dna_type = "All-Court"
            dna_summary = f"{matched} performs consistently across all surfaces. No clear weakness to exploit, no standout surface to target."
        elif best_surface[0] == "clay":
            dna_type = "Clay Specialist"
            dna_summary = f"{matched}'s game is built for clay — patience, topspin, and endurance define the identity. Other surfaces require adaptation."
        elif best_surface[0] == "grass":
            dna_type = "Grass Specialist"
            dna_summary = f"{matched} comes alive on grass. The fast, low-bouncing conditions reward the aggressive, serve-dominant style."
        elif best_surface[0] == "hard":
            dna_type = "Hard Court Specialist"
            dna_summary = f"{matched} is most dangerous on hard courts — the neutral surface rewards the complete, well-rounded game."
        else:
            dna_type = "Balanced"
            dna_summary = f"{matched} shows reasonable comfort across surfaces."

        return {
            "available": True,
            "player": matched,
            "dna_type": dna_type,
            "dna_summary": dna_summary,
            "overall_rating": float(overall),
            "best_surface": {"surface": best_surface[0], "rating": float(best_surface[1])},
            "worst_surface": {"surface": worst_surface[0], "rating": float(worst_surface[1])},
            "spread": round(float(spread), 1),
            "profiles": profiles,
        }
    except Exception as e:
        logger.exception("surface-dna error for %s", name)
        return {"available": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────
# Match Narrative Generator
# ─────────────────────────────────────────────────────────────

@app.post("/api/match-narrative")
async def match_narrative(request: Request):
    """Template-based analyst-style match narrative. Reads like ESPN commentary."""
    body = await request.json()
    p1_raw = body.get("player1", "")
    p2_raw = body.get("player2", "")
    surface = body.get("surface", "hard")

    engine = _get_engine()
    p1 = engine.find_player(p1_raw)
    p2 = engine.find_player(p2_raw)
    if not p1 or not p2:
        return {"available": False, "reason": "Player not found"}

    try:
        pred = engine.predict(p1, p2, surface)
    except Exception:
        return {"available": False, "reason": "Prediction failed"}

    p1_prob = float(pred.get("player1_win_prob", 0.5))
    card1 = engine.get_player_card(p1, surface)
    card2 = engine.get_player_card(p2, surface)
    if not card1 or not card2:
        return {"available": False, "reason": "Player data unavailable"}

    p1_last = p1.split()[-1]
    p2_last = p2.split()[-1]
    elo1 = float(card1.get("elo", 1500))
    elo2 = float(card2.get("elo", 1500))
    elo_diff = abs(elo1 - elo2)
    attrs1 = card1.get("attributes", {})
    attrs2 = card2.get("attributes", {})
    surfs1 = card1.get("surfaces", {})
    surfs2 = card2.get("surfaces", {})

    f3_1 = float(engine.player_form.get(p1, {}).get("form_3", 0.5))
    f3_2 = float(engine.player_form.get(p2, {}).get("form_3", 0.5))

    favorite = p1 if p1_prob >= 0.5 else p2
    underdog = p2 if p1_prob >= 0.5 else p1
    fav_last = favorite.split()[-1]
    dog_last = underdog.split()[-1]
    fav_prob = max(p1_prob, 1 - p1_prob)

    fav_attrs = attrs1 if favorite == p1 else attrs2
    dog_attrs = attrs2 if favorite == p1 else attrs1
    fav_surfs = surfs1 if favorite == p1 else surfs2
    dog_surfs = surfs2 if favorite == p1 else surfs1
    fav_form = f3_1 if favorite == p1 else f3_2
    dog_form = f3_2 if favorite == p1 else f3_1

    surface_name = {"hard": "hard court", "clay": "clay", "grass": "grass"}.get(surface, surface)

    paragraphs = []

    # PARAGRAPH 1: The Setup
    if fav_prob > 0.70:
        opener = f"This is {fav_last}'s match to lose."
        if elo_diff > 200:
            opener += f" A {int(elo_diff)}-point Elo gap tells you everything about the class difference here."
        else:
            opener += f" The model gives {fav_last} a commanding {fav_prob*100:.0f}% edge on {surface_name}."
    elif fav_prob > 0.55:
        opener = f"Slight edge to {fav_last}, but this is far from a foregone conclusion."
        opener += f" At {fav_prob*100:.0f}-{(1-fav_prob)*100:.0f}, the margins are thin enough that one service break could flip the script."
    else:
        opener = f"Throw the rankings out — this is a genuine coin-flip."
        opener += f" {p1_last} and {p2_last} are separated by just {int(elo_diff)} Elo points, and the model sees it at {p1_prob*100:.0f}-{(1-p1_prob)*100:.0f}."
    paragraphs.append(opener)

    # PARAGRAPH 2: The Key Matchup Dynamic
    biggest_gap = None
    biggest_gap_val = 0
    for attr in ["serve", "groundstroke", "endurance", "mental", "clutch"]:
        v1 = float(fav_attrs.get(attr) or 50)
        v2 = float(dog_attrs.get(attr) or 50)
        gap = abs(v1 - v2)
        if gap > biggest_gap_val:
            biggest_gap_val = gap
            biggest_gap = {"attr": attr, "fav_val": int(v1), "dog_val": int(v2), "favors": "favorite" if v1 > v2 else "underdog"}

    if biggest_gap and biggest_gap_val > 10:
        attr_name = biggest_gap["attr"]
        if biggest_gap["favors"] == "favorite":
            dynamic = f"The matchup hinges on {attr_name}. {fav_last} holds a clear {biggest_gap['fav_val']}-to-{biggest_gap['dog_val']} advantage there"
            if attr_name == "serve":
                dynamic += " — expect free points on the first serve and pressure in return games."
            elif attr_name == "endurance":
                dynamic += " — if this goes three sets, the fitness edge becomes decisive."
            elif attr_name == "mental":
                dynamic += " — in the big moments, tiebreaks and break points, that mental edge separates the professionals from the pretenders."
            elif attr_name == "groundstroke":
                dynamic += f" — on {surface_name}, that baseline superiority should dictate rallies."
            elif attr_name == "clutch":
                dynamic += " — when the pressure peaks, clutch players find a way. That's the difference-maker."
            else:
                dynamic += "."
        else:
            dynamic = f"Here's what makes this interesting: {dog_last} actually outscores {fav_last} in {attr_name}, {biggest_gap['dog_val']} to {biggest_gap['fav_val']}."
            dynamic += f" If {dog_last} can steer the match into a {attr_name}-heavy battle, the upset window cracks open."
    else:
        dynamic = "These two mirror each other across the stat sheet — no clear technical mismatch to exploit."
        dynamic += " It comes down to who executes better on the day."
    paragraphs.append(dynamic)

    # PARAGRAPH 3: The Surface Factor
    fav_surf_rating = float(fav_surfs.get(surface, 80))
    dog_surf_rating = float(dog_surfs.get(surface, 80))
    sf_diff = fav_surf_rating - dog_surf_rating

    if abs(sf_diff) > 5:
        better = fav_last if sf_diff > 0 else dog_last
        worse = dog_last if sf_diff > 0 else fav_last
        surface_para = f"The {surface_name} surface tilts this. {better} rates {max(fav_surf_rating, dog_surf_rating):.1f} here versus {min(fav_surf_rating, dog_surf_rating):.1f} for {worse}."
        if surface == "clay":
            surface_para += f" Clay rewards patience and topspin — {better}'s game translates better to the slow, high-bouncing conditions."
        elif surface == "grass":
            surface_para += f" Grass is about first-strike tennis — serve, slice, get to the net. {better} is more equipped for that style."
        else:
            surface_para += f" Hard court is the great equalizer in tennis, but even here, the numbers favor {better}."
    elif abs(sf_diff) < 2:
        surface_para = f"Surface is a non-factor — both players rate within {abs(sf_diff):.1f} points on {surface_name}. This one will be decided by execution, not conditions."
    else:
        surface_para = f"Slight {surface_name} edge to {fav_last if sf_diff > 0 else dog_last}, but not enough to be a decisive factor."
    paragraphs.append(surface_para)

    # PARAGRAPH 4: The Prediction
    if fav_prob > 0.70:
        prediction = f"The call: {fav_last} in straight sets."
        prediction += f" The gap is too wide across too many dimensions for {dog_last} to overcome in a best-of-three."
        prediction += f" {dog_last} will have moments — maybe a break in the first set — but {fav_last} has the tools to reset and close."
    elif fav_prob > 0.58:
        prediction = f"The call: {fav_last} in three sets."
        prediction += f" {dog_last} has enough game to take a set, and the margins suggest this will be competitive throughout."
        prediction += f" But {fav_last}'s edge in the key areas should prove just enough to close it out."
    else:
        prediction = f"The call: pick'em, but lean {fav_last}."
        prediction += " This is a match where form on the day matters more than any stat line."
        if fav_form > dog_form + 0.15:
            prediction += f" {fav_last}'s recent form ({int(fav_form*3)}-of-3 recent wins) provides the tiebreaker."
        elif dog_form > fav_form + 0.15:
            prediction += f" But watch out — {dog_last} is actually in better recent form. This could easily go the other way."
        else:
            prediction += " Both are in similar form. Expect a war."
    paragraphs.append(prediction)

    return {
        "available": True,
        "player1": p1,
        "player2": p2,
        "surface": surface,
        "narrative": paragraphs,
        "favorite": favorite,
        "underdog": underdog,
        "fav_prob": float(fav_prob),
    }


_tournament_pred_cache: dict = {}

@app.get("/api/tournament-predictions")
def tournament_predictions():
    """
    Return favorites and dark horses for the live tournament from the ATP 2026
    calendar. Falls back to most-recent-finished if nothing is live, and to
    next-upcoming if nothing has finished. Surface taken from calendar.
    """
    # Pick target tournament from the calendar
    today = date.today()
    target = get_live_tournament(today) or get_just_finished(today) or get_next_upcoming(today)
    if not target:
        return {"available": False, "reason": "No tournament in calendar"}
    surface = (target.get("surface") or "hard").lower()
    tour_name = target.get("name", "")
    cache_key = f"{tour_name}:{surface}"
    if _tournament_pred_cache.get("_key") == cache_key:
        return _tournament_pred_cache

    import math as _math
    import csv as _csv

    engine = _get_engine()
    _latest = engine.latest_data_date
    _retire_days = 425

    # ── Load draw from supplemental CSV (fuzzy match by tournament name) ──
    _suppl_path = Path(__file__).parent.parent.parent / 'data' / 'processed' / 'supplemental_matches_2025_2026.csv'
    draw_canonical = set()
    draw_available = False

    # Token aliases for matching the calendar tournament name to supplemental rows
    _tname_lower = tour_name.lower()
    _aliases = [_tname_lower]
    if 'italian' in _tname_lower or 'rome' in _tname_lower:
        _aliases += ['italian', 'rome', 'internazionali']
    if 'madrid' in _tname_lower:
        _aliases += ['madrid']
    if 'monte carlo' in _tname_lower:
        _aliases += ['monte carlo']
    if 'indian wells' in _tname_lower:
        _aliases += ['indian wells', 'bnp']
    if 'miami' in _tname_lower:
        _aliases += ['miami']
    if 'cincinnati' in _tname_lower:
        _aliases += ['cincinnati']
    if 'canadian' in _tname_lower:
        _aliases += ['canadian', 'toronto']
    if _suppl_path.exists():
        _name_map = getattr(engine, '_supplemental_name_map', {})
        if not _name_map:
            from src.api.predict_engine import _build_supplemental_name_map
            _name_map = _build_supplemental_name_map(engine.player_names)
        try:
            import pandas as _pd
            _suppl_df = _pd.read_csv(_suppl_path)
            _suppl_df = _suppl_df.dropna(subset=['tourney_name'])
            _mask = False
            for _alias in _aliases:
                _mask = _mask | _suppl_df['tourney_name'].str.lower().str.contains(_alias, na=False)
            _draw_rows = _suppl_df[_mask] if _aliases else _suppl_df
            if len(_draw_rows) > 0:
                draw_available = True
                for _col in ['winner_name', 'loser_name']:
                    for _abbrev in _draw_rows[_col].dropna().unique():
                        _canon = _name_map.get(str(_abbrev).strip())
                        if _canon:
                            draw_canonical.add(_canon)
        except Exception as _e:
            logger.warning(f"Could not load draw from supplemental CSV: {_e}")

    # Collect active players with surface-specific ratings
    surface_key = surface if surface in ("hard", "clay", "grass") else "hard"
    active_surf = []
    _today_dt = datetime.now()
    for pname, surfaces_dict in engine.glicko.ratings.items():
        r_all = surfaces_dict.get('all')
        if r_all is None or r_all.last_match_date is None:
            continue
        days_since = (_today_dt - datetime.combine(r_all.last_match_date, datetime.min.time())).days
        if days_since > _retire_days and r_all.match_count > 20:
            continue  # retired
        r_surf = surfaces_dict.get(surface_key)
        if r_surf and r_surf.match_count >= 10:
            surf_mu = r_surf.mu
        else:
            surf_mu = r_all.mu
        active_surf.append((pname, surf_mu, r_all.mu))

    active_surf.sort(key=lambda x: x[1], reverse=True)
    # alias for the rest of the function which uses active_hard
    active_hard = active_surf

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
        "tournament": tour_name,
        "year": int(target["start"][:4]) if target.get("start") else None,
        "surface": surface_key.capitalize(),
        "category": target.get("category"),
        "draw_available": draw_available,
        "draw_size": len(draw_canonical) if draw_available else None,
        "favorites": favorites,
        "dark_horses": dark_horses,
        "_key": cache_key,
    }
    _tournament_pred_cache.clear()
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
