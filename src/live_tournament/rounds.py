"""Shared round normalization and progress selection for scraper and API."""
from __future__ import annotations


_ALIASES = {
    "R1": "R128", "1R": "R128", "1ST ROUND": "R128",
    "R2": "R64", "2R": "R64", "2ND ROUND": "R64",
    "R3": "R32", "3R": "R32", "3RD ROUND": "R32",
    "R4": "R16", "4R": "R16", "4TH ROUND": "R16",
    "QUARTERFINALS": "QF", "SEMIFINALS": "SF",
    "FINAL": "F", "THE FINAL": "F",
}
_ORDER = {"R128": 1, "R64": 2, "R32": 3, "R16": 4, "QF": 5, "SF": 6, "F": 7}
FINISHED_STATUSES = frozenset({"completed", "retired", "walkover"})


def normalize_round(round_name: str) -> str:
    """Normalize aliases for comparisons while allowing source labels in responses."""
    label = (round_name or "").strip().upper()
    return _ALIASES.get(label, label)


def round_order(round_name: str, draw_size: int | None = None) -> int:
    """Higher means later; unknown labels rank zero. draw_size is compatibility-only."""
    return _ORDER.get(normalize_round(round_name), 0)


def current_round(draw: list[dict]) -> str:
    """Earliest live round, then earliest scheduled, then latest finished round.

    Derive ranks from labels, never persisted round_order values. Unknown
    labels do not outrank recognized rounds within a status bucket.
    Retirements and walkovers advance the bracket just like completed matches.
    """
    for statuses, latest in (({"in_progress"}, False), ({"scheduled"}, False),
                             (FINISHED_STATUSES, True)):
        bucket = [m for m in draw if m.get("status", "scheduled") in statuses and m.get("round")]
        if bucket:
            known = [m for m in bucket if round_order(m["round"]) > 0]
            choose = max if latest else min
            return choose(known or bucket, key=lambda m: round_order(m["round"]))["round"]
    return "1R"
