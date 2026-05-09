"""Mine candidate stat-of-the-day items from local match data + Glicko state.

Six categories:
  1. active_streak       — current win streaks of N+ matches
  2. surface_specialist  — largest cross-surface Glicko gaps (active top-50)
  3. age_anomaly         — recent main-tour wins by 36+ year-olds
  4. h2h_breakthrough    — first-ever H2H win after 3+ losses
  5. tournament_pattern  — career match-win totals at a single tournament
  6. rating_jump         — current Glicko vs peak Glicko (under/overperformer)

Each candidate has a category, a `facts` dict (only fields the templates
reference), and a novelty_score in [0,1].
"""
from __future__ import annotations

import glob
import json
import logging
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mine")

DATA_DIR = PROJECT_ROOT / "data"
SACKMANN_DIR = DATA_DIR / "sackmann" / "tennis_atp"
SUPPL_CSV = DATA_DIR / "processed" / "supplemental_matches_2025_2026.csv"
GLICKO_PATH = DATA_DIR / "processed" / "glicko2_state.pkl"
PEAK_ELO_PATH = DATA_DIR / "processed" / "peak_elo.json"
OUT_PATH = DATA_DIR / "processed" / "stat_candidates.json"


def load_combined_matches() -> pd.DataFrame:
    """Sackmann main-tour CSVs + supplement (mapped), sorted chronologically."""
    pattern = str(SACKMANN_DIR / "atp_matches_[0-9][0-9][0-9][0-9].csv")
    files = sorted(glob.glob(pattern))
    cols = ["winner_name", "loser_name", "surface", "tourney_date",
            "tourney_name", "winner_age", "loser_age"]
    sack_dfs = []
    for f in files[-5:]:  # last 5 years is plenty for "recent" miners
        try:
            df = pd.read_csv(f, usecols=cols, low_memory=False)
            sack_dfs.append(df)
        except Exception:
            continue
    sack = pd.concat(sack_dfs, ignore_index=True)
    sack["date"] = pd.to_datetime(
        sack["tourney_date"].astype(int).astype(str), format="%Y%m%d", errors="coerce"
    )
    sack = sack.dropna(subset=["date", "winner_name", "loser_name"])

    # Supplement: rename columns to match, surface to match
    sup = pd.read_csv(SUPPL_CSV)
    sup = sup.dropna(subset=["winner_name", "loser_name", "tourney_date"])
    sup["date"] = pd.to_datetime(
        sup["tourney_date"].astype(int).astype(str), format="%Y%m%d", errors="coerce"
    )
    sup = sup.dropna(subset=["date"])
    # Map supplement names to canonical via predict_engine helper
    from src.api.predict_engine import _build_supplemental_name_map
    canonical = list(_load_glicko_names())
    name_map = _build_supplemental_name_map(canonical)
    sup["winner_name"] = sup["winner_name"].astype(str).str.strip().map(name_map)
    sup["loser_name"] = sup["loser_name"].astype(str).str.strip().map(name_map)
    sup = sup.dropna(subset=["winner_name", "loser_name"])
    sup["winner_age"] = None
    sup["loser_age"] = None
    sup["surface"] = sup["surface"].astype(str).str.capitalize()

    keep = ["winner_name", "loser_name", "surface", "tourney_name",
            "winner_age", "loser_age", "date"]
    df = pd.concat([sack[keep], sup[keep]], ignore_index=True)
    df = df.sort_values("date").reset_index(drop=True)
    log.info(f"Combined matches loaded: {len(df):,} ({df['date'].min().date()} → {df['date'].max().date()})")
    return df


def _load_glicko_names() -> set:
    if not GLICKO_PATH.exists():
        return set()
    with open(GLICKO_PATH, "rb") as f:
        state = pickle.load(f)
    return set(state.ratings.keys())


