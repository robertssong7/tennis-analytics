"""
TennisIQ — Experiment Result Logger
Records experiment outcomes to experiments/log.jsonl and experiments/results/

Usage:
  python experiments/log_result.py --desc "what you changed" --decision KEEP
  python experiments/log_result.py --desc "trigram serve+1 window" --decision REVERT
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results"
LOG_FILE    = Path(__file__).parent / "log.jsonl"
BASELINE    = Path(__file__).parent / "baseline.json"


def get_current_branch():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def get_latest_eval():
    """Read most recent evaluation result from evaluate.py output if available."""
    eval_out = Path(__file__).parent.parent / "experiments" / "_last_eval.json"
    if eval_out.exists():
        with open(eval_out) as f:
            return json.load(f)
    return {}


def get_baseline():
    if BASELINE.exists():
        with open(BASELINE) as f:
            return json.load(f)
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--desc", required=True, help="Experiment description")
    parser.add_argument(
        "--decision", required=True,
        choices=["KEEP", "REVERT", "NEUTRAL"],
        help="KEEP if improved, REVERT if not, NEUTRAL if within noise"
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Build experiment ID from date + sequence
    date_str = datetime.now().strftime("%Y%m%d")
    existing = sorted(RESULTS_DIR.glob(f"{date_str}-*.json"))
    seq = len(existing) + 1
    exp_id = f"{date_str}-{seq:03d}"

    branch  = get_current_branch()
    eval_data = get_latest_eval()
    baseline  = get_baseline()

    brier        = eval_data.get("brier_score")
    baseline_b   = baseline.get("brier_score")
    brier_delta  = round(brier - baseline_b, 4) if (brier and baseline_b) else None

    next_k2      = eval_data.get("next_shot_acc_k2")
    baseline_k2  = baseline.get("next_shot_acc_k2")
    next_k2_delta = round(next_k2 - baseline_k2, 4) if (next_k2 and baseline_k2) else None

    record = {
        "exp_id":           exp_id,
        "timestamp":        datetime.now().isoformat(),
        "branch":           branch,
        "description":      args.desc,
        "decision":         args.decision,
        "brier_score":      brier,
        "brier_delta":      brier_delta,
        "calibration_error": eval_data.get("calibration_error"),
        "next_shot_k2":     next_k2,
        "next_shot_k2_delta": next_k2_delta,
        "next_shot_k4":     eval_data.get("next_shot_acc_k4"),
        "n_matches":        eval_data.get("n_matches"),
        "surface":          eval_data.get("surface", "hard"),
    }

    # Write individual result file
    result_path = RESULTS_DIR / f"{exp_id}.json"
    with open(result_path, "w") as f:
        json.dump(record, f, indent=2)

    # Append to log.jsonl
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    # Print summary
    brier_str = f"{brier_delta:+.4f}" if brier_delta is not None else "N/A"
    k2_str    = f"{next_k2_delta:+.4f}" if next_k2_delta is not None else "N/A"
    print(f"\n[{args.decision}] {exp_id} — {args.desc}")
    print(f"  Brier delta:    {brier_str}  (lower is better)")
    print(f"  Next-shot k2:   {k2_str}  (higher is better)")
    print(f"  Branch: {branch}")
    print(f"  Logged to: {result_path}")


if __name__ == "__main__":
    main()
