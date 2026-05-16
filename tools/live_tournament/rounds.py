"""Round ordering for tournaments of different draw sizes.

Eliminates the Session 17 "stuck at R16 cap" bug by deriving the current
round from data rather than configuration.
"""
from __future__ import annotations


# Canonical order across all common round labels. Larger int = later round.
# Includes synonyms so the parser doesn't have to normalize first.
_BASE_ORDER = {
    "R128": 1, "1R": 1,
    "R64": 2,  "2R": 2,
    "R32": 3,  "3R": 3,
    "R16": 4,  "4R": 4,
    "QF":  5,
    "SF":  6,
    "F":   7,
}


def round_order(round_name: str, draw_size: int | None = None) -> int:
    """Integer rank for a round label. Higher = later. Unknown labels return 0.

    draw_size is accepted for forward compatibility but does not change the
    ranking: a final is always 7, a semifinal always 6, regardless of draw
    size. The bug we are fixing is "round inferred from configuration";
    ranking is always data-driven.
    """
    return _BASE_ORDER.get(round_name.strip().upper(), 0)


def current_round(draw: list[dict]) -> str:
    """Pick the latest round visible in the draw.

    Prefers in_progress matches, falls back to scheduled, falls back to the
    most recent completed. Returns the round label as a string, or "1R" if
    the draw is empty.
    """
    by_status: dict[str, list[dict]] = {"in_progress": [], "scheduled": [], "completed": []}
    for m in draw:
        st = m.get("status", "scheduled")
        if st in by_status:
            by_status[st].append(m)
    for status in ("in_progress", "scheduled", "completed"):
        bucket = by_status[status]
        if bucket:
            return max(bucket, key=lambda m: m.get("round_order", 0))["round"]
    return "1R"
