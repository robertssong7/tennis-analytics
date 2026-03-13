#!/usr/bin/env python3
"""
TennisIQ — Autonomous Research Agent
scripts/run_agent.py

Calls AWS Bedrock Claude Haiku to propose and evaluate experiments on
feature_engine.py. Loops until 8:30am local time.

Cost: ~$0.004 per experiment (2 Haiku calls). 12-18 experiments/night ≈ $0.05-0.10.

Usage:
    python scripts/run_agent.py
    python scripts/run_agent.py --dry-run      # plan experiments, don't execute
    python scripts/run_agent.py --model claude-3-haiku-20240307  # older model

Requirements:
    pip install boto3
    AWS credentials with bedrock:InvokeModel permission on us-east-1
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, time as dtime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT     = Path(__file__).parent.parent
FEATURE_ENG   = REPO_ROOT / "feature_engine.py"
PROGRAM_MD    = REPO_ROOT / "experiments" / "program.md"
HYPOTHESIS_MD = REPO_ROOT / "experiments" / "hypothesis_log.md"
BASELINE_JSON = REPO_ROOT / "experiments" / "baseline.json"
LOG_JSONL     = REPO_ROOT / "experiments" / "log.jsonl"

STOP_TIME = dtime(8, 30)   # 8:30am local

# ─────────────────────────────────────────────────────────────
# Bedrock client
# ─────────────────────────────────────────────────────────────

DEFAULT_MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"
FALLBACK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


def make_bedrock_client(region: str = None):
    import boto3
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return boto3.client("bedrock-runtime", region_name=region)


def call_haiku(client, model_id: str, system: str, user: str, max_tokens: int = 1024) -> str:
    """Call Bedrock Claude Haiku and return the text response."""
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    })
    try:
        resp = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"]
    except Exception as e:
        # Try fallback model
        if model_id != FALLBACK_MODEL_ID:
            logger.warning("Model %s failed (%s), trying fallback %s", model_id, e, FALLBACK_MODEL_ID)
            resp = client.invoke_model(
                modelId=FALLBACK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            result = json.loads(resp["body"].read())
            return result["content"][0]["text"]
        raise


# ─────────────────────────────────────────────────────────────
# Parameter registry — the only values the agent may change
# ─────────────────────────────────────────────────────────────

PARAM_REGISTRY = {
    # param_key: (config_name, dict_key, sub_key_or_None, type, min, max)
    "decay.serve_direction.half_life":    ("DECAY_CONFIG", "serve_direction",   "half_life_months", int,   6,  96),
    "decay.shot_type_mix.half_life":      ("DECAY_CONFIG", "shot_type_mix",     "half_life_months", int,   6,  96),
    "decay.rally_patterns.half_life":     ("DECAY_CONFIG", "rally_patterns",    "half_life_months", int,   6,  96),
    "decay.pressure_win_rate.half_life":  ("DECAY_CONFIG", "pressure_win_rate", "half_life_months", int,   3,  48),
    "decay.net_tendency.half_life":       ("DECAY_CONFIG", "net_tendency",      "half_life_months", int,   6,  96),
    "decay.error_rate.half_life":         ("DECAY_CONFIG", "error_rate",        "half_life_months", int,   3,  48),
    "window.serve_plus1":                 ("WINDOW_CONFIG", "serve_plus1_window",   None, int, 1, 5),
    "window.rally_pattern":               ("WINDOW_CONFIG", "rally_pattern_window", None, int, 2, 6),
    "window.pressure":                    ("WINDOW_CONFIG", "pressure_window",      None, int, 1, 4),
    "cluster.k":                          ("CLUSTER_CONFIG", "k",             None, int,   3,  12),
    "cluster.serve_weight":               ("CLUSTER_CONFIG", "serve_weight",   None, float, 0.5, 3.0),
    "cluster.rally_weight":               ("CLUSTER_CONFIG", "rally_weight",   None, float, 0.5, 3.0),
    "confidence.min_n_shrinkage":         ("CONFIDENCE_CONFIG", "min_n_shrinkage",    None, int, 10, 100),
    "confidence.low_threshold":           ("CONFIDENCE_CONFIG", "low_threshold",      None, int,  5,  30),
    "confidence.moderate_threshold":      ("CONFIDENCE_CONFIG", "moderate_threshold", None, int, 15,  60),
    "confidence.high_threshold":          ("CONFIDENCE_CONFIG", "high_threshold",     None, int, 30, 120),
}


def read_current_values() -> dict:
    """Import feature_engine to read live config values."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("feature_engine", FEATURE_ENG)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    current = {}
    for key, (cfg_name, dict_key, sub_key, typ, lo, hi) in PARAM_REGISTRY.items():
        cfg = getattr(mod, cfg_name)
        val = cfg[dict_key][sub_key] if sub_key else cfg[dict_key]
        current[key] = val
    return current


