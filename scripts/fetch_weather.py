"""
Weather Cache Builder for TennisIQ (V2)
========================================
Fetches historical daily weather from Open-Meteo for ATP tournament locations.
Builds data/processed/weather_cache.parquet used by build_edge_features_v2.py.

API: Open-Meteo historical archive (free, no key needed).
     https://archive-api.open-meteo.com/v1/archive

Features per match day:
  temp_max    (°C)   — hot days → faster bounce, harder on clay grinders
  precip      (mm)   — rain (especially meaningful on grass/clay)
  wind_max    (km/h) — wind disrupts big servers; benefits accurate baseline
  humidity    (%)    — dry = faster court, humid = heavier ball/slower surface

Derived match features:
  is_hot        — temp_max > 28°C
  is_windy      — wind_max > 25 km/h
  is_wet_day    — precip > 1mm
  heat_index    — temp_max × (1 + humidity/100)  [physical load proxy]
  surface_speed_mod — CPI adjustment: hot+low precip → faster; cold/wet → slower

Usage:
  python3 scripts/fetch_weather.py [--start 2000] [--end 2024]

Output:
  data/processed/weather_cache.parquet
    Columns: tourney_date (str YYYYMMDD), tourney_name_key (str, lowercased),
             temp_max, precip, wind_max, humidity
"""

import glob
import json
import time
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SACKMANN_PATTERN = str(PROJECT_ROOT / "data" / "sackmann" / "tennis_atp" / "atp_matches_*.csv")
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "weather_cache.parquet"

# Open-Meteo API — free, no key required
OPEN_METEO_BASE = "https://archive-api.open-meteo.com/v1/archive"

# Tournament name → (lat, lon)
# Normalized keys (lowercase). Matching uses 'in' check.
TOURNAMENT_COORDS = {
    "australian open":       (-37.8247, 144.9783),
    "roland garros":         (48.8462,   2.2496),
    "wimbledon":             (51.4340,  -0.2149),
    "us open":               (40.7282,  -73.8468),
    "indian wells":          (33.7363, -116.3109),
    "miami":                 (25.7093,  -80.2381),
    "monte-carlo":           (43.7384,   7.4246),
    "monte carlo":           (43.7384,   7.4246),
    "madrid":                (40.4534,  -3.6883),
    "rome":                  (41.9282,  12.4534),
    "italian open":          (41.9282,  12.4534),
    "internazionali":        (41.9282,  12.4534),
    "canadian open":         (43.7282,  -79.3831),
    "rogers cup":            (43.7282,  -79.3831),
    "toronto":               (43.7282,  -79.3831),
    "montreal":              (45.5017,  -73.5673),
    "cincinnati":            (39.1031,  -84.5120),
    "western & southern":    (39.1031,  -84.5120),
    "shanghai":              (31.1775,  121.4737),
    "paris":                 (48.8396,   2.3780),
    "atp finals":            (45.0703,   7.6869),
    "nitto atp":             (45.0703,   7.6869),
    "barclays":              (51.5384,  -0.0754),
    "barcelona":             (41.3569,   2.1450),
    "munich":                (48.2188,  11.5820),
    "halle":                 (51.9607,   8.5517),
    "queens":                (51.4864,  -0.2066),
    "eastbourne":            (50.7690,   0.2799),
    "mallorca":              (39.6953,   3.0176),
    "stuttgart":             (48.7758,   9.1829),
    "vienna":                (48.2082,  16.3738),
    "swiss indoors":         (47.5596,   7.5886),
    "basel":                 (47.5596,   7.5886),
    "stockholm":             (59.3293,  18.0686),
    "antwerp":               (51.2194,   4.4025),
    "washington":            (38.8951,  -77.0364),
    "citi open":             (38.8951,  -77.0364),
    "hamburg":               (53.5753,  10.0153),
    "acapulco":              (16.8531,  -99.8237),
    "rio":                   (-22.9068, -43.1729),
    "buenos aires":          (-34.6037, -58.3816),
    "santiago":              (-33.4569, -70.6483),
    "houston":               (29.7604,  -95.3698),
    "istanbul":              (41.0082,  28.9784),
    "estoril":               (38.7436,  -9.3014),
    "marrakech":             (31.6295,  -7.9811),
    "dubai":                 (25.2048,  55.2708),
    "doha":                  (25.2854,  51.5310),
    "qatar":                 (25.2854,  51.5310),
    "auckland":              (-36.8485, 174.7633),
    "sydney":                (-33.8688, 151.2093),
    "brisbane":              (-27.4698, 153.0251),
    "hobart":                (-42.8821, 147.3272),
    "adelaide":              (-34.9285, 138.6007),
    "beijing":               (39.9042,  116.4074),
    "shenzhen":              (22.5431,  114.0579),
    "chengdu":               (30.5728,  104.0668),
    "moselle":               (49.1193,   6.1757),
    "metz":                  (49.1193,   6.1757),
    "lyon":                  (45.7640,   4.8357),
    "rotterdam":             (51.9225,   4.4792),
    "marseille":             (43.2965,   5.3698),
    "montpellier":           (43.6108,   3.8767),
    "dallas":                (32.7767,  -96.7970),
    "delray beach":          (26.4615,  -80.0728),
    "winston-salem":         (36.0999,  -80.2442),
    "umag":                  (45.4362,  13.5192),
    "kitzbuhel":             (47.4474,  12.3914),
    "gstaad":                (46.4749,   7.2882),
    "bastad":                (56.4282,  12.8526),
    "atlanta":               (33.7490,  -84.3880),
    "los cabos":             (22.8905, -109.9167),
    "sofia":                 (42.6977,  23.3219),
    "st. petersburg":        (59.9343,  30.3351),
    "st petersburg":         (59.9343,  30.3351),
    "memphis":               (35.1495,  -90.0490),
    "san jose":              (37.3382, -121.8863),
    "london":                (51.5074,  -0.1278),
}


