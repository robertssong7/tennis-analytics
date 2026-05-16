"""Haiku 4.5 editorial layer for TennisIQ insights.

Takes a fact-verified candidate, calls Claude Haiku 4.5 with an ESPN/538 voice
constraint, and returns a structured insight {headline, body, category,
subject, metrics_cited}. Tracks per-call token spend so the orchestrator
can enforce the monthly cap.

The model rewrites the raw_text_seed in the editorial voice; it MUST NOT
introduce new numbers. Anything it adds gets compared back against
supporting_metrics by run.py before publish.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

MODEL_ID = os.environ.get("INSIGHTS_MODEL", "anthropic/claude-haiku-4.5")

# Haiku 4.5 pricing (USD per million tokens). Used for spend projection only.
PRICE_INPUT_PER_MTOK = 1.00
PRICE_OUTPUT_PER_MTOK = 5.00

SYSTEM_PROMPT = (
    "You are the TennisIQ insights editor. Voice: ESPN / FiveThirtyEight - "
    "factual, analytical, calm. Never write marketing or hype. No em dashes. "
    "No emojis. Maximum 60 words across the entire body. Do not introduce "
    "new statistics or claims beyond the supporting_metrics provided. "
    "Output strict JSON with keys: headline (<=8 words), body (<=60 words), "
    "category (echo the input category), subject (the primary player name), "
    "metrics_cited (a list of numeric metric keys from supporting_metrics "
    "that appear in the body). Nothing outside the JSON object."
)


class HaikuUnavailable(RuntimeError):
    """Raised when no usable LLM credentials are configured."""


def _load_anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HaikuUnavailable("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise HaikuUnavailable(f"anthropic SDK not installed: {e}") from e
    return Anthropic(api_key=api_key)


def _build_user_prompt(candidate: dict) -> str:
    return (
        "Rewrite this seed in the TennisIQ editorial voice. Cite only the "
        "metrics in supporting_metrics.\n\n"
        f"category: {candidate.get('category')}\n"
        f"subject_players: {candidate.get('subject_players')}\n"
        f"supporting_metrics: {json.dumps(candidate.get('supporting_metrics', {}), sort_keys=True)}\n"
        f"raw_text_seed: {candidate.get('raw_text_seed', '')}\n"
        "\nReturn only the JSON object."
    )


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def edit(candidate: dict) -> tuple[dict, dict]:
    """Returns (insight, usage). insight is the published shape; usage has
    input_tokens, output_tokens, cost_usd."""
    client = _load_anthropic_client()
    user = _build_user_prompt(candidate)

    msg = client.messages.create(
        model=MODEL_ID,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )

    # Aggregate text content from the response.
    text_parts = []
    for block in msg.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw_text = "\n".join(text_parts).strip()

    parsed = _extract_json(raw_text)
    headline = str(parsed.get("headline", "")).strip()
    body = str(parsed.get("body", "")).strip()
    if not headline or not body:
        raise ValueError(f"empty headline or body in model output: {parsed!r}")
    if "—" in headline or "—" in body:
        raise ValueError("em dash detected in model output")

    insight = {
        "headline": headline,
        "body": body,
        "category": candidate.get("category"),
        "subject": (candidate.get("subject_players") or [None])[0],
        "metrics_cited": list(parsed.get("metrics_cited", []) or []),
        "supporting_metrics": candidate.get("supporting_metrics", {}),
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }

    in_tok = int(getattr(msg.usage, "input_tokens", 0))
    out_tok = int(getattr(msg.usage, "output_tokens", 0))
    cost = (in_tok / 1_000_000) * PRICE_INPUT_PER_MTOK + (out_tok / 1_000_000) * PRICE_OUTPUT_PER_MTOK

    usage = {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 6),
        "model": MODEL_ID,
    }
    return insight, usage
