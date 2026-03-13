"""
TennisIQ — Morning Report Generator
Reads experiments/log.jsonl and summarizes overnight results.

Usage:
  python experiments/morning_report.py
  python experiments/morning_report.py --date 20260311
"""

import argparse
import json
from datetime import datetime, date
from pathlib import Path


LOG_FILE    = Path(__file__).parent / "log.jsonl"
SUMMARY_OUT = Path(__file__).parent / "overnight_summary.md"
BASELINE    = Path(__file__).parent / "baseline.json"


def load_experiments(filter_date=None):
    if not LOG_FILE.exists():
        return []
    exps = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if filter_date:
                    ts = rec.get("timestamp", "")
                    if not ts.startswith(filter_date):
                        continue
                exps.append(rec)
            except json.JSONDecodeError:
                continue
    return exps


def load_baseline():
    if BASELINE.exists():
        with open(BASELINE) as f:
            return json.load(f)
    return {}


def generate_report(exps, baseline):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# TennisIQ — Overnight Summary ({today})",
        "",
        f"**Experiments run:** {len(exps)}",
    ]

    keeps   = [e for e in exps if e.get("decision") == "KEEP"]
    reverts = [e for e in exps if e.get("decision") == "REVERT"]
    neutral = [e for e in exps if e.get("decision") == "NEUTRAL"]

    lines += [
        f"**KEEP / REVERT / NEUTRAL:** {len(keeps)} / {len(reverts)} / {len(neutral)}",
        "",
    ]

    # Best result
    kept_with_delta = [e for e in keeps if e.get("brier_delta") is not None]
    if kept_with_delta:
        best = min(kept_with_delta, key=lambda e: e["brier_delta"])
        lines += [
            "## Best Result",
            f"**{best['exp_id']}** — {best['description']}",
            f"Brier delta: {best['brier_delta']:+.4f}",
            f"Branch: `{best['branch']}`",
            "",
        ]

    # All experiments table
    lines += ["## All Experiments", "", "| ID | Decision | Brier Δ | Next-shot k2 Δ | Description |", "|---|---|---|---|---|"]
    for e in exps:
        b_str  = f"{e['brier_delta']:+.4f}" if e.get("brier_delta") is not None else "—"
        k2_str = f"{e['next_shot_k2_delta']:+.4f}" if e.get("next_shot_k2_delta") is not None else "—"
        dec    = e.get("decision", "?")
        emoji  = {"KEEP": "✅", "REVERT": "❌", "NEUTRAL": "➖"}.get(dec, "?")
        lines.append(f"| {e['exp_id']} | {emoji} {dec} | {b_str} | {k2_str} | {e.get('description', '')} |")

    lines += ["", "## Baseline Reference"]
    if baseline:
        lines += [
            f"- Brier score: {baseline.get('brier_score', 'N/A')}",
            f"- Next-shot k2: {baseline.get('next_shot_acc_k2', 'N/A')}",
            f"- Calibration error: {baseline.get('calibration_error', 'N/A')}",
        ]
    else:
        lines.append("_Baseline not yet established — run Phase 4 first._")

    lines += ["", f"_Generated {datetime.now().isoformat()}_"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Filter to YYYYMMDD date (default: today)")
    args = parser.parse_args()

    raw_date = args.date or datetime.now().strftime("%Y%m%d")
    # Normalise to ISO prefix (YYYY-MM-DD) so it matches isoformat timestamps in log.jsonl
    if len(raw_date) == 8 and raw_date.isdigit():
        filter_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    else:
        filter_date = raw_date
    exps     = load_experiments(filter_date)
    baseline = load_baseline()

    if not exps:
        print(f"No experiments logged for {filter_date}.")
        print("Run experiments and log them with experiments/log_result.py")
        return

    report = generate_report(exps, baseline)
    print(report)

    with open(SUMMARY_OUT, "w") as f:
        f.write(report)
    print(f"\nReport saved to: {SUMMARY_OUT}")


if __name__ == "__main__":
    main()
