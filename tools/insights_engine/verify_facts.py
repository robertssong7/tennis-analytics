"""Fact verifier for the TennisIQ insights engine.

Re-fetches the API state and checks that every numeric claim in a candidate's
supporting_metrics still matches the live source within a small tolerance.
Binary pass/fail per candidate. Anything that fails is dropped; the run
records the reason for telemetry.
"""

from __future__ import annotations

import os
from typing import Any

import requests

API_BASE = os.environ.get(
    "TENNISIQ_API",
    "https://su7vqmgkbd.us-east-1.awsapprunner.com",
)

TIMEOUT = 25
TOLERANCE = 0.6  # FIFA-point slack to absorb refresh-time drift


def _get(path: str) -> Any:
    r = requests.get(API_BASE + path, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _safe_get(path: str, default: Any = None) -> Any:
    try:
        return _get(path)
    except Exception:
        return default


def _elo_to_fifa(mu: float | None) -> float | None:
    import math

    if mu is None:
        return None
    return 55.0 + 42.0 / (1.0 + math.exp(-0.004 * (mu - 1750.0)))


def _within(a: float | None, b: float | None, tol: float = TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _verify_surface(c: dict) -> tuple[bool, str]:
    name = c["supporting_metrics"]["player"]
    card = _safe_get(f"/player/{requests.utils.quote(name, safe='')}")
    if not card:
        return False, "player card unreachable"
    live = {
        "hard": _elo_to_fifa(card.get("elo_hard")),
        "clay": _elo_to_fifa(card.get("elo_clay")),
        "grass": _elo_to_fifa(card.get("elo_grass")),
    }
    claimed = c["supporting_metrics"]["ratings_by_surface"]
    for k, v in claimed.items():
        if not _within(v, live.get(k)):
            return False, f"{k} rating drift: claimed {v}, live {live.get(k)}"
    if c["supporting_metrics"]["top_surface"] not in live:
        return False, "unknown top_surface"
    gap = float(c["supporting_metrics"]["gap"])
    live_vals = [v for v in live.values() if v is not None]
    if not live_vals:
        return False, "no live surface ratings"
    live_gap = max(live_vals) - min(live_vals)
    if abs(live_gap - gap) > TOLERANCE * 2:
        return False, f"gap drift: claimed {gap}, live {round(live_gap,1)}"
    return True, "ok"


def _verify_form(c: dict) -> tuple[bool, str]:
    name = c["supporting_metrics"]["player"]
    card = _safe_get(f"/player/{requests.utils.quote(name, safe='')}")
    if not card:
        return False, "player card unreachable"
    base = card.get("base_rating")
    display = card.get("elo_display")
    form_mod = card.get("form_modifier")
    if base is None or display is None or form_mod is None:
        return False, "card missing rating fields"
    if not _within(base, c["supporting_metrics"]["base_rating"]):
        return False, "base rating drift"
    if not _within(display, c["supporting_metrics"]["display_rating"]):
        return False, "display rating drift"
    if not _within(form_mod, c["supporting_metrics"]["form_modifier"]):
        return False, "form modifier drift"
    return True, "ok"


def _verify_tournament(c: dict) -> tuple[bool, str]:
    live = _safe_get("/api/live-tournament", {})
    tourn = (live or {}).get("live") or {}
    if not tourn.get("tournament"):
        return False, "no live tournament"
    if tourn.get("tournament") != c["supporting_metrics"]["tournament"]:
        return False, "tournament name drift"
    if tourn.get("year") != c["supporting_metrics"]["year"]:
        return False, "tournament year drift"
    player = c["supporting_metrics"]["player"]
    results = tourn.get("results") or []
    wins = sum(1 for r in results if r.get("winner") == player)
    if wins < c["supporting_metrics"]["wins_in_tournament"]:
        return False, f"wins drift: claimed {c['supporting_metrics']['wins_in_tournament']}, live {wins}"
    return True, "ok"


_VERIFIERS = {
    "surface_specialists": _verify_surface,
    "form_reversals": _verify_form,
    "tournament_narrative": _verify_tournament,
}


def verify(candidate: dict) -> tuple[bool, str]:
    fn = _VERIFIERS.get(candidate.get("category", ""))
    if not fn:
        return False, f"unknown category {candidate.get('category')!r}"
    try:
        return fn(candidate)
    except Exception as e:  # noqa: BLE001 - explicit verifier failure path
        return False, f"verifier exception: {type(e).__name__}: {e}"


def verify_all(candidates: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    kept: list[dict] = []
    rejected: list[tuple[dict, str]] = []
    for c in candidates:
        ok, reason = verify(c)
        if ok:
            kept.append(c)
        else:
            rejected.append((c, reason))
    return kept, rejected
