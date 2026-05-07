"""
Refresh data/processed/live_tournament.json from the ATP 2026 calendar
+ supplemental match results. Run daily by GitHub Actions so the
tournament feed stays current even when tennis-data.co.uk lags.

Outputs the same shape /api/live-tournament returns: live, just_finished,
next_upcoming with results from supplemental_matches_2025_2026.csv.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent.parent
CALENDAR = BASE / "data" / "processed" / "atp_calendar_2026.json"
SUPPL = BASE / "data" / "processed" / "supplemental_matches_2025_2026.csv"
OUTPUT = BASE / "data" / "processed" / "live_tournament.json"


def _load_calendar() -> list:
    return json.loads(CALENDAR.read_text())


def _live(today: date, cal: list):
    for t in cal:
        if datetime.fromisoformat(t["start"]).date() <= today <= datetime.fromisoformat(t["end"]).date():
            return t
    return None


def _just_finished(today: date, cal: list):
    finished = [
        t
        for t in cal
        if datetime.fromisoformat(t["end"]).date() < today
        and t.get("category") in ("Masters 1000", "Grand Slam", "ATP Finals")
    ]
    return max(finished, key=lambda t: t["end"]) if finished else None


def _next_upcoming(today: date, cal: list):
    upcoming = [t for t in cal if datetime.fromisoformat(t["start"]).date() > today]
    return min(upcoming, key=lambda t: t["start"]) if upcoming else None


def _aliases(name: str) -> list[str]:
    n = name.lower()
    out = [n]
    if "italian" in n or "rome" in n:
        out += ["italian", "rome", "internazionali"]
    if "madrid" in n:
        out += ["madrid"]
    if "monte carlo" in n:
        out += ["monte carlo"]
    if "indian wells" in n:
        out += ["indian wells", "bnp"]
    if "miami" in n:
        out += ["miami"]
    if "cincinnati" in n:
        out += ["cincinnati"]
    if "canadian" in n:
        out += ["canadian", "toronto"]
    return out


def _results_for(name: str, year: int) -> list[dict]:
    if not SUPPL.exists():
        return []
    df = pd.read_csv(SUPPL)
    df = df.dropna(subset=["winner_name", "loser_name", "tourney_name"])
    df["tourney_date"] = pd.to_numeric(df["tourney_date"], errors="coerce").fillna(0).astype(int)
    mask = False
    for a in _aliases(name):
        mask = mask | df["tourney_name"].str.lower().str.contains(a, na=False)
    sub = df[mask & (df["tourney_date"] >= year * 10000) & (df["tourney_date"] < (year + 1) * 10000)]
    if len(sub) == 0:
        sub = df[mask]
    sub = sub.sort_values("tourney_date")
    return [
        {
            "winner": str(r.get("winner_name", "")),
            "loser": str(r.get("loser_name", "")),
            "score": str(r.get("score", "")),
            "round": str(r.get("round", "")),
        }
        for _, r in sub.iterrows()
    ]


def _to_feed(t: dict | None, status: str) -> dict | None:
    if not t:
        return None
    start_d = datetime.fromisoformat(t["start"]).date()
    end_d = datetime.fromisoformat(t["end"]).date()
    if start_d.month == end_d.month:
        dates_str = f"{start_d.strftime('%b %-d')}-{end_d.day}, {end_d.year}"
    else:
        dates_str = f"{start_d.strftime('%b %-d')} - {end_d.strftime('%b %-d')}, {end_d.year}"
    year = int(t["start"][:4])
    results = _results_for(t["name"], year)
    return {
        "tournament": t["name"],
        "year": year,
        "dates": dates_str,
        "location": f"{t.get('city', '')}, {t.get('country', '')}".strip(", "),
        "surface": (t.get("surface") or "").capitalize(),
        "level": t.get("category"),
        "indoor_outdoor": "Indoor" if t.get("indoor") else "Outdoor",
        "status": status,
        "results": results[-20:],
        "data_available": len(results) > 0,
    }


def main():
    today = date.today()
    cal = _load_calendar()
    out = {
        "live": _to_feed(_live(today, cal), "Live"),
        "just_finished": _to_feed(_just_finished(today, cal), "Complete"),
        "next_upcoming": _to_feed(_next_upcoming(today, cal), "Upcoming"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    OUTPUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUTPUT}")
    for key in ("live", "just_finished", "next_upcoming"):
        v = out[key]
        if v:
            print(f"  {key}: {v['tournament']} ({v['dates']}) — {len(v['results'])} results")
        else:
            print(f"  {key}: <none>")


if __name__ == "__main__":
    sys.exit(main() or 0)