def find_active_streaks(df: pd.DataFrame, min_streak: int = 8) -> list:
    """Walk chronologically, track per-player current win streak.

    A streak resets to 0 on any loss. A player whose latest match is a win
    and whose pre-streak loss is at least `min_streak` matches behind is a
    candidate. Streak start_date / start_tourney captured at streak inception.
    """
    streaks: dict = defaultdict(int)
    streak_start: dict = {}
    last_played: dict = {}
    for _, row in df.iterrows():
        w, l = row["winner_name"], row["loser_name"]
        if streaks[w] == 0:
            streak_start[w] = (row["date"], row["tourney_name"])
        streaks[w] += 1
        last_played[w] = row["date"]
        streaks[l] = 0
        last_played[l] = row["date"]
    today = datetime.now()
    out = []
    for p, s in streaks.items():
        if s < min_streak:
            continue
        # Only surface streaks where the last match was within the past 90d.
        last = last_played.get(p)
        if last is None or (today - last).days > 90:
            continue
        start_d, start_t = streak_start.get(p, (None, ""))
        out.append({
            "category": "active_streak",
            "facts": {
                "player": p,
                "n": int(s),
                "start_date": start_d.strftime("%B %-d") if start_d is not None else "",
                "start_tourney": start_t or "",
                "today": today.strftime("%B %-d"),
            },
            "novelty_score": float(min(1.0, s / 25)),
        })
    return out


def find_surface_specialists() -> list:
    """Largest cross-surface Glicko mu gaps among active top-50 by 'all' mu."""
    if not GLICKO_PATH.exists():
        return []
    with open(GLICKO_PATH, "rb") as f:
        state = pickle.load(f)
    players = []
    today = datetime.now().date()
    for name, surfs in state.ratings.items():
        all_r = surfs.get("all")
        if all_r is None:
            continue
        last = getattr(all_r, "last_match_date", None)
        if last is None or (today - last).days > 365:
            continue
        players.append((name, all_r.mu, surfs))
    players.sort(key=lambda x: x[1], reverse=True)
    top50 = players[:50]
    out = []
    for name, _, surfs in top50:
        per_surf = []
        for k in ("hard", "clay", "grass"):
            r = surfs.get(k)
            if r is not None:
                per_surf.append((k, float(r.mu)))
        if len(per_surf) < 3:
            continue
        per_surf.sort(key=lambda x: x[1])
        worst, best = per_surf[0], per_surf[-1]
        gap = best[1] - worst[1]
        if gap < 200:
            continue
        out.append({
            "category": "surface_specialist",
            "facts": {
                "player": name,
                "best_surface": best[0].capitalize(),
                "worst_surface": worst[0].capitalize(),
                "gap": int(round(gap)),
                "best_rating": int(round(best[1])),
                "worst_rating": int(round(worst[1])),
            },
            "novelty_score": float(min(1.0, gap / 500)),
        })
    return sorted(out, key=lambda x: x["novelty_score"], reverse=True)[:8]


def find_age_anomalies(df: pd.DataFrame) -> list:
    """Recent main-tour wins by 36+ year-olds. Sackmann-only (supplement
    has no winner_age)."""
    df2 = df.dropna(subset=["winner_age"]).copy()
    df2 = df2[df2["date"] >= (datetime.now() - timedelta(days=365))]
    out = []
    for _, row in df2.iterrows():
        age = float(row["winner_age"])
        if age < 36:
            continue
        out.append({
            "category": "age_anomaly",
            "facts": {
                "player": row["winner_name"],
                "age": float(round(age, 1)),
                "tourney": row["tourney_name"],
                "date": row["date"].strftime("%B %-d, %Y"),
                "opponent": row["loser_name"],
            },
            "novelty_score": float(min(1.0, (age - 30) / 12)),
        })
    return sorted(out, key=lambda x: x["novelty_score"], reverse=True)[:10]


def find_h2h_breakthroughs(df: pd.DataFrame) -> list:
    """First-ever H2H win after 3+ losses, in the past 60d."""
    cutoff = datetime.now() - timedelta(days=60)
    recent = df[df["date"] >= cutoff].copy()
    out = []
    for _, row in recent.iterrows():
        winner, loser = row["winner_name"], row["loser_name"]
        pair_mask = (
            ((df["winner_name"] == winner) & (df["loser_name"] == loser))
            | ((df["winner_name"] == loser) & (df["loser_name"] == winner))
        )
        prior = df[pair_mask & (df["date"] < row["date"])]
        if len(prior) < 3:
            continue
        prior_wins = prior[prior["winner_name"] == winner]
        if len(prior_wins) > 0:
            continue
        out.append({
            "category": "h2h_breakthrough",
            "facts": {
                "winner": winner,
                "loser": loser,
                "previous_meetings": int(len(prior)),
                "tourney": row["tourney_name"],
                "date": row["date"].strftime("%B %-d, %Y"),
            },
            "novelty_score": float(min(1.0, len(prior) / 8)),
        })
    return out


