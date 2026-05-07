"""
Compute peak career Elo for every ATP player from Sackmann match CSVs.

Uses simple chronological Elo (start=1500, K=32).
Outputs data/processed/peak_elo.json keyed by canonical player name.
"""

import json
import math
import os
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent.parent
SACKMANN_DIR = BASE / "data" / "sackmann" / "tennis_atp"
OUTPUT_PATH = BASE / "data" / "processed" / "peak_elo.json"


def compute_peak_elo():
    # Gather all main-tour CSV files (same filter as predict_engine.py)
    all_csvs = sorted(SACKMANN_DIR.glob("atp_matches_*.csv"))
    csv_files = [
        f for f in all_csvs
        if "qual" not in f.name
        and "futures" not in f.name
        and "doubles" not in f.name
        and "amateur" not in f.name
        and "supplement" not in f.name
    ]
    print(f"Loading {len(csv_files)} main-tour CSV files...")

    # Collect all matches with dates
    matches = []
    for csv_path in csv_files:
        try:
            df = pd.read_csv(
                csv_path,
                usecols=["winner_name", "loser_name", "tourney_date"],
                low_memory=False,
            )
        except Exception as e:
            print(f"  Skip {csv_path.name}: {e}")
            continue
        df = df.dropna(subset=["winner_name", "loser_name"])
        for _, row in df.iterrows():
            tdate = int(row["tourney_date"]) if pd.notna(row.get("tourney_date")) else 0
            matches.append((tdate, str(row["winner_name"]), str(row["loser_name"])))

    # Sort chronologically
    matches.sort(key=lambda x: x[0])
    print(f"Total matches: {len(matches):,}")

    # Run Elo calculation
    elo = {}  # player -> current Elo
    peak = {}  # player -> {peak_elo, peak_year, last_match_year, last_match_date, total_matches}
    K = 32

    def _to_iso(tdate):
        if tdate <= 0:
            return None
        s = str(tdate)
        if len(s) != 8:
            return None
        try:
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        except Exception:
            return None

    for tdate, winner, loser in matches:
        year = tdate // 10000 if tdate > 0 else 0
        iso_date = _to_iso(tdate)

        # Initialize if needed
        if winner not in elo:
            elo[winner] = 1500.0
            peak[winner] = {"peak_elo": 1500.0, "peak_year": year, "last_match_year": year, "last_match_date": iso_date, "total_matches": 0}
        if loser not in elo:
            elo[loser] = 1500.0
            peak[loser] = {"peak_elo": 1500.0, "peak_year": year, "last_match_year": year, "last_match_date": iso_date, "total_matches": 0}

        # Expected scores
        ew = 1.0 / (1.0 + 10.0 ** ((elo[loser] - elo[winner]) / 400.0))

        # Update Elo
        elo[winner] += K * (1.0 - ew)
        elo[loser] -= K * (1.0 - ew)

        # Track peak and last match
        peak[winner]["total_matches"] += 1
        peak[loser]["total_matches"] += 1
        if year > 0:
            if year >= peak[winner]["last_match_year"]:
                peak[winner]["last_match_year"] = year
                peak[winner]["last_match_date"] = iso_date or peak[winner]["last_match_date"]
            if year >= peak[loser]["last_match_year"]:
                peak[loser]["last_match_year"] = year
                peak[loser]["last_match_date"] = iso_date or peak[loser]["last_match_date"]

        if elo[winner] > peak[winner]["peak_elo"]:
            peak[winner]["peak_elo"] = elo[winner]
            peak[winner]["peak_year"] = year

        if elo[loser] > peak[loser]["peak_elo"]:
            peak[loser]["peak_elo"] = elo[loser]
            peak[loser]["peak_year"] = year

    # Round values for JSON
    result = {}
    for player, data in peak.items():
        result[player] = {
            "peak_elo": round(float(data["peak_elo"]), 1),
            "current_elo": round(float(elo[player]), 1),
            "peak_year": int(data["peak_year"]),
            "last_match_year": int(data["last_match_year"]),
            "last_match_date": data.get("last_match_date"),
            "total_matches": int(data["total_matches"]),
        }

    # Write output
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote {len(result):,} players to {OUTPUT_PATH}")

    # Sanity check
    for name in ["Rafael Nadal", "Roger Federer", "Novak Djokovic", "Andy Murray",
                  "Jannik Sinner", "Carlos Alcaraz"]:
        d = result.get(name)
        if d:
            print(f"  {name:20s}  peak={d['peak_elo']:7.1f}  year={d['peak_year']}  last_date={d.get('last_match_date')}  matches={d['total_matches']}")
        else:
            print(f"  {name:20s}  NOT FOUND")


if __name__ == "__main__":
    compute_peak_elo()
