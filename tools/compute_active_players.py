"""Compute the set of players currently active in any live ATP tournament.

Active = entered into a tournament whose date window includes today AND not
yet eliminated (no completed loss recorded in the latest scrape).

Reads `data/processed/live_tournament.json` (the existing daily-action
output, schema: {live, just_finished, next_upcoming}). Emits canonical
Sackmann/Glicko player names by mapping through the same short-name map
predict_engine builds.

Output: data/processed/active_players.json
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.predict_engine import _build_supplemental_name_map  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compute_active_players")

LIVE_PATH = PROJECT_ROOT / "data" / "processed" / "live_tournament.json"
GLICKO_PATH = PROJECT_ROOT / "data" / "processed" / "glicko2_state.pkl"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "active_players.json"

ROUND_ORDER = {"R128": 1, "R64": 2, "R32": 3, "R16": 4, "QF": 5, "SF": 6, "F": 7, "W": 8}


def _load_glicko_names() -> list:
    if not GLICKO_PATH.exists():
        return []
    with open(GLICKO_PATH, "rb") as f:
        state = pickle.load(f)
    return list(state.ratings.keys())


def _normalize_short_names(short_names: set) -> dict:
    """Map "Sinner J." → "Jannik Sinner" via the predict_engine helper.

    The helper reads from SUPPLEMENTAL_CSV to enumerate short names; we
    augment its mapping with any short names that came from the live feed
    but aren't in the supplement (they fall through unchanged).
    """
    glicko_names = _load_glicko_names()
    name_map = _build_supplemental_name_map(glicko_names)
    # Backfill any live-only short names by retrying the same heuristic.
    canonical_by_key: dict = {}
    for canonical in glicko_names:
        parts = canonical.split()
        if len(parts) < 2:
            continue
        first = parts[0]
        last = " ".join(parts[1:])
        canonical_by_key.setdefault((last.lower(), first[0].lower()), []).append(canonical)
    for sname in short_names:
        if sname in name_map:
            continue
        parts = sname.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        last_part = parts[0].strip()
        initial_part = parts[1].strip().rstrip(".")
        if not initial_part:
            continue
        candidates = canonical_by_key.get((last_part.lower(), initial_part[0].lower()), [])
        if candidates:
            name_map[sname] = candidates[0]
    return name_map


def _parse_date_range(d: str | None) -> tuple | None:
    """Best-effort parse of dates strings like 'May 6-17, 2026' or
    'Apr 22 - May 4, 2026'. Returns (start_date, end_date) or None.
    """
    if not d:
        return None
    try:
        from datetime import date
        # Normalize separators
        norm = d.replace("–", "-").replace("—", "-")
        # Split year
        if "," in norm:
            body, year_str = norm.rsplit(",", 1)
            year = int(year_str.strip())
        else:
            return None
        body = body.strip()
        # Two formats:
        #   "Apr 22 - May 4"  — month appears in both halves
        #   "May 6-17"        — month only in first half
        if " - " in body:
            left, right = body.split(" - ", 1)
        else:
            # "May 6-17" → split on the dash inside the day range
            left, right = body.split("-", 1)
            # If left is "May 6" and right is "17", paste month onto right
            left_parts = left.strip().split()
            if len(left_parts) == 2 and right.strip().isdigit():
                right = f"{left_parts[0]} {right.strip()}"
        start = datetime.strptime(f"{left.strip()} {year}", "%b %d %Y").date()
        end = datetime.strptime(f"{right.strip()} {year}", "%b %d %Y").date()
        return start, end
    except Exception as exc:
        log.debug(f"date parse failed for '{d}': {exc}")
        return None


def _eliminated_set(results: list) -> set:
    """A player is eliminated if they appear as a loser in any completed match."""
    return {m["loser"] for m in results if m.get("loser")}


def _entrants_set(results: list) -> set:
    out = set()
    for m in results:
        if m.get("winner"):
            out.add(m["winner"])
        if m.get("loser"):
            out.add(m["loser"])
    return out


def _current_round(results: list) -> str:
    if not results:
        return "?"
    rounds = [m.get("round", "?") for m in results if m.get("round")]
    if not rounds:
        return "?"
    return max(rounds, key=lambda r: ROUND_ORDER.get(r, 0))


def compute_active_players() -> dict:
    if not LIVE_PATH.exists():
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "active_tournament_count": 0,
            "tournaments": [],
            "all_active_players": [],
            "active_player_count": 0,
            "warning": "live_tournament.json not found",
        }

    with open(LIVE_PATH) as f:
        feed = json.load(f)

    today = datetime.now(timezone.utc).date()
    active = []
    all_short_names: set = set()
    candidate_slots = []
    live_block = feed.get("live") or {}
    if live_block and live_block.get("data_available"):
        candidate_slots.append(live_block)

    for t in candidate_slots:
        rng = _parse_date_range(t.get("dates"))
        if rng is not None:
            start, end = rng
            if not (start <= today <= end):
                log.info(f"Skipping {t.get('tournament')}: date {start}-{end} excludes today {today}")
                continue
        results = t.get("results", [])
        if not results:
            continue
        for m in results:
            for k in ("winner", "loser"):
                if m.get(k):
                    all_short_names.add(m[k])

    # Build short-to-canonical map once, after we've seen all short names.
    name_map = _normalize_short_names(all_short_names)

    canonical_active: set = set()
    for t in candidate_slots:
        rng = _parse_date_range(t.get("dates"))
        if rng is not None:
            start, end = rng
            if not (start <= today <= end):
                continue
        results = t.get("results", [])
        if not results:
            continue
        eliminated = _eliminated_set(results)
        entrants = _entrants_set(results)
        still_alive_short = sorted(entrants - eliminated)
        canonical_alive = sorted(
            {name_map.get(s, s) for s in still_alive_short}
        )
        canonical_active.update(canonical_alive)

        active.append({
            "name": t.get("tournament"),
            "level": t.get("level"),
            "surface": t.get("surface"),
            "start": str(rng[0]) if rng else None,
            "end": str(rng[1]) if rng else None,
            "round": _current_round(results),
            "active_players_short": still_alive_short,
            "active_players": canonical_alive,
            "matches_recorded": len(results),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_tournament_count": len(active),
        "tournaments": active,
        "all_active_players": sorted(canonical_active),
        "active_player_count": len(canonical_active),
        "name_map_size": len(name_map),
    }


if __name__ == "__main__":
    result = compute_active_players()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(
        f"Wrote {OUTPUT_PATH}: {result['active_player_count']} active players "
        f"across {result['active_tournament_count']} tournaments"
    )