def find_tournament_records(df: pd.DataFrame) -> list:
    """Players passing milestone career-win totals at a single tournament."""
    cutoff = datetime.now() - timedelta(days=30)
    recent = df[df["date"] >= cutoff]
    out = []
    seen_pairs = set()
    for _, row in recent.iterrows():
        player, tourney = row["winner_name"], row["tourney_name"]
        if (player, tourney) in seen_pairs:
            continue
        seen_pairs.add((player, tourney))
        history = df[
            (df["tourney_name"] == tourney)
            & ((df["winner_name"] == player) | (df["loser_name"] == player))
            & (df["date"] < row["date"])
        ]
        wins_at_tourney = int(len(history[history["winner_name"] == player]))
        if wins_at_tourney + 1 < 15:
            continue
        out.append({
            "category": "tournament_pattern",
            "facts": {
                "player": player,
                "tourney": tourney,
                "career_wins_here": int(wins_at_tourney + 1),
                "date": row["date"].strftime("%B %-d, %Y"),
            },
            "novelty_score": float(min(1.0, (wins_at_tourney + 1) / 60)),
        })
    return out


def find_rating_jumps() -> list:
    """Players whose current Glicko is far above or below their peak.

    Substitute for the weekly snapshot history we don't have. Uses
    peak_elo.json + glicko2_state.pkl current mu.
    """
    if not GLICKO_PATH.exists() or not PEAK_ELO_PATH.exists():
        return []
    with open(GLICKO_PATH, "rb") as f:
        state = pickle.load(f)
    with open(PEAK_ELO_PATH) as f:
        peaks = json.load(f)
    today = datetime.now().date()
    out = []
    for name, surfs in state.ratings.items():
        all_r = surfs.get("all")
        if all_r is None:
            continue
        last = getattr(all_r, "last_match_date", None)
        if last is None or (today - last).days > 90:
            continue
        peak = peaks.get(name)
        if not peak or peak.get("peak_elo") is None:
            continue
        cur = float(all_r.mu)
        peak_v = float(peak["peak_elo"])
        delta = cur - peak_v
        if abs(delta) < 80:
            continue
        out.append({
            "category": "rating_jump",
            "facts": {
                "player": name,
                "delta": int(round(delta)),
                "direction": "above" if delta > 0 else "below",
                "current_rating": int(round(cur)),
                "peak_rating": int(round(peak_v)),
                "peak_year": int(peak.get("peak_year", 0)),
            },
            "novelty_score": float(min(1.0, abs(delta) / 200)),
        })
    return sorted(out, key=lambda x: x["novelty_score"], reverse=True)[:10]


def main():
    df = load_combined_matches()
    candidates = []
    candidates.extend(find_active_streaks(df))
    log.info(f"  active_streak candidates: {len([c for c in candidates if c['category']=='active_streak'])}")
    candidates.extend(find_surface_specialists())
    log.info(f"  surface_specialist added")
    candidates.extend(find_age_anomalies(df))
    log.info(f"  age_anomaly added")
    candidates.extend(find_h2h_breakthroughs(df))
    log.info(f"  h2h_breakthrough added")
    candidates.extend(find_tournament_records(df))
    log.info(f"  tournament_pattern added")
    candidates.extend(find_rating_jumps())
    log.info(f"  rating_jump added")
    candidates.sort(key=lambda x: x["novelty_score"], reverse=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(candidates),
        "candidates": candidates[:30],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)
    log.info(f"Wrote {OUT_PATH}: {len(candidates):,} total, top {len(out['candidates'])} kept")


if __name__ == "__main__":
    main()
