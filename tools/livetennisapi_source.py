"""
Optional supplemental match source: Live Tennis API.

`refresh_supplement_data.py` currently has one source — the tennis-data.co.uk
yearly xlsx. When that download fails or lags behind the tour, the supplement
CSV (and therefore `/api/live-tournament`) goes stale with no fallback.

This module is a SECOND, OPTIONAL source that fills the exact same schema
(tourney_name, tourney_date, surface, tourney_level, round, winner_name,
loser_name, winner_rank, loser_rank, score, best_of, court, location), so the
existing concat/dedupe/sort in `refresh_supplement_data.main()` absorbs it
without any other change.

It is inert unless `LIVETENNISAPI_KEY` is set: `fetch_supplement_rows()`
returns None and nothing is requested. tennis-data.co.uk remains the primary
source and is merged first.

Environment:
    LIVETENNISAPI_KEY       API key. Absent  →  this source is skipped entirely.
    LIVETENNISAPI_BASE_URL  Override the base URL (default: public v1).
    LIVETENNISAPI_TOUR      Tour filter (default "atp", matching the existing
                            tennis-data.co.uk men's file). Empty = all tours.
    LIVETENNISAPI_MAX_PAGES Page budget (default 10 × 200 = 2000 matches).

Fields we deliberately leave blank rather than guess:
    tourney_level  The API does not publish a tournament tier on the live-side
                   match object. `_normalize()` in the primary source fills
                   this from tennis-data.co.uk's "Series"; we leave it "" so a
                   fabricated tier never enters the CSV.
    location       No venue field is published.

Both columns are already tolerant of blanks — nothing downstream requires them.

Disclosure: Live Tennis API is operated by the author of this pull request.
It is offered here strictly as an optional fallback, not a replacement.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.livetennisapi.com/api/public/v1"
DEFAULT_TOUR = "atp"
PAGE_SIZE = 200  # the API's documented maximum
DEFAULT_MAX_PAGES = 10
TIMEOUT = 30

# The supplement CSV column order, kept identical to `_normalize()` so the two
# sources concat cleanly.
SUPPLEMENT_COLUMNS = [
    "tourney_name",
    "tourney_date",
    "surface",
    "tourney_level",
    "round",
    "winner_name",
    "loser_name",
    "winner_rank",
    "loser_rank",
    "score",
    "best_of",
    "court",
    "location",
]

_BEST_OF = {"BO3": 3, "BO5": 5}


def _score_string(games: Any, winner: int) -> str:
    """Render `score.games` as "6-3 6-4", winner's games first.

    `games` is [games_p1, games_p2], each a per-set list. A completed match can
    carry an EMPTY games array; when it does we return "" rather than invent a
    scoreline. Ragged or non-numeric entries are skipped, not padded.
    """
    if not isinstance(games, (list, tuple)) or len(games) < 2:
        return ""
    p1, p2 = games[0], games[1]
    if not isinstance(p1, (list, tuple)) or not isinstance(p2, (list, tuple)):
        return ""

    # Winner's games lead each set, matching tennis-data.co.uk's W{i}-L{i}.
    first, second = (p1, p2) if winner == 1 else (p2, p1)

    sets = []
    for w, l in zip(first, second):
        if isinstance(w, bool) or isinstance(l, bool):
            continue
        if not isinstance(w, int) or not isinstance(l, int):
            continue
        sets.append(f"{w}-{l}")
    return " ".join(sets)


def _match_date(match: dict) -> str:
    """`scheduled_time` (ISO-8601) → YYYYMMDD, or "" if absent/unparseable."""
    raw = match.get("scheduled_time")
    if not raw or not isinstance(raw, str):
        return ""
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y%m%d")


def _row_from_match(match: dict) -> Optional[dict]:
    """Map one Match object onto a supplement row, or None if unusable.

    Returns None — rather than a partial row — when the match cannot name a
    winner and a loser, since every downstream consumer keys on those.
    """
    if not isinstance(match, dict):
        return None

    # Doubles teams are not players in the Elo/attribute name space; the
    # existing tennis-data.co.uk source is singles-only. Keep it that way.
    if match.get("is_doubles"):
        return None

    if match.get("status") != "completed":
        return None

    # `winner` is populated on completed matches only, as 1 or 2. Anything else
    # (including null) means the result is not settled: skip, never infer.
    winner = match.get("winner")
    if winner not in (1, 2):
        return None

    players = match.get("players") or {}
    p1 = players.get("p1") or {}
    p2 = players.get("p2") or {}
    win_p, lose_p = (p1, p2) if winner == 1 else (p2, p1)

    winner_name = (win_p.get("name") or "").strip()
    loser_name = (lose_p.get("name") or "").strip()
    if not winner_name or not loser_name:
        return None

    tourney_date = _match_date(match)
    if len(tourney_date) != 8:
        return None

    return {
        "tourney_name": (match.get("tournament") or "").strip(),
        "tourney_date": tourney_date,
        "surface": match.get("surface") or "",
        "tourney_level": "",  # no published tier on the live-side match object
        "round": match.get("round") or "",
        "winner_name": winner_name,
        "loser_name": loser_name,
        "winner_rank": win_p.get("ranking"),
        "loser_rank": lose_p.get("ranking"),
        "score": _score_string((match.get("score") or {}).get("games"), winner),
        "best_of": _BEST_OF.get(match.get("format") or "", 3),
        "court": "Indoor" if match.get("indoor") else "Outdoor",
        "location": "",  # no published venue field
    }


def normalize_matches(matches: Iterable[dict]) -> pd.DataFrame:
    """Map Match objects onto the supplement schema. Pure; no network."""
    rows = [r for r in (_row_from_match(m) for m in matches) if r is not None]
    if not rows:
        return pd.DataFrame(columns=SUPPLEMENT_COLUMNS)
    df = pd.DataFrame(rows, columns=SUPPLEMENT_COLUMNS)
    df["winner_rank"] = pd.to_numeric(df["winner_rank"], errors="coerce")
    df["loser_rank"] = pd.to_numeric(df["loser_rank"], errors="coerce")
    df["best_of"] = pd.to_numeric(df["best_of"], errors="coerce").fillna(3).astype(int)
    return df


def fetch_supplement_rows(session: Optional[requests.Session] = None) -> Optional[pd.DataFrame]:
    """Fetch completed matches and return them in the supplement schema.

    Returns None — never raises, never an empty-but-meaningful frame — when the
    source is not configured or cannot answer, so the caller's existing
    "no data fetched, file unchanged" path handles it unmodified.
    """
    api_key = os.environ.get("LIVETENNISAPI_KEY", "").strip()
    if not api_key:
        return None  # not configured: this source does not exist

    base_url = os.environ.get("LIVETENNISAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    tour = os.environ.get("LIVETENNISAPI_TOUR", DEFAULT_TOUR).strip()
    try:
        max_pages = int(os.environ.get("LIVETENNISAPI_MAX_PAGES", DEFAULT_MAX_PAGES))
    except ValueError:
        max_pages = DEFAULT_MAX_PAGES

    http = session or requests.Session()
    collected: list[dict] = []
    offset = 0

    for _ in range(max(1, max_pages)):
        params = {"status": "completed", "limit": PAGE_SIZE, "offset": offset}
        if tour:
            params["tour"] = tour
        try:
            r = http.get(
                f"{base_url}/matches",
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            print(f"  [err]  livetennisapi: {e}")
            return None

        if r.status_code in (401, 403):
            # 403 is the documented answer for completed results on a key below
            # BASIC. Neither is retryable and neither is our caller's problem.
            print(
                f"  [skip] livetennisapi: HTTP {r.status_code} — key not "
                f"authorized for completed matches"
            )
            return None
        if r.status_code != 200:
            print(f"  [skip] livetennisapi: HTTP {r.status_code}")
            return None

        try:
            payload = r.json()
        except ValueError:
            print("  [err]  livetennisapi: response was not JSON")
            return None

        batch = payload.get("data") or []
        collected.extend(m for m in batch if isinstance(m, dict))

        meta = payload.get("meta") or {}
        # `has_more` is authoritative; the spec says to read it rather than
        # compare count to limit.
        if not meta.get("has_more"):
            break
        offset += PAGE_SIZE

    if not collected:
        print("  [skip] livetennisapi: no completed matches returned")
        return None

    df = normalize_matches(collected)
    if df.empty:
        print(f"  [skip] livetennisapi: {len(collected)} matches, none usable")
        return None

    print(f"  [ok]   livetennisapi: {len(df)} rows (from {len(collected)} matches)")
    return df
