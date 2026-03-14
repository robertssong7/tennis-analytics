"""
Fetch historical weather data for tennis tournaments.
Uses Open-Meteo free API (no key needed).
Saves to data/tournament_weather.csv
"""

import pandas as pd
import numpy as np
import json
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent
UNI_PATH = REPO_ROOT / "data" / "processed" / "universal_features.parquet"
OUTPUT_PATH = REPO_ROOT / "data" / "tournament_weather.csv"

# Major tournament locations (lat, lon)
TOURNAMENT_COORDS = {
    "Australian Open": (-37.8218, 144.9785),
    "Roland Garros": (48.8469, 2.2481),
    "Wimbledon": (51.4340, -0.2145),
    "US Open": (40.7499, -73.8459),
    "Indian Wells": (33.7238, -116.3052),
    "Miami": (25.7089, -80.1536),
    "Monte Carlo": (43.7523, 7.4405),
    "Madrid": (40.3722, -3.6884),
    "Rome": (41.9282, 12.4584),
    "Canadian Open": (45.5017, -73.5673),  # Montreal
    "Montreal": (45.5017, -73.5673),
    "Toronto": (43.6532, -79.3832),
    "Cincinnati": (39.2550, -84.2717),
    "Shanghai": (31.0421, 121.3544),
    "Paris": (48.8323, 2.3551),  # Bercy
    "ATP Finals": (45.4654, 9.1859),  # Turin
    "Brisbane": (-27.4698, 153.0251),
    "Doha": (25.2854, 51.5310),
    "Dubai": (25.2048, 55.2708),
    "Acapulco": (16.8531, -99.8237),
    "Barcelona": (41.3874, 2.1686),
    "Halle": (52.0597, 8.3608),
    "Queens": (51.4893, -0.2106),  # London
    "Hamburg": (53.5511, 9.9937),
    "Washington": (38.8951, -77.0364),
    "Winston-Salem": (36.0999, -80.2442),
    "Beijing": (39.9042, 116.4074),
    "Tokyo": (35.6762, 139.6503),
    "Vienna": (48.2082, 16.3738),
    "Basel": (47.5596, 7.5886),
    "Stockholm": (59.3293, 18.0686),
    "St. Petersburg": (59.9343, 30.3351),
    "Marseille": (43.2965, 5.3698),
    "Rotterdam": (51.9225, 4.4792),
    "s-Hertogenbosch": (51.6978, 5.3037),
    "Eastbourne": (50.7684, 0.2906),
    "Stuttgart": (48.7758, 9.1829),
    "Lyon": (45.7640, 4.8357),
    "Buenos Aires": (-34.6037, -58.3816),
    "Rio de Janeiro": (-22.9068, -43.1729),
    "Santiago": (-33.4489, -70.6693),
    "Estoril": (38.7071, -9.3977),
    "Kitzbuhel": (47.4474, 12.3914),
    "Umag": (45.4362, 13.5192),
    "Gstaad": (46.4749, 7.2882),
    "Atlanta": (33.7490, -84.3880),
    "Los Cabos": (22.8905, -109.9167),
    "Metz": (49.1193, 6.1757),
    "Sofia": (42.6977, 23.3219),
    "Antwerp": (51.2194, 4.4025),
    "Zhuhai": (22.2710, 113.5767),
    "Chengdu": (30.5728, 104.0668),
    "Florence": (43.7696, 11.2558),
    "Adelaide": (-34.9285, 138.6007),
    "Auckland": (-36.8509, 174.7645),
    "Pune": (18.5204, 73.8567),
    "Montpellier": (43.6108, 3.8767),
    "Dallas": (32.7767, -96.7970),
    "Delray Beach": (26.4615, -80.0728),
}

