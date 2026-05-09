"""Precompute historical trends: yearly aggregates by metric and surface.

All numbers come from Sackmann main-tour CSVs (year-only filenames). The
optional MCP-derived metrics layer on top if parsed_points is reachable.

Output: data/processed/historical_trends.json
"""
from __future__ import annotations

import glob
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("trends")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "data" / "processed" / "historical_trends.json"


def load_sackmann_matches() -> pd.DataFrame:
    pattern = str(PROJECT_ROOT / "data" / "sackmann" / "tennis_atp" / "atp_matches_[0-9][0-9][0-9][0-9].csv")
    files = sorted(glob.glob(pattern))
    log.info(f"Loading {len(files)} year-only main-tour CSVs")
    dfs = [pd.read_csv(f, low_memory=False) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["year"] = pd.to_datetime(df["tourney_date"], format="%Y%m%d", errors="coerce").dt.year
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    log.info(f"Loaded {len(df):,} matches, {df['year'].min()}–{df['year'].max()}")
    return df


def by_surface_aggregate(df: pd.DataFrame, value_col: str, min_n: int = 50,
                         decimals: int = 2) -> dict:
    out = {}
    for surface in ["all", "Hard", "Clay", "Grass", "Carpet"]:
        sub = df if surface == "all" else df[df["surface"] == surface]
        agg = sub.groupby("year").agg(value=(value_col, "mean"),
                                      n=(value_col, "count")).reset_index()
        agg = agg[agg["n"] >= min_n]
        out[surface] = [
            {"year": int(r.year), "value": float(round(r.value, decimals)), "n": int(r.n)}
            for r in agg.itertuples()
        ]
    return out


def matches_per_year(df: pd.DataFrame) -> dict:
    out = {}
    for surface in ["all", "Hard", "Clay", "Grass", "Carpet"]:
        sub = df if surface == "all" else df[df["surface"] == surface]
        agg = sub.groupby("year").size().reset_index(name="value")
        out[surface] = [
            {"year": int(r.year), "value": int(r.value), "n": int(r.value)}
            for r in agg.itertuples()
        ]
    return out


def avg_combined_per_match(df: pd.DataFrame, w_col: str, l_col: str,
                            min_n: int = 50, decimals: int = 2) -> dict:
    df2 = df.dropna(subset=[w_col, l_col]).copy()
    df2["combined"] = df2[w_col] + df2[l_col]
    return by_surface_aggregate(df2, "combined", min_n=min_n, decimals=decimals)


def avg_match_minutes(df: pd.DataFrame) -> dict:
    df2 = df.dropna(subset=["minutes"]).copy()
    return by_surface_aggregate(df2, "minutes", decimals=1)


def avg_first_serve_pct(df: pd.DataFrame) -> dict:
    df2 = df.dropna(subset=["w_1stIn", "w_svpt", "l_1stIn", "l_svpt"]).copy()
    df2 = df2[(df2["w_svpt"] > 0) & (df2["l_svpt"] > 0)]
    df2["first_in_pct"] = (df2["w_1stIn"] + df2["l_1stIn"]) / (df2["w_svpt"] + df2["l_svpt"])
    return by_surface_aggregate(df2, "first_in_pct", decimals=4)


def avg_bp_save_pct(df: pd.DataFrame) -> dict:
    df2 = df.dropna(subset=["w_bpSaved", "w_bpFaced", "l_bpSaved", "l_bpFaced"]).copy()
    df2 = df2[(df2["w_bpFaced"] + df2["l_bpFaced"]) > 0]
    df2["bp_save_pct"] = (df2["w_bpSaved"] + df2["l_bpSaved"]) / (df2["w_bpFaced"] + df2["l_bpFaced"])
    return by_surface_aggregate(df2, "bp_save_pct", decimals=4)


def ace_rate_proxy(df: pd.DataFrame) -> dict:
    df2 = df.dropna(subset=["w_ace", "l_ace", "w_SvGms", "l_SvGms"]).copy()
    df2["aces"] = df2["w_ace"] + df2["l_ace"]
    df2["sv_games"] = df2["w_SvGms"] + df2["l_SvGms"]
    df2 = df2[df2["sv_games"] > 0]
    df2["rate"] = df2["aces"] / df2["sv_games"]
    return by_surface_aggregate(df2, "rate", decimals=4)


def mcp_metrics() -> dict:
    """Compute rally-level aggregates from parsed_points if available."""
    try:
        from src.api.data_loaders import load_parsed_points
        df = load_parsed_points(columns=None)
    except Exception as exc:
        log.warning(f"parsed_points unavailable, skipping MCP metrics: {exc}")
        return {}

    if "match_id" not in df.columns or "rally_length" not in df.columns:
        log.warning(f"parsed_points missing expected columns; cols={list(df.columns)[:10]}")
        return {}

    # Try to get year per point. parsed_points carries match_id like
    # "20240115-..." (Sackmann MCP convention).
    def parse_year(mid):
        try:
            return int(str(mid)[:4])
        except Exception:
            return None

    df["year"] = df["match_id"].map(parse_year)
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    df = df[df["year"] >= 2000]

    out = {}

    # Avg rally length per match (mean of per-point rally_length, by year)
    if "rally_length" in df.columns:
        rally_per_match = (df.groupby(["year", "match_id"])["rally_length"]
                            .mean().reset_index(name="rally_avg"))
        agg = rally_per_match.groupby("year").agg(
            value=("rally_avg", "mean"), n=("rally_avg", "count")
        ).reset_index()
        agg = agg[agg["n"] >= 30]
        out["avg_rally_length"] = {
            "label": "Average Rally Length (shots)",
            "description": "Mean shots per point, computed from charted-match data. Coverage is sparse and biased toward high-profile matches.",
            "source": "Match Charting Project (parsed_points.parquet)",
            "coverage_start": int(agg["year"].min()) if len(agg) else 2010,
            "by_surface": {
                "all": [
                    {"year": int(r.year), "value": float(round(r.value, 2)), "n": int(r.n)}
                    for r in agg.itertuples()
                ]
            },
        }

    return out


def main():
    df = load_sackmann_matches()

    metrics = {}
    metrics["matches_per_year"] = {
        "label": "Matches Played per Year",
        "description": "Total ATP main-draw matches recorded each year (Open Era).",
        "source": "Sackmann tennis_atp",
        "coverage_start": 1968,
        "by_surface": matches_per_year(df),
    }
    metrics["avg_aces_per_match"] = {
        "label": "Average Aces per Match",
        "description": "Sum of winner + loser aces per match, averaged by year.",
        "source": "Sackmann tennis_atp (serve stats logged from 1991)",
        "coverage_start": 1991,
        "by_surface": avg_combined_per_match(df, "w_ace", "l_ace", decimals=2),
    }
    metrics["avg_double_faults_per_match"] = {
        "label": "Average Double Faults per Match",
        "description": "Sum of winner + loser double faults per match.",
        "source": "Sackmann tennis_atp",
        "coverage_start": 1991,
        "by_surface": avg_combined_per_match(df, "w_df", "l_df", decimals=2),
    }
    metrics["avg_match_minutes"] = {
        "label": "Average Match Duration (minutes)",
        "description": "Average match length in minutes.",
        "source": "Sackmann tennis_atp",
        "coverage_start": 1991,
        "by_surface": avg_match_minutes(df),
    }
    metrics["avg_first_serve_pct"] = {
        "label": "Average First Serve In %",
        "description": "Combined first-serves-in / total service points across both players.",
        "source": "Sackmann tennis_atp",
        "coverage_start": 1991,
        "by_surface": avg_first_serve_pct(df),
    }
    metrics["avg_break_points_saved_pct"] = {
        "label": "Average Break Points Saved %",
        "description": "Combined break points saved / faced across both players.",
        "source": "Sackmann tennis_atp",
        "coverage_start": 1991,
        "by_surface": avg_bp_save_pct(df),
    }
    metrics["ace_rate_proxy_court_speed"] = {
        "label": "Ace Rate (Court Speed Proxy)",
        "description": "Aces per service game by year. A proxy for surface speed; not the ITF Court Pace Index.",
        "source": "Sackmann tennis_atp (derived)",
        "coverage_start": 1991,
        "is_proxy": True,
        "by_surface": ace_rate_proxy(df),
    }

    metrics.update(mcp_metrics())

    annotations = [
        {"year": 1973, "label": "Tiebreak introduced at majors"},
        {"year": 1979, "label": "Yellow ball adopted"},
        {"year": 1990, "label": "ATP Tour formed"},
        {"year": 2001, "label": "Wimbledon grass slowed"},
        {"year": 2006, "label": "Hawk-Eye introduced"},
        {"year": 2008, "label": "Australian Open switches to Plexicushion"},
        {"year": 2018, "label": "ATP Cup-era serve clock enforced"},
        {"year": 2020, "label": "COVID disruption (reduced calendar)"},
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "annotations": annotations,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    log.info(f"Wrote {OUT_PATH}: {len(metrics)} metrics, {len(annotations)} annotations")


if __name__ == "__main__":
    main()
