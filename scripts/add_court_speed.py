"""
Join courtspeed.com CPI data to match data.
Adds cpi, ball_type, elevation columns to universal_features.parquet.
"""
import pandas as pd
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Sackmann tourney_name -> courtspeed.com tournament name
NAME_MAP = {
    "Australian Open": "Australian Open",
    "Indian Wells Masters": "Indian Wells",
    "Miami Masters": "Miami",
    "Monte Carlo Masters": "Monte Carlo",
    "Madrid Masters": "Madrid",
    "Rome Masters": "Rome",
    "Roland Garros": "Roland Garros",
    "Wimbledon": "Wimbledon",
    "Canada Masters": "Canadian Open",
    "Cincinnati Masters": "Cincinnati",
    "US Open": "US Open",
    "Us Open": "US Open",
    "Shanghai Masters": "Shanghai",
    "Paris Masters": "Paris",
    "Tour Finals": "ATP Finals",
    "ATP Finals": "ATP Finals",
}

def main():
    cpi = pd.read_csv(REPO_ROOT / "data" / "court_speed.csv")
    uf = pd.read_parquet(REPO_ROOT / "data" / "processed" / "universal_features.parquet")

    # Map Sackmann names to CPI names
    uf["cpi_tournament"] = uf["tourney_name"].map(NAME_MAP)

    # Extract year from tourney_date
    uf["tourney_year"] = uf["tourney_date"].astype(str).str[:4].astype(int)

    # Build CPI lookup: (tournament, year) -> cpi
    cpi_lookup = {}
    for _, row in cpi.iterrows():
        if row["cpi"] and row["cpi"] > 0:
            cpi_lookup[(row["tournament"], row["year"])] = {
                "cpi": row["cpi"],
                "ball_type": row["ball_type"],
                "elevation": row["elevation"],
            }

    # Join
    cpi_vals = []
    ball_vals = []
    elev_vals = []
    matched = 0
    for _, row in uf.iterrows():
        key = (row["cpi_tournament"], row["tourney_year"])
        if key in cpi_lookup:
            cpi_vals.append(cpi_lookup[key]["cpi"])
            ball_vals.append(cpi_lookup[key]["ball_type"])
            elev_vals.append(cpi_lookup[key]["elevation"])
            matched += 1
        else:
            cpi_vals.append(None)
            ball_vals.append(None)
            elev_vals.append(None)

    uf["cpi"] = cpi_vals
    uf["ball_type"] = ball_vals
    uf["elevation"] = elev_vals

    # Drop temp columns
    uf.drop(columns=["cpi_tournament", "tourney_year"], inplace=True)

    # Stats
    total = len(uf)
    has_cpi = uf["cpi"].notna().sum()
    print(f"Total matches: {total}")
    print(f"Matches with CPI: {has_cpi} ({has_cpi/total*100:.1f}%)")
    print(f"\nCPI coverage by tournament:")
    with_cpi = uf[uf["cpi"].notna()]
    print(with_cpi.groupby("tourney_name")["cpi"].agg(["count", "mean", "min", "max"]).sort_values("count", ascending=False).to_string())

    # Save
    uf.to_parquet(REPO_ROOT / "data" / "processed" / "universal_features.parquet", index=False)
    print(f"\nSaved updated universal_features.parquet with CPI column")

if __name__ == "__main__":
    main()