def match_coords(tourney_name: str):
    """Match tournament name to (lat, lon). Returns None if no match."""
    name_lower = tourney_name.lower().strip()
    for key, coords in TOURNAMENT_COORDS.items():
        if key in name_lower or name_lower in key:
            return coords
    # Partial word match
    for key, coords in TOURNAMENT_COORDS.items():
        for word in key.split():
            if len(word) >= 4 and word in name_lower:
                return coords
    return None


def fetch_year(lat: float, lon: float, year: int, retries: int = 3) -> list:
    """Fetch one full year of daily weather for a lat/lon. Returns list of row dicts."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "daily": "temperature_2m_max,precipitation_sum,windspeed_10m_max,relative_humidity_2m_max",
        "timezone": "auto",
    }
    url = OPEN_METEO_BASE + "?" + urllib.parse.urlencode(params)

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())

            daily = data.get("daily", {})
            dates = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            precips = daily.get("precipitation_sum", [])
            winds = daily.get("windspeed_10m_max", [])
            humids = daily.get("relative_humidity_2m_max", [])

            rows = []
            for d, t, p, w, h in zip(dates, temps, precips, winds, humids):
                rows.append({
                    "date_str": d.replace("-", ""),  # YYYYMMDD
                    "lat_r": round(lat, 2),
                    "lon_r": round(lon, 2),
                    "temp_max": t,
                    "precip": p,
                    "wind_max": w,
                    "humidity": h,
                })
            return rows

        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 60 * (attempt + 1)
                print(f"    Rate limited — waiting {wait}s ...")
                time.sleep(wait)
            else:
                print(f"    HTTP {e.code} lat={lat} lon={lon} year={year}")
                return []
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                print(f"    Failed: {e}")
                return []
    return []


def build_weather_cache(start_year: int = 2000, end_year: int = 2024):
    """
    Main entry point: builds weather_cache.parquet from Sackmann match data.
    """
    # ---- Load Sackmann to get unique (tourney_name, year) combos ----
    print("Scanning Sackmann CSVs for tournament-year pairs...")
    files = sorted(glob.glob(SACKMANN_PATTERN))
    if not files:
        raise FileNotFoundError(f"No CSVs at {SACKMANN_PATTERN}")

    all_tourney_years = set()
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["tourney_name", "tourney_date"],
                             low_memory=False)
            df["year"] = df["tourney_date"].astype(str).str[:4].astype(int, errors="ignore")
            df["tourney_name"] = df["tourney_name"].astype(str)
            for _, row in df[["tourney_name", "year"]].drop_duplicates().iterrows():
                all_tourney_years.add((row["tourney_name"], int(row["year"])))
        except Exception:
            continue

    print(f"  Found {len(all_tourney_years):,} unique tournament-year pairs")

    # ---- Map to (lat, lon, year) — deduplicate by location ----
    loc_year_to_tourneys = defaultdict(list)
    unmatched = set()
    for tourney, year in all_tourney_years:
        if year < start_year or year > end_year:
            continue
        coords = match_coords(tourney)
        if coords:
            lat_r, lon_r = round(coords[0], 2), round(coords[1], 2)
            loc_year_to_tourneys[(lat_r, lon_r, year)].append(tourney)
        else:
            unmatched.add(tourney)

    print(f"  Matched: {len(loc_year_to_tourneys)} location-year pairs")
    print(f"  Unmatched tournaments: {len(unmatched)}")

    # ---- Load existing cache ----
    existing_keys = set()
    existing_rows = []
    if OUTPUT_FILE.exists():
        existing = pd.read_parquet(OUTPUT_FILE)
        existing_rows = existing.to_dict("records")
        existing_keys = set(
            zip(existing["date_str"].astype(str),
                existing["lat_r"].round(2),
                existing["lon_r"].round(2))
        )
        print(f"  Existing cache: {len(existing_rows):,} rows, "
              f"{len(existing_keys):,} unique (date, loc) keys")

    # ---- Fetch missing location-years ----
    new_rows = []
    total = len(loc_year_to_tourneys)
    done = 0
    skipped = 0

    for (lat_r, lon_r, year), tourneys in sorted(loc_year_to_tourneys.items()):
        # Check if we already have Jan 1 of this year for this location
        sample_key = (f"{year}0101", lat_r, lon_r)
        if sample_key in existing_keys:
            skipped += 1
            done += 1
            continue

        done += 1
        if done % 10 == 0:
            print(f"  {done}/{total} | {len(new_rows):,} new rows | skip={skipped}")

        rows = fetch_year(lat_r, lon_r, year)
        new_rows.extend(rows)

        # Polite: ~2 calls/sec to avoid rate limits
        time.sleep(0.55)

    print(f"\nFetched {len(new_rows):,} new rows from {done - skipped} API calls")

    # ---- Combine, dedup, save ----
    all_rows = existing_rows + new_rows
    if not all_rows:
        print("No data to save.")
        return

    df = pd.DataFrame(all_rows)
    df["lat_r"] = df["lat_r"].round(2)
    df["lon_r"] = df["lon_r"].round(2)
    df = df.drop_duplicates(subset=["date_str", "lat_r", "lon_r"])
    df = df.sort_values(["lat_r", "lon_r", "date_str"]).reset_index(drop=True)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_FILE, index=False)
    size = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"Saved weather cache: {len(df):,} rows, {size:.1f} MB → {OUTPUT_FILE}")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2000)
    parser.add_argument("--end", type=int, default=2024)
    args = parser.parse_args()
    build_weather_cache(args.start, args.end)
