"""Orchestrator for the TennisIQ insights engine.

Pipeline:
    1. generate_candidates  - structured candidates across categories
    2. verify_facts         - hard-gate, drops anything with drift
    3. edit_with_haiku      - per-candidate Haiku rewrite (skipped without ANTHROPIC_API_KEY)
    4. publish              - write data/insights/published.json + history

Logging goes to _insights_engine.log so spend stays observable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.insights_engine import generate_candidates as gen  # noqa: E402
from tools.insights_engine import verify_facts as verify  # noqa: E402
from tools.insights_engine import edit_with_haiku as haiku  # noqa: E402

PUBLISH_PATH = ROOT / "data" / "insights" / "published.json"
HISTORY_PATH = ROOT / "data" / "insights" / "history.json"
LOG_PATH = ROOT / "_insights_engine.log"

# Hard monthly cap. If projected month-end spend exceeds this, the run still
# publishes the deterministic seed but skips Haiku editorial.
MONTHLY_BUDGET_USD = 5.0

MAX_PUBLISH = 6  # keep the feed compact; 3 categories x 2 slots

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("insights")


def _seed_insight(candidate: dict) -> dict:
    """Deterministic, template-based insight for the no-Haiku path.

    Mirrors the published shape so the frontend can render either source
    interchangeably. The headline is a category-specific template; the body
    is the candidate's own raw_text_seed.
    """
    cat = candidate.get("category")
    sm = candidate.get("supporting_metrics", {})
    subject = (candidate.get("subject_players") or [None])[0]

    if cat == "surface_specialists":
        headline = f"{subject}: {sm.get('gap')}-point surface gap"
        metrics_cited = ["top_rating", "bottom_rating", "gap"]
    elif cat == "form_reversals":
        direction = sm.get("direction", "shifting")
        headline = f"{subject} trending {direction}"
        metrics_cited = ["base_rating", "display_rating", "form_modifier"]
    elif cat == "tournament_narrative":
        headline = f"{subject} deep at the {sm.get('tournament')}"
        metrics_cited = ["wins_in_tournament"]
    else:
        headline = subject or "Insight"
        metrics_cited = []

    return {
        "headline": headline,
        "body": candidate.get("raw_text_seed", ""),
        "category": cat,
        "subject": subject,
        "metrics_cited": metrics_cited,
        "supporting_metrics": sm,
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "source": "seed",
    }


def _monthly_spend_so_far() -> float:
    """Sum cost_usd entries logged this calendar month (UTC)."""
    if not LOG_PATH.exists():
        return 0.0
    today = datetime.now(timezone.utc)
    month_prefix = f"{today.year:04d}-{today.month:02d}"
    total = 0.0
    try:
        with open(LOG_PATH) as f:
            for line in f:
                if "cost_usd=" not in line:
                    continue
                if month_prefix not in line:
                    continue
                # Parse the trailing "cost_usd=<number>" token.
                for tok in line.strip().split():
                    if tok.startswith("cost_usd="):
                        try:
                            total += float(tok.split("=", 1)[1])
                        except ValueError:
                            pass
    except Exception:
        return total
    return total


def _maybe_edit(candidate: dict, allow_haiku: bool) -> tuple[dict, dict | None]:
    if not allow_haiku:
        return _seed_insight(candidate), None
    try:
        insight, usage = haiku.edit(candidate)
        insight["source"] = "haiku"
        return insight, usage
    except haiku.HaikuUnavailable as e:
        log.warning(f"haiku unavailable, falling back to seed: {e}")
        return _seed_insight(candidate), None
    except Exception as e:  # noqa: BLE001 - degrade gracefully on editorial failure
        log.warning(f"haiku edit failed for {candidate.get('subject_players')}: "
                    f"{type(e).__name__}: {e}")
        return _seed_insight(candidate), None


def _diversify(insights: list[dict]) -> list[dict]:
    """Limit to 2 per category and at most MAX_PUBLISH total."""
    by_cat: dict[str, list[dict]] = {}
    for i in insights:
        by_cat.setdefault(i.get("category", "_"), []).append(i)
    out: list[dict] = []
    for cat, items in by_cat.items():
        out.extend(items[:2])
    return out[:MAX_PUBLISH]


def _append_history(insights: list[dict]) -> None:
    history: list[dict] = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text())
        except Exception:
            history = []
    history.extend(insights)
    history = history[-300:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def main() -> int:
    log.info("insights_engine run start")

    candidates = gen.generate()
    log.info(f"candidates generated: {len(candidates)}")

    kept, rejected = verify.verify_all(candidates)
    log.info(f"fact-verified: kept={len(kept)} rejected={len(rejected)}")
    for c, reason in rejected:
        log.info(f"reject {c.get('category')}/{c.get('subject_players')}: {reason}")

    if not kept:
        log.warning("no candidates survived verification; nothing to publish")
        return 0

    spend = _monthly_spend_so_far()
    allow_haiku = bool(os.environ.get("ANTHROPIC_API_KEY")) and spend < MONTHLY_BUDGET_USD
    if not allow_haiku:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log.warning("ANTHROPIC_API_KEY missing; publishing seed-only insights")
        else:
            log.warning(f"monthly spend ${spend:.4f} >= ${MONTHLY_BUDGET_USD}, "
                        f"publishing seed-only insights")

    published: list[dict] = []
    run_cost = 0.0
    for c in kept:
        insight, usage = _maybe_edit(c, allow_haiku=allow_haiku)
        published.append(insight)
        if usage:
            run_cost += float(usage.get("cost_usd") or 0)
            log.info(
                f"haiku usage subject={insight.get('subject')!r} "
                f"input_tokens={usage['input_tokens']} "
                f"output_tokens={usage['output_tokens']} "
                f"cost_usd={usage['cost_usd']}"
            )

    published = _diversify(published)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "insights": published,
        "run": {
            "candidates": len(candidates),
            "verified": len(kept),
            "rejected": len(rejected),
            "published": len(published),
            "haiku_run_cost_usd": round(run_cost, 6),
            "month_spend_so_far_usd": round(spend + run_cost, 6),
        },
    }

    PUBLISH_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLISH_PATH.write_text(json.dumps(payload, indent=2))
    _append_history(published)

    log.info(
        f"published={len(published)} "
        f"haiku_run_cost_usd={round(run_cost, 6)} "
        f"month_spend_usd={round(spend + run_cost, 6)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