def apply_param_change(param: str, new_value) -> tuple[str, str]:
    """
    Apply a parameter change to feature_engine.py using targeted regex.
    Returns (old_value_str, new_value_str).
    Raises ValueError if param unknown or value out of bounds.
    """
    if param not in PARAM_REGISTRY:
        raise ValueError(f"Unknown param {param!r}. Valid: {sorted(PARAM_REGISTRY)}")

    cfg_name, dict_key, sub_key, typ, lo, hi = PARAM_REGISTRY[param]
    new_value = typ(new_value)
    if not (lo <= new_value <= hi):
        raise ValueError(f"{param} value {new_value} out of bounds [{lo}, {hi}]")

    source = FEATURE_ENG.read_text()

    if sub_key:
        # e.g. "serve_direction": {"half_life_months": 36, ...}
        pattern = rf'("{re.escape(dict_key)}":\s*\{{[^}}]*"{re.escape(sub_key)}":\s*)([0-9.]+)'
    else:
        # e.g. "serve_plus1_window":   2,
        pattern = rf'("{re.escape(dict_key)}":\s*)([0-9.]+)'

    match = re.search(pattern, source)
    if not match:
        raise ValueError(f"Could not locate {param} in feature_engine.py")

    old_str   = match.group(2)
    new_str   = str(new_value) if typ == int else f"{new_value}"
    new_source = source[:match.start(2)] + new_str + source[match.end(2):]
    FEATURE_ENG.write_text(new_source)
    logger.info("Applied %s: %s → %s", param, old_str, new_str)
    return old_str, new_str


def revert_param_change(param: str, old_value_str: str):
    """Revert a parameter to its previous value."""
    cfg_name, dict_key, sub_key, typ, lo, hi = PARAM_REGISTRY[param]
    source = FEATURE_ENG.read_text()
    if sub_key:
        pattern = rf'("{re.escape(dict_key)}":\s*\{{[^}}]*"{re.escape(sub_key)}":\s*)([0-9.]+)'
    else:
        pattern = rf'("{re.escape(dict_key)}":\s*)([0-9.]+)'
    match = re.search(pattern, source)
    if match:
        new_source = source[:match.start(2)] + old_value_str + source[match.end(2):]
        FEATURE_ENG.write_text(new_source)
        logger.info("Reverted %s → %s", param, old_value_str)


# ─────────────────────────────────────────────────────────────
# Experiment execution
# ─────────────────────────────────────────────────────────────

