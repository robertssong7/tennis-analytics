"""Full Glicko-2 retrain on Sackmann + supplemental data.

This is a Glicko-only rebuild. It does NOT touch the ML feature matrix or
ensemble models. Output: data/processed/glicko2_state.pkl.

Match sources mirror predict_engine.py exactly (so name normalization and
filters stay in sync):
  - data/sackmann/tennis_atp/atp_matches_*.csv (excluding qual, futures,
    doubles, amateur, supplement)
  - data/processed/supplemental_matches_2025_2026.csv (mapped via
    _build_supplemental_name_map)

Pass is single chronological — snapshot then record_result for every match,
exactly like build_edge_features_v2.py's Glicko loop.
"""
import logging
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from modules.glicko2 import Glicko2RatingSystem  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("retrain_glicko")

DATA_DIR = PROJECT_ROOT / "data"
SACKMANN_DIR = DATA_DIR / "sackmann" / "tennis_atp"
SUPPLEMENTAL_CSV = DATA_DIR / "processed" / "supplemental_matches_2025_2026.csv"
OUTPUT = DATA_DIR / "processed" / "glicko2_state.pkl"

SURFACE_MAP = {
    "Hard": "hard", "Clay": "clay", "Grass": "grass", "Carpet": "carpet",
    "hard": "hard", "clay": "clay", "grass": "grass", "carpet": "carpet",
}


def load_sackmann() -> pd.DataFrame:
    files = sorted(SACKMANN_DIR.glob("atp_matches_*.csv"))
    files = [
        f for f in files
        if "qual" not in f.name and "futures" not in f.name
        and "doubles" not in f.name and "amateur" not in f.name
        and "supplement" not in f.name
    ]
    log.info(f"Loading {len(files)} main-tour CSVs")
    cols = ["winner_name", "loser_name", "surface", "tourney_date"]
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=cols, low_memory=False)
            dfs.append(df)
        except Exception as e:
            log.warning(f"  skip {f.name}: {e}")
    df = pd.concat(dfs, ignore_index=True)
    df = df.dropna(subset=["winner_name", "loser_name", "tourney_date"])
    df["tourney_date"] = pd.to_datetime(
        df["tourney_date"].astype(int).astype(str), format="%Y%m%d", errors="coerce"
    )
    df = df.dropna(subset=["tourney_date"])
    df = df[df["tourney_date"] >= pd.Timestamp("1968-01-01")]
    df["surface_clean"] = df["surface"].map(SURFACE_MAP).fillna("hard")
    log.info(f"Sackmann: {len(df):,} matches, "
             f"{df['tourney_date'].min().date()} to {df['tourney_date'].max().date()}")
    return df[["winner_name", "loser_name", "surface_clean", "tourney_date"]].rename(
        columns={"surface_clean": "surface"}
    )