print("Loading tournament data...")
uni = pd.read_parquet(UNI_PATH)
uni["match_date"] = pd.to_datetime(uni["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")

# Get unique tournament + date combos
tourney_dates = uni.groupby("tourney_name").agg(
    start_date=("match_date", "min"),
    end_date=("match_date", "max"),
    n_matches=("match_date", "count")
).reset_index()

print(f"Found {len(tourney_dates)} unique tournaments")

# Match tournament names to coordinates
def find_coords(name):
    name_lower = name.lower()
    for key, coords in TOURNAMENT_COORDS.items():
        if key.lower() in name_lower or name_lower in key.lower():
            return coords
    # Try partial match
    for key, coords in TOURNAMENT_COORDS.items():
        for word in key.lower().split():
            if len(word) > 3 and word in name_lower:
                return coords
    return None

matched = 0
results = []

for _, row in tourney_dates.iterrows():
    name = row["tourney_name"]
    coords = find_coords(name)
    if coords:
        matched += 1

print(f"Matched {matched}/{len(tourney_dates)} tournaments to coordinates")

# Fetch weather for matched tournaments
print("\nFetching weather data from Open-Meteo (free API)...")
weather_cache = {}
errors = 0

unique_fetches = set()
for _, row in uni.iterrows():
    name = row.get("tourney_name", "")
    d = row["match_date"]
    if pd.isna(d):
        continue
    coords = find_coords(name)
    if coords:
        date_str = d.strftime("%Y-%m-%d")
        key = (name, date_str, coords[0], coords[1])
        unique_fetches.add(key)

print(f"Need to fetch weather for {len(unique_fetches)} unique tournament-dates")

# Group by location+month to reduce API calls
# Open-Meteo allows date ranges, so we batch by tournament
tourney_weather = {}
fetched = 0
api_calls = 0

# Group unique fetches by tournament name
from collections import defaultdict
by_tourney = defaultdict(list)
for name, date_str, lat, lon in unique_fetches:
    by_tourney[(name, lat, lon)].append(date_str)

print(f"Grouped into {len(by_tourney)} tournament-location batches")

for (name, lat, lon), date_list in by_tourney.items():
    dates_sorted = sorted(date_list)
    start = dates_sorted[0]
    end = dates_sorted[-1]

    url = (f"https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={lat}&longitude={lon}"
           f"&start_date={start}&end_date={end}"
           f"&daily=temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,"
           f"windspeed_10m_max,precipitation_sum"
           f"&timezone=auto")

    try:
        req = Request(url, headers={"User-Agent": "TennisIQ/1.0"})
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())

        if "daily" in data:
            daily = data["daily"]
            for i, d in enumerate(daily.get("time", [])):
                tourney_weather[(name, d)] = {
                    "temp_max": daily["temperature_2m_max"][i],
                    "temp_min": daily["temperature_2m_min"][i],
                    "humidity": daily["relative_humidity_2m_mean"][i] if daily["relative_humidity_2m_mean"][i] else 50,
                    "wind_max": daily["windspeed_10m_max"][i] if daily["windspeed_10m_max"][i] else 10,
                    "precip": daily["precipitation_sum"][i] if daily["precipitation_sum"][i] else 0,
                }
            fetched += len(daily.get("time", []))
        api_calls += 1
        if api_calls % 20 == 0:
            print(f"  {api_calls} API calls, {fetched} day-records fetched...")
        time.sleep(3)  # Be polite
    except Exception as e:
        errors += 1
        if errors <= 5:
            print(f"  Error fetching {name}: {e}")

print(f"\nFetched {fetched} weather records ({api_calls} API calls, {errors} errors)")

# Build output CSV: one row per match in universal features
print("\nMapping weather to matches...")
weather_rows = []
matched_weather = 0

for _, row in uni.iterrows():
    name = row.get("tourney_name", "")
    d = row["match_date"]
    if pd.isna(d):
        weather_rows.append({})
        continue

    date_str = d.strftime("%Y-%m-%d")
    w = tourney_weather.get((name, date_str), None)

    if w:
        matched_weather += 1
        weather_rows.append({
            "tourney_name": name,
            "match_date": date_str,
            "temp_max": w["temp_max"],
            "temp_min": w["temp_min"],
            "temp_avg": (w["temp_max"] + w["temp_min"]) / 2 if w["temp_max"] and w["temp_min"] else None,
            "humidity": w["humidity"],
            "wind_max": w["wind_max"],
            "precip": w["precip"],
        })
    else:
        weather_rows.append({
            "tourney_name": name,
            "match_date": date_str,
        })

weather_df = pd.DataFrame(weather_rows)
weather_df.to_csv(OUTPUT_PATH, index=False)
print(f"Saved weather for {matched_weather}/{len(uni)} matches to {OUTPUT_PATH}")
print(f"Coverage: {matched_weather/len(uni)*100:.1f}%")
print("Done.")
