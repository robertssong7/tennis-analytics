"""Candidate generator for the TennisIQ insights engine.

Pulls data from the deployed API and produces structured candidates across
three categories for the session-17 v0:
    surface_specialists  - large per-surface FIFA gap on an active player
    form_reversals       - form modifier crossed a tier boundary
    tournament_narrative - storyline from the live tournament

Each candidate has:
    category, subject_players, supporting_metrics, raw_text_seed

The raw_text_seed is a plain factual sentence built from real numbers; the
Haiku editor will re-voice it without changing the underlying facts. Every
number that appears in the seed must be present in supporting_metrics so the
fact verifier can cross-check.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

API_BASE = os.environ.get(
    "TENNISIQ_API",
    "https://su7vqmgkbd.us-east-1.awsapprunner.com",
)

TIMEOUT = 25


def _get(path: str) -> Any:
    r = requests.get(API_BASE + path, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _safe_get(path: str, default: Any = None) -> Any:
    try:
        return _get(path)
    except Exception:
        return default


def _tier_for(fifa: float) -> str:
    if fifa is None:
        return "unknown"
    if fifa >= 91:
        return "legendary"
    if fifa >= 80:
        return "gold"
    if fifa >= 69:
        return "silver"
    return "bronze"


def _elo_to_fifa(mu: float) -> float | None:
    """Convert a Glicko mu to the FIFA-scale base rating used elsewhere.

    Mirrors the formula in predict_engine._compute_card; the +form modifier is
    applied at the engine level and is not relevant for per-surface ratings.
    """
    import math

    if mu is None:
        return None
    return 55.0 + 42.0 / (1.0 + math.exp(-0.004 * (mu - 1750.0)))


def _gather_active_players() -> list[str]:
    """Union of full-name players from active list, tournament favorites,
    and key matchups. Keeps the pool focused on tour-relevant players
    without enumerating the whole Glicko index."""
    pool: list[str] = []
    seen: set[str] = set()

    def _add(n: str | None) -> None:
        if not n:
            return
        if n in seen:
            return
        seen.add(n)
        pool.append(n)

    for n in (_safe_get("/api/active-players", {}) or {}).get("active_players") or []:
        _add(n)
    for f in (_safe_get("/api/tournament-predictions", {}) or {}).get("favorites") or []:
        _add(f.get("player"))
    for m in (_safe_get("/api/key-matchups-live", {}) or {}).get("matchups") or []:
        _add(m.get("player1"))
        _add(m.get("player2"))
    return pool


def _gather_player(name: str) -> dict | None:
    return _safe_get(f"/player/{requests.utils.quote(name, safe='')}")


def _surface_gap_candidates(active: list[str]) -> list[dict]:
    """Players whose per-surface FIFA spread exceeds 8 points."""
    out: list[dict] = []
    for name in active:
        card = _gather_player(name)
        if not card:
            continue

        elo_hard = card.get("elo_hard")
        elo_clay = card.get("elo_clay")
        elo_grass = card.get("elo_grass")
        fifa_hard = _elo_to_fifa(elo_hard)
        fifa_clay = _elo_to_fifa(elo_clay)
        fifa_grass = _elo_to_fifa(elo_grass)
        per_surface = {
            "hard": round(fifa_hard, 1) if fifa_hard is not None else None,
            "clay": round(fifa_clay, 1) if fifa_clay is not None else None,
            "grass": round(fifa_grass, 1) if fifa_grass is not None else None,
        }
        usable = [(k, v) for k, v in per_surface.items() if v is not None]
        if len(usable) < 2:
            continue
        usable.sort(key=lambda kv: kv[1], reverse=True)
        top_surface, top_val = usable[0]
        bot_surface, bot_val = usable[-1]
        gap = round(top_val - bot_val, 1)
        if gap <= 8.0:
            continue

        out.append({
            "category": "surface_specialists",
            "subject_players": [name],
            "supporting_metrics": {
                "player": name,
                "top_surface": top_surface,
                "top_rating": top_val,
                "bottom_surface": bot_surface,
                "bottom_rating": bot_val,
                "gap": gap,
                "ratings_by_surface": per_surface,
            },
            "raw_text_seed": (
                f"{name} is rated {top_val} on {top_surface} and only "
                f"{bot_val} on {bot_surface}, a {gap}-point spread "
                f"that flags him as a surface specialist."
            ),
        })
    return out


def _form_reversal_candidates(active: list[str]) -> list[dict]:
    """Players whose live form modifier has pushed their display rating
    across a tier boundary (legendary >=91, gold >=80, silver >=69)."""
    out: list[dict] = []
    boundaries = [69, 80, 91]
    for name in active:
        card = _gather_player(name)
        if not card:
            continue
        base = card.get("base_rating")
        display = card.get("elo_display")
        form_mod = card.get("form_modifier")
        if base is None or display is None or form_mod is None:
            continue
        if card.get("is_retired"):
            continue
        if abs(form_mod) < 1.0:
            continue
        crossed: tuple[float, str, str] | None = None
        for b in boundaries:
            below = min(base, display)
            above = max(base, display)
            if below < b <= above:
                base_tier = _tier_for(base)
                live_tier = _tier_for(display)
                direction = "up" if display > base else "down"
                crossed = (b, base_tier, live_tier)
                break
        if not crossed:
            continue
        boundary, base_tier, live_tier = crossed
        direction = "up" if display > base else "down"
        out.append({
            "category": "form_reversals",
            "subject_players": [name],
            "supporting_metrics": {
                "player": name,
                "base_rating": round(float(base), 1),
                "display_rating": round(float(display), 1),
                "form_modifier": round(float(form_mod), 1),
                "base_tier": base_tier,
                "live_tier": live_tier,
                "direction": direction,
                "boundary": boundary,
            },
            "raw_text_seed": (
                f"{name}'s form has nudged his TennisIQ rating from "
                f"{round(float(base),1)} to {round(float(display),1)}, "
                f"flipping his tier from {base_tier} to {live_tier}."
            ),
        })
    return out


def _tournament_narrative_candidates() -> list[dict]:
    """Storylines from the live tournament: deep run by a lower-ranked
    player, or an unexpected favorite struggling. Limit to one candidate
    per cron run to avoid flooding."""
    live = _safe_get("/api/live-tournament", {})
    tourn = (live or {}).get("live")
    if not tourn or not tourn.get("tournament"):
        return []
    results = tourn.get("results") or []
    if not results:
        return []
    name = tourn.get("tournament")
    surface = tourn.get("surface") or ""
    year = tourn.get("year")

    # Find players who advanced through at least 2 rounds in this tournament
    # using the results log.
    winner_rounds: dict[str, list[str]] = {}
    for r in results:
        w = r.get("winner")
        rd = r.get("round")
        if not w or not rd:
            continue
        winner_rounds.setdefault(w, []).append(rd)

    # Dedup rounds_won per player, keep insertion order.
    deduped: dict[str, list[str]] = {}
    for p, rds in winner_rounds.items():
        out_rounds: list[str] = []
        seen_rd: set[str] = set()
        for rd in rds:
            if rd in seen_rd:
                continue
            seen_rd.add(rd)
            out_rounds.append(rd)
        deduped[p] = out_rounds

    deep_runners = [(p, rds) for p, rds in deduped.items() if len(rds) >= 2]
    if not deep_runners:
        return []

    # Pick the player with the longest run; tie-break by name for determinism.
    deep_runners.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    player, rounds_won = deep_runners[0]
    wins = len(rounds_won)

    return [{
        "category": "tournament_narrative",
        "subject_players": [player],
        "supporting_metrics": {
            "tournament": name,
            "year": year,
            "surface": surface,
            "player": player,
            "wins_in_tournament": wins,
            "rounds_won": rounds_won,
        },
        "raw_text_seed": (
            f"{player} has won {wins} matches at the {name} {year}, "
            f"advancing on {surface.lower() if surface else 'tour'} through "
            f"{', '.join(rounds_won[:3])}."
        ),
    }]


def generate() -> list[dict]:
    active = _gather_active_players()
    cands: list[dict] = []
    cands += _surface_gap_candidates(active)
    cands += _form_reversal_candidates(active)
    # cands += _tournament_narrative_candidates()  # disabled until live data source wired in (Session 18)
    return cands


if __name__ == "__main__":
    import sys
    out = generate()
    json.dump(out, sys.stdout, indent=2)
    print(f"\n# {len(out)} candidates generated", file=sys.stderr)