def build_suppl_name_map(known_players: set) -> dict:
    """Mirror predict_engine._build_supplemental_name_map but using a set of
    canonical names from Sackmann (rather than a prior Glicko pickle)."""
    canonical_by_key: dict = {}
    for name in known_players:
        parts = name.split()
        if len(parts) < 2:
            continue
        first = parts[0]
        last = " ".join(parts[1:])
        key = (last.lower(), first[0].lower())
        canonical_by_key.setdefault(key, []).append(name)

    if not SUPPLEMENTAL_CSV.exists():
        return {}
    sup = pd.read_csv(SUPPLEMENTAL_CSV, usecols=["winner_name", "loser_name"])
    suppl_names = sorted(
        set(sup["winner_name"].dropna().unique())
        | set(sup["loser_name"].dropna().unique())
    )

    MANUAL_OVERRIDES = {
        "Auger-Aliassime F.": "Felix Auger Aliassime",
        "Mpetshi G.": "Giovanni Mpetshi Perricard",
        "Mpetshi Perricard G.": "Giovanni Mpetshi Perricard",
        "Struff J.L.": "Jan Lennard Struff",
        "Bu Y.": "Yunchaokete Bu",
        "O Connell C.": "Christopher O Connell",
        "O'Connell C.": "Christopher O Connell",
        "Cerundolo J.M.": "Juan Manuel Cerundolo",
        "Tseng C.H.": "Chun Hsin Tseng",
        "Zhang Zh.": "Zhizhen Zhang",
        "Tirante T.A.": "Thiago Agustin Tirante",
        "Barrios M.": "Tomas Barrios Vera",
        "Galan D.E.": "Daniel Elahi Galan",
        "Ficovich J.P.": "Juan Pablo Ficovich",
        "Blanch Dar.": "Darwin Blanch",
        "Bailly G.A.": "Gilles Arnaud Bailly",
        "Royer V.": "Valentin Royer",
        "Boyer T.": "Timofej Skatov",
    }
    name_map = {}
    for short, full in MANUAL_OVERRIDES.items():
        if full in known_players:
            name_map[short] = full
    for sname in suppl_names:
        sname = str(sname).strip()
        if not sname or sname == "nan" or sname in name_map:
            continue
        parts = sname.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        last_part = parts[0].strip()
        initial_part = parts[1].strip().rstrip(".")
        if not initial_part:
            continue
        key = (last_part.lower(), initial_part[0].lower())
        candidates = canonical_by_key.get(key, [])
        if len(candidates) >= 1:
            name_map[sname] = candidates[0]
    return name_map


def load_supplement(known_players: set) -> pd.DataFrame:
    if not SUPPLEMENTAL_CSV.exists():
        return pd.DataFrame(columns=["winner_name", "loser_name", "surface", "tourney_date"])
    name_map = build_suppl_name_map(known_players)
    sup = pd.read_csv(SUPPLEMENTAL_CSV)
    sup = sup.dropna(subset=["winner_name", "loser_name", "tourney_date"])
    sup["winner_name"] = sup["winner_name"].astype(str).str.strip().map(name_map)
    sup["loser_name"] = sup["loser_name"].astype(str).str.strip().map(name_map)
    sup = sup.dropna(subset=["winner_name", "loser_name"])
    sup["tourney_date"] = pd.to_datetime(
        sup["tourney_date"].astype(int).astype(str), format="%Y%m%d", errors="coerce"
    )
    sup = sup.dropna(subset=["tourney_date"])
    sup["surface"] = sup["surface"].astype(str).str.lower().map(
        lambda s: s if s in ("hard", "clay", "grass", "carpet") else "hard"
    )
    log.info(f"Supplement: {len(sup):,} mapped matches, "
             f"{sup['tourney_date'].min().date()} to {sup['tourney_date'].max().date()}")
    return sup[["winner_name", "loser_name", "surface", "tourney_date"]]


def run() -> None:
    sack = load_sackmann()
    known = set(sack["winner_name"]) | set(sack["loser_name"])
    sup = load_supplement(known)

    matches = pd.concat([sack, sup], ignore_index=True)
    matches = matches.sort_values("tourney_date", kind="mergesort").reset_index(drop=True)
    log.info(f"Combined: {len(matches):,} matches, retraining Glicko-2...")

    glicko = Glicko2RatingSystem()
    t0 = time.time()
    n = len(matches)
    for i, row in enumerate(matches.itertuples(index=False), 1):
        winner = row.winner_name
        loser = row.loser_name
        surface = row.surface
        match_date = row.tourney_date.date()
        glicko.snapshot(winner, surface, match_date)
        glicko.snapshot(loser, surface, match_date)
        glicko.record_result(winner, loser, surface, match_date)
        if i % 50000 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (n - i) / rate
            log.info(f"  {i:,}/{n:,} matches ({rate:.0f}/s, ETA {eta/60:.1f} min)")

    log.info(f"Done in {(time.time() - t0) / 60:.1f} min. "
             f"{len(glicko.ratings):,} unique players rated.")
    with open(OUTPUT, "wb") as f:
        pickle.dump(glicko, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    run()
