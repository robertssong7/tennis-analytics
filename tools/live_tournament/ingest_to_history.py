"""Append live-scraped completed matches into the historical CSV.

Reads `data/processed/live_matches_ingest.parquet` (produced by scraper.py
each cron tick) and merges new completed matches into
`data/processed/supplemental_matches_2025_2026.csv` (the same file the
existing predict_engine + Glicko refresh consume). Dedupes on
(tourney_name, round, winner, loser) so re-runs are idempotent.

Phase 4 piece of Session 18. Runs weekly via
`.github/workflows/refresh_ratings.yml`, before `retrain_glicko.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INGEST_PATH = REPO_ROOT / "data" / "processed" / "live_matches_ingest.parquet"
SUPPLEMENTAL_PATH = REPO_ROOT / "data" / "processed" / "supplemental_matches_2025_2026.csv"


# Tournament-name canonicalization: the live scraper writes
# "Internazionali BNL d'Italia" but the supplemental CSV uses
# "Italian Open" (matching the ATP calendar). Map known equivalences.
_NAME_ALIASES = {
    "Internazionali BNL d'Italia": "Italian Open",
    "Mutua Madrid Open": "Madrid Masters",
    "Monte Carlo Masters": "Monte Carlo Masters",
    "Barcelona Open": "Barcelona",
    "Roland Garros": "Roland Garros",
    "Wimbledon": "Wimbledon",
    "US Open": "US Open",
    "Australian Open": "Australian Open",
}


# Round-label canonicalization: live state uses Tennis Abstract labels
# (R128/R64/R32/R16/QF/SF/F); the supplemental CSV uses the ATP labels.
_ROUND_ALIASES = {
    "R128": "1st Round",
    "R64":  "2nd Round",
    "R32":  "3rd Round",
    "R16":  "4th Round",
    "QF":   "Quarterfinals",
    "SF":   "Semifinals",
    "F":    "The Final",
}


def _log(msg: str) -> None:
    sys.stderr.write(f"[ingest] {msg}\n")


def main() -> int:
    if not INGEST_PATH.exists():
        _log("no_ingest_file_yet — nothing to do")
        return 0
    try:
        import pandas as pd
    except ImportError:
        _log("pandas_unavailable — cannot ingest")
        return 1

    new = pd.read_parquet(INGEST_PATH)
    if new.empty:
        _log("ingest_file_empty — nothing to do")
        return 0

    new = new.rename(columns={
        "tournament": "tourney_name",
        "winner": "winner_name",
        "loser": "loser_name",
    })
    new["tourney_name"] = new["tourney_name"].replace(_NAME_ALIASES)
    new["round"] = new["round"].replace(_ROUND_ALIASES)
    # Add columns the supplemental CSV carries that we don't track yet.
    for col, default in [
        ("tourney_date", ""), ("tourney_level", ""),
        ("winner_rank", ""), ("loser_rank", ""), ("best_of", 3),
        ("court", ""), ("location", ""),
    ]:
        if col not in new.columns:
            new[col] = default
    new["surface"] = new.get("surface", "").astype(str).str.capitalize()

    out_cols = [
        "tourney_name", "tourney_date", "surface", "tourney_level", "round",
        "winner_name", "loser_name", "winner_rank", "loser_rank", "score",
        "best_of", "court", "location",
    ]
    new = new[[c for c in out_cols if c in new.columns]].copy()

    # Append-only ingest: do NOT dedupe rows already in the CSV (the
    # existing file has pre-existing duplicates from earlier sessions that
    # may be load-bearing for the Glicko retrain). Only suppress rows whose
    # (tourney_name, round, winner_name, loser_name) tuple is already
    # present. This is safe to re-run on every cron tick.
    if SUPPLEMENTAL_PATH.exists():
        existing = pd.read_csv(SUPPLEMENTAL_PATH)
        existing_keys = set(
            zip(
                existing["tourney_name"].astype(str),
                existing["round"].astype(str),
                existing["winner_name"].astype(str),
                existing["loser_name"].astype(str),
            )
        )
        new_keys = list(zip(
            new["tourney_name"].astype(str),
            new["round"].astype(str),
            new["winner_name"].astype(str),
            new["loser_name"].astype(str),
        ))
        mask = [k not in existing_keys for k in new_keys]
        new_unique = new[mask]
        if new_unique.empty:
            _log("no_new_rows — all live matches already in supplemental CSV")
            return 0
        combined = pd.concat([existing, new_unique], ignore_index=True)
        added = len(new_unique)
    else:
        combined = new
        added = len(new)

    combined.to_csv(SUPPLEMENTAL_PATH, index=False)
    _log(f"wrote {SUPPLEMENTAL_PATH} (+{added} rows, {len(combined)} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
