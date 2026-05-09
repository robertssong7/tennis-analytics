"""Stat-of-the-Day engine — deterministic selection + templated rendering.

Pipeline:
  1. Load candidates from data/processed/stat_candidates.json
  2. Load history from data/processed/stat_history.json
  3. Score each candidate: novelty * diversity_penalty * repeat_penalty
  4. Pick highest-scoring candidate
  5. Apply category template
  6. Write data/processed/stat_of_the_day.json and append to history
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.stat_templates import render  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stat_of_day")

CANDIDATES_PATH = PROJECT_ROOT / "data" / "processed" / "stat_candidates.json"
HISTORY_PATH = PROJECT_ROOT / "data" / "processed" / "stat_history.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "stat_of_the_day.json"


def load_candidates() -> list:
    if not CANDIDATES_PATH.exists():
        return []
    with open(CANDIDATES_PATH) as f:
        return json.load(f).get("candidates", [])


def load_history() -> list:
    if not HISTORY_PATH.exists():
        return []
    with open(HISTORY_PATH) as f:
        return json.load(f)


def score(candidate: dict, history: list) -> float:
    base = float(candidate.get("novelty_score", 0.5))
    last_7 = [h.get("category") for h in history[-7:]]
    last_1 = history[-1].get("category") if history else None
    if candidate["category"] == last_1:
        base *= 0.2
    elif candidate["category"] in last_7:
        base *= 0.5
    last_14 = history[-14:]
    cand_player = candidate.get("facts", {}).get("player") or candidate.get("facts", {}).get("winner")
    for h in last_14:
        if h.get("category") != candidate["category"]:
            continue
        h_player = h.get("raw_facts", {}).get("player") or h.get("raw_facts", {}).get("winner")
        if h_player and cand_player and h_player == cand_player:
            base *= 0.3
            break
    return base


def pick(candidates: list, history: list) -> dict:
    if not candidates:
        return {}
    scored = [(score(c, history), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else {}


def main():
    candidates = load_candidates()
    history = load_history()

    if not candidates:
        payload = {
            "rendered": False,
            "headline": "Stat of the day will return soon",
            "body": "The pipeline produced no qualifying candidates today.",
            "category": "fallback",
            "served_at": datetime.now(timezone.utc).isoformat(),
            "served_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
    else:
        chosen = pick(candidates, history)
        rendered = render(chosen["category"], chosen.get("facts", {}))
        payload = {
            **rendered,
            "raw_facts": chosen.get("facts", {}),
            "novelty_score": float(round(chosen.get("novelty_score", 0), 3)),
            "served_at": datetime.now(timezone.utc).isoformat(),
            "served_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    history.append(payload)
    history = history[-100:]
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)

    log.info(f"Stat of the day [{payload.get('category')}]: {payload.get('headline')}")


if __name__ == "__main__":
    main()
