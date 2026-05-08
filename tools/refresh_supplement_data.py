"""
Refresh data/processed/supplemental_matches_2025_2026.csv by scraping
tennis-data.co.uk's per-week match results pages.

tennis-data.co.uk publishes weekly results files in xlsx/csv format at
predictable URLs:
  http://www.tennis-data.co.uk/2026/2026.xlsx   (cumulative ATP year file)

This script downloads the current year's file, normalizes it to the same
schema as the existing supplement CSV (tourney_name, tourney_date, surface,
tourney_level, round, winner_name, loser_name, winner_rank, loser_rank,
score, best_of, court, location), and merges into the existing CSV.

Falls back to a no-op if download fails — never wipes existing data.
"""

from __future__ import annotations

import io
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).parent.parent
OUTPUT = BASE / "data" / "processed" / "supplemental_matches_2025_2026.csv"

SOURCES = [
    "http://www.tennis-data.co.uk/2026/2026.xlsx",
    "http://www.tennis-data.co.uk/2025/2025.xlsx",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "*/*",
}


def _fetch_year(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200 or len(r.content) < 1000:
            print(f"  [skip] {url}: HTTP {r.status_code}, {len(r.content)}B")
            return None
        df = pd.read_excel(io.BytesIO(r.content))
        print(f"  [ok]   {url}: {len(df)} rows")
        return df
    except Exception as e:
        print(f"  [err]  {url}: {e}")
        return None


def _normalize(df: pd.DataFrame, source_url: str) -> pd.DataFrame:
    """Map tennis-data.co.uk schema to our supplement CSV schema."""
    out = pd.DataFrame()
    out["tourney_name"] = df.get("Tournament", "")
    if "Date" in df.columns:
        out["tourney_date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y%m%d")
    else:
        out["tourney_date"] = ""
    out["surface"] = df.get("Surface", "")
    out["tourney_level"] = df.get("Series", "")
    out["round"] = df.get("Round", "")
    out["winner_name"] = df.get("Winner", "")
    out["loser_name"] = df.get("Loser", "")
    out["winner_rank"] = pd.to_numeric(df.get("WRank"), errors="coerce")
    out["loser_rank"] = pd.to_numeric(df.get("LRank"), errors="coerce")
    # Score: combine W/L set games into "6-3 6-4" style if available
    score_parts = []
    for i in range(1, 6):
        w = df.get(f"W{i}")
        l = df.get(f"L{i}")
        if w is not None and l is not None:
            score_parts.append((w, l))
    if score_parts:
        scores = []
        for idx in range(len(df)):
            sets = []
            for w, l in score_parts:
                wv = w.iloc[idx] if hasattr(w, "iloc") else None
                lv = l.iloc[idx] if hasattr(l, "iloc") else None
                if pd.notna(wv) and pd.notna(lv):
                    sets.append(f"{int(wv)}-{int(lv)}")
            scores.append(" ".join(sets))
        out["score"] = scores
    else:
        out["score"] = ""
    out["best_of"] = pd.to_numeric(df.get("Best of"), errors="coerce").fillna(3).astype(int)
    out["court"] = df.get("Court", "Outdoor")
    out["location"] = df.get("Location", "")
    out = out.dropna(subset=["winner_name", "loser_name", "tourney_date"])
    out = out[out["tourney_date"].str.len() == 8]
    return out


def main():
    if not OUTPUT.exists():
        print(f"Existing supplement file missing at {OUTPUT}; creating fresh.")
        existing = pd.DataFrame()
    else:
        existing = pd.read_csv(OUTPUT)
        print(f"Existing supplement: {len(existing)} rows, max date={existing['tourney_date'].max()}")

    fetched = []
    for url in SOURCES:
        df = _fetch_year(url)
        if df is None:
            continue
        norm = _normalize(df, url)
        fetched.append(norm)

    if not fetched:
        print("No data fetched; supplement file unchanged.")
        return 0

    merged = pd.concat([existing] + fetched, ignore_index=True)
    # Dedupe on the natural key (tournament + date + winner + loser)
    merged = merged.drop_duplicates(
        subset=["tourney_date", "tourney_name", "winner_name", "loser_name"],
        keep="last",
    )
    # Sort by date so iteration is chronological
    merged["tourney_date"] = merged["tourney_date"].astype(str)
    merged = merged.sort_values("tourney_date")
    print(f"Merged: {len(merged)} rows, max date={merged['tourney_date'].max()}")

    if len(merged) <= len(existing):
        print(f"No new rows. (Existing {len(existing)} → merged {len(merged)}.)")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUTPUT, index=False)
    print(f"Wrote {OUTPUT}: +{len(merged) - len(existing)} new rows")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