def run_feature_engine() -> bool:
    """Run feature_engine.py --surface hard. Returns True on success."""
    result = subprocess.run(
        [sys.executable, str(FEATURE_ENG), "--surface", "hard"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        logger.error("feature_engine failed:\n%s", result.stderr[-2000:])
        return False
    logger.info("feature_engine complete")
    return True


def run_evaluate() -> dict | None:
    """Run evaluate.py --surface hard. Returns parsed result dict or None on crash."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "evaluate.py"), "--surface", "hard"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    # evaluate.py prints JSON to stdout regardless of gate outcome
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error("evaluate.py produced no JSON:\n%s", result.stderr[-1000:])
        return None


def log_result(desc: str, decision: str) -> str:
    """Call log_result.py and return the exp_id."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "experiments" / "log_result.py"),
         "--desc", desc, "--decision", decision],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    # Extract exp_id from output line like "[KEEP] 20260312-003 — ..."
    m = re.search(r"\] (\d{8}-\d{3}) —", output)
    exp_id = m.group(1) if m else "unknown"
    logger.info("%s", output.strip())
    return exp_id


# ─────────────────────────────────────────────────────────────
# Context helpers
# ─────────────────────────────────────────────────────────────

def load_text(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text()
    return text[-max_chars:] if len(text) > max_chars else text


def recent_log_entries(n: int = 8) -> str:
    if not LOG_JSONL.exists():
        return "No experiments logged yet."
    lines = LOG_JSONL.read_text().strip().splitlines()
    entries = []
    for line in lines[-n:]:
        try:
            r = json.loads(line)
            entries.append(
                f"  [{r['decision']}] {r['exp_id']} Brier={r['brier_score']} "
                f"Δ={r.get('brier_delta', 'N/A')} — {r['description']}"
            )
        except Exception:
            pass
    return "\n".join(entries) or "No recent experiments."


def baseline_summary() -> str:
    if not BASELINE_JSON.exists():
        return "No baseline found."
    b = json.loads(BASELINE_JSON.read_text())
    return f"Brier={b.get('brier_score')} | cal_error={b.get('calibration_error')} | n={b.get('n_matches')}"


# ─────────────────────────────────────────────────────────────
# Agent prompts
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """{program}

---
Current parameter values:
{current_values}

Baseline: {baseline}

Recent experiment history:
{recent_log}

Hypothesis log (most recent session):
{hypothesis_log}
"""

PROPOSE_PROMPT = """Propose the single most promising experiment to run next.

Output ONLY a JSON object (no prose before or after) with exactly these keys:
{
  "param": "<param_key from registry>",
  "new_value": <number>,
  "rationale": "<one sentence>",
  "expected_delta": "<e.g. -0.003>"
}

Valid param keys: {param_keys}

Do not repeat an experiment that already appears in the recent history.
Prioritize experiments in categories that haven't been tried yet.
"""

DECIDE_PROMPT = """Experiment result:
  param:     {param}
  old_value: {old_value}
  new_value: {new_value}
  Brier:     {brier} (delta {delta:+.4f} vs baseline {baseline_brier})
  Cal error: {cal_error}
  Gate:      {"PASSED" if cal_ok else "FAILED (cal_error > 0.15 — must REVERT)"}

Decide: output ONLY one of: KEEP / NEUTRAL / REVERT
Then on a new line: one sentence explanation.

Rules:
- REVERT if gate failed (cal_error > 0.15)
- KEEP if Brier delta <= -0.002 and gate passed
- NEUTRAL if delta between -0.002 and +0.001
- REVERT if delta > +0.001
"""


# ─────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────

def should_stop() -> bool:
    return datetime.now().time() >= STOP_TIME


def update_hypothesis_log(entry: str):
    existing = load_text(HYPOTHESIS_MD)
    new_entry = f"\n---\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{entry}\n"
    HYPOTHESIS_MD.write_text(existing + new_entry)


def write_overnight_summary(results: list[dict]):
    n = len(results)
    keeps  = sum(1 for r in results if r["decision"] == "KEEP")
    revert = sum(1 for r in results if r["decision"] == "REVERT")
    neutral = sum(1 for r in results if r["decision"] == "NEUTRAL")

    lines = [
        f"# TennisIQ — Overnight Summary ({datetime.now().strftime('%Y-%m-%d')})",
        f"",
        f"**Experiments run:** {n}",
        f"**KEEP / REVERT / NEUTRAL:** {keeps} / {revert} / {neutral}",
        f"",
        f"## All Experiments",
        f"",
        f"| ID | Decision | Brier Δ | Description |",
        f"|---|---|---|---|",
    ]
    for r in results:
        d = f"{r['delta']:+.4f}" if r.get("delta") is not None else "N/A"
        icon = {"KEEP": "✅", "REVERT": "❌", "NEUTRAL": "⚪"}.get(r["decision"], "?")
        lines.append(f"| {r['exp_id']} | {icon} {r['decision']} | {d} | {r['desc']} |")

    lines += [
        f"",
        f"## Baseline Reference",
        f"- {baseline_summary()}",
        f"",
        f"_Generated {datetime.now().isoformat()}_",
    ]
    summary = "\n".join(lines)
    out_path = REPO_ROOT / "experiments" / "overnight_summary.md"
    out_path.write_text(summary)
    logger.info("Overnight summary written to %s", out_path)
    print(summary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Propose experiments but don't execute them")
    parser.add_argument("--model", default=DEFAULT_MODEL_ID,
                        help="Bedrock model ID")
    parser.add_argument("--region", default=None,
                        help="AWS region (default: AWS_DEFAULT_REGION or us-east-1)")
    parser.add_argument("--max-experiments", type=int, default=20,
                        help="Safety cap on experiment count")
    args = parser.parse_args()

    logger.info("TennisIQ agent starting — will stop at 08:30 local time")
    logger.info("Dry run: %s | Model: %s", args.dry_run, args.model)

    try:
        client = make_bedrock_client(args.region)
    except Exception as e:
        logger.error("Could not create Bedrock client: %s", e)
        logger.error("Ensure AWS credentials are configured with bedrock:InvokeModel permission.")
        sys.exit(1)

    program_text   = load_text(PROGRAM_MD)
    param_keys_str = ", ".join(sorted(PARAM_REGISTRY))

    session_results: list[dict] = []
    consecutive_non_keep = {"decay": 0, "window": 0, "cluster": 0, "confidence": 0}

    while not should_stop() and len(session_results) < args.max_experiments:
        exp_num = len(session_results) + 1
        logger.info("── Experiment %d ──────────────────────────────", exp_num)

        current_values = read_current_values()
        cv_str = "\n".join(f"  {k}: {v}" for k, v in sorted(current_values.items()))

        system = SYSTEM_PROMPT_TEMPLATE.format(
            program=program_text,
            current_values=cv_str,
            baseline=baseline_summary(),
            recent_log=recent_log_entries(8),
            hypothesis_log=load_text(HYPOTHESIS_MD, max_chars=2000),
        )

        # Step 1: propose
        propose_user = PROPOSE_PROMPT.format(param_keys=param_keys_str)
        logger.info("Calling Haiku for experiment proposal...")
        raw_proposal = call_haiku(client, args.model, system, propose_user, max_tokens=256)
        logger.info("Proposal: %s", raw_proposal.strip())

        # Parse JSON proposal
        try:
            # Extract JSON even if Haiku adds surrounding prose
            json_match = re.search(r'\{[^}]+\}', raw_proposal, re.DOTALL)
            proposal = json.loads(json_match.group(0) if json_match else raw_proposal)
            param     = proposal["param"]
            new_value = proposal["new_value"]
            rationale = proposal.get("rationale", "")
        except Exception as e:
            logger.error("Could not parse proposal JSON: %s — skipping", e)
            continue

        desc = f"{param} → {new_value}: {rationale}"

        if args.dry_run:
            logger.info("[DRY RUN] Would run: %s", desc)
            session_results.append({"exp_id": "DRY", "decision": "DRY", "delta": None, "desc": desc})
            continue

        # Step 2: apply change
        try:
            old_str, new_str = apply_param_change(param, new_value)
        except ValueError as e:
            logger.error("Invalid proposal: %s — skipping", e)
            continue

        # Step 3: run feature engine
        if not run_feature_engine():
            revert_param_change(param, old_str)
            session_results.append({"exp_id": "ERR", "decision": "REVERT", "delta": None, "desc": desc})
            continue

        # Step 4: evaluate
        eval_result = run_evaluate()
        if eval_result is None:
            revert_param_change(param, old_str)
            session_results.append({"exp_id": "ERR", "decision": "REVERT", "delta": None, "desc": desc})
            continue

        brier     = eval_result.get("brier_score")
        cal_error = eval_result.get("calibration_error")
        baseline  = json.loads(BASELINE_JSON.read_text())
        b_brier   = baseline.get("brier_score", 0.2544)
        delta     = round(brier - b_brier, 4) if brier is not None else None
        cal_ok    = cal_error is not None and cal_error <= 0.15

        # Step 5: decide
        decide_user = DECIDE_PROMPT.format(
            param=param, old_value=old_str, new_value=new_str,
            brier=brier, delta=delta or 0, baseline_brier=b_brier,
            cal_error=cal_error, cal_ok=cal_ok,
        )
        logger.info("Calling Haiku for decision...")
        raw_decision = call_haiku(client, args.model, system, decide_user, max_tokens=128)
        logger.info("Decision: %s", raw_decision.strip())

        decision_line = raw_decision.strip().splitlines()[0].strip().upper()
        if "KEEP" in decision_line:
            decision = "KEEP"
        elif "NEUTRAL" in decision_line:
            decision = "NEUTRAL"
        else:
            decision = "REVERT"

        # Force REVERT if gate failed
        if not cal_ok:
            decision = "REVERT"
            logger.warning("Gate failed (cal_error=%.4f > 0.15) — forcing REVERT", cal_error)

        # Step 6: log
        exp_id = log_result(desc, decision)

        # Step 7: revert if needed
        if decision in ("REVERT", "NEUTRAL"):
            revert_param_change(param, old_str)
        else:
            # KEEP — update baseline
            baseline["brier_score"]       = brier
            baseline["calibration_error"] = cal_error
            baseline["date"]              = datetime.now().strftime("%Y-%m-%d")
            baseline["notes"]             = f"Updated by agent loop exp {exp_id}: {desc}"
            BASELINE_JSON.write_text(json.dumps(baseline, indent=2))
            logger.info("Baseline updated: Brier %.4f → %.4f", b_brier, brier)

        # Step 8: update hypothesis log
        category = param.split(".")[0]
        consecutive_non_keep[category] = 0 if decision == "KEEP" else consecutive_non_keep.get(category, 0) + 1
        update_hypothesis_log(
            f"**{exp_id}** [{decision}] Brier={brier} Δ={delta:+.4f}\n"
            f"  `{param}` {old_str}→{new_str}: {rationale}\n"
            f"  Cal error: {cal_error}"
        )

        session_results.append({
            "exp_id": exp_id, "decision": decision, "delta": delta, "desc": desc
        })

        logger.info("Result: %s Brier=%.4f Δ=%+.4f cal=%.4f",
                    decision, brier or 0, delta or 0, cal_error or 0)

    # Done
    reason = "08:30 stop time" if should_stop() else f"max experiments ({args.max_experiments})"
    logger.info("Agent loop ended: %s. %d experiments run.", reason, len(session_results))
    write_overnight_summary(session_results)


if __name__ == "__main__":
    main()
