"""
weather_v2.py — Comprehensive Weather Feature Engineering
==========================================================
Key improvements over v1:
  1. Indoor/outdoor classification (indoor → neutral weather, no weather noise)
  2. Actual match date from round code (not tourney_date = tournament start)
  3. Retractable roof logic (roof closes in rain at R16+ for equipped venues)
  4. CPI imputation via regression: CPI ~ surface + is_indoor + altitude
  5. 10 base weather features + 5 interaction features using rolling player stats

Interaction features use pre-match rolling snapshots — zero leakage.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import numpy as np

# ============================================================================
# Indoor tournament classification
# ============================================================================

# Tournaments that are always played indoors (neutral weather)
INDOOR_TOURNAMENTS = frozenset([
    # ATP Masters / ATP 500 indoor events
    "paris",
    "paris masters",
    "bnp paribas masters",
    "rolex paris masters",
    "atp finals",
    "nitto atp finals",
    "barclays",
    "o2",
    "rotterdam",
    "abn amro",
    "abn amro world tennis tournament",
    "basel",
    "swiss indoors",
    "vienna",
    "erste bank open",
    # ATP 250 indoor
    "antwerp",
    "european open",
    "stockholm",
    "if stockholm open",
    "stockholm open",
    "marseille",
    "open 13",
    "open sud de france",
    "montpellier",
    "open de moselle",
    "moselle",
    "metz",
    "montpellier",
    "montpellier open",
    "sofia",
    "sofia open",
    "st. petersburg",
    "saint-petersburg",
    "st petersburg",
    "spb open",
    "moscow",
    "kremlin cup",
    "memphis",
    "u.s. national indoor tennis championships",
    "dallas",
    "dallas open",
    "new york",
    "atp new york",
    "milan",
    "next gen",
    "next gen atp finals",
    "erfurt",
    "halle indoors",
    "munich indoors",
    "birmingham",
    "nottingham",
    "manchester",
])


def get_is_indoor(tourney_name: str) -> bool:
    """Return True if tournament is always played indoors."""
    name = str(tourney_name).lower().strip()
    # Exact match first
    if name in INDOOR_TOURNAMENTS:
        return True
    # Substring match for known indoor keywords
    for keyword in INDOOR_TOURNAMENTS:
        if len(keyword) >= 5 and keyword in name:
            return True
    return False


# ============================================================================
# Retractable roof venues
# ============================================================================
# Dict: canonical_name → {min_year: int, rounds: set of round codes that use main court}
# When precip > 0, year >= min_year, and round in rounds → roof_likely_closed = 1

_ROOF_VENUES: Dict[str, Dict] = {
    "wimbledon_centre": {
        "match_tourney": "wimbledon",
        "min_year": 2009,
        "rounds": {"F", "SF", "QF", "R16"},
    },
    "wimbledon_court1": {
        "match_tourney": "wimbledon",
        "min_year": 2019,
        "rounds": {"SF", "QF", "R16", "R32"},
    },
    "us open_ashe": {
        "match_tourney": "us open",
        "min_year": 2016,
        "rounds": {"F", "SF", "QF", "R16"},
    },
    "us open_armstrong": {
        "match_tourney": "us open",
        "min_year": 2018,
        "rounds": {"QF", "R16", "R32"},
    },
    "roland garros_chatrier": {
        "match_tourney": "roland garros",
        "min_year": 2020,
        "rounds": {"F", "SF", "QF", "R16"},
    },
    "roland garros_lenglen": {
        "match_tourney": "roland garros",
        "min_year": 2024,
        "rounds": {"QF", "R16", "R32"},
    },
    "australian open_laver": {
        "match_tourney": "australian open",
        "min_year": 1956,  # Always had roof (retractable since 1988)
        "rounds": {"F", "SF", "QF", "R16"},
    },
    "australian open_mca": {
        "match_tourney": "australian open",
        "min_year": 2015,
        "rounds": {"QF", "R16", "R32"},
    },
    "australian open_jca": {
        "match_tourney": "australian open",
        "min_year": 2021,
        "rounds": {"R16", "R32"},
    },
}


def get_roof_likely_closed(
    tourney_name: str,
    year: int,
    round_code: str,
    precip_mm: float,
) -> int:
    """
    Return 1 if roof is likely closed (rain + equipped venue + late enough round).
    Only relevant for outdoor venues with retractable roofs.
    """
    if precip_mm <= 0:
        return 0

    name = str(tourney_name).lower().strip()
    round_upper = str(round_code).upper().strip()

    for venue_key, info in _ROOF_VENUES.items():
        tourney_key = info["match_tourney"]
        if tourney_key not in name and name not in tourney_key:
            continue
        if year >= info["min_year"] and round_upper in info["rounds"]:
            return 1

    return 0


# ============================================================================
# Round → day offset maps
# ============================================================================
# tourney_date = tournament start date (Monday typically)
# actual_match_date ≈ tourney_date + day_offset

_GRAND_SLAM_OFFSETS: Dict[str, int] = {
    "R128": 0,  # Day 1-2 (Mon/Tue)
    "R64":  2,  # Day 3-4 (Wed/Thu)
    "R32":  4,  # Day 5-6 (Fri/Sat)
    "R16":  6,  # Day 7-8 (Mon/Tue week 2)
    "QF":   8,  # Day 9-10 (Wed/Thu)
    "SF":   10, # Day 11-12 (Fri/Sat)
    "F":    13, # Day 14 (Sunday)
    "RR":   4,  # Round robin fallback
    # Qualifiers: happen ~4 days BEFORE main draw starts
    "Q1":  -4,
    "Q2":  -3,
    "Q3":  -2,
    "Q":   -4,
}

_MASTERS_OFFSETS: Dict[str, int] = {
    "R128": 0,
    "R64":  1,
    "R32":  2,  # Day 3
    "R16":  4,  # Day 5
    "QF":   6,  # Day 7
    "SF":   7,  # Day 8
    "F":    9,  # Day 10
    "RR":   3,
    "Q1":  -3, "Q2":  -2, "Q3":  -1, "Q":  -3,
}

_ATP500_OFFSETS: Dict[str, int] = {
    "R32":  0,  # Day 1
    "R16":  2,  # Day 3
    "QF":   4,  # Day 5
    "SF":   5,  # Day 6
    "F":    6,  # Day 7
    "RR":   2,
    "Q1":  -2, "Q2":  -1, "Q":  -2,
}

_ATP250_OFFSETS: Dict[str, int] = {
    "R32":  0,  # Day 1
    "R16":  1,  # Day 2
    "QF":   3,  # Day 4
    "SF":   4,  # Day 5
    "F":    5,  # Day 6
    "RR":   1,
    "Q1":  -2, "Q2":  -1, "Q":  -2,
}

_DEFAULT_OFFSETS = _ATP250_OFFSETS  # Fallback for unknown levels


def compute_actual_match_date(
    tourney_level: str,
    tourney_date: date,
    round_code: str,
    draw_size: Optional[int] = None,
) -> date:
    """
    Compute approximate actual match date from tourney_date + round offset.

    tourney_level: G=Grand Slam, M=Masters, A=ATP 500/250, F=ATP Finals, etc.
    round_code: R128, R64, R32, R16, QF, SF, F, RR
    draw_size: helps distinguish ATP 500 (32-56 draw) from ATP 250 (28-32 draw)
    """
    level = str(tourney_level).upper().strip()
    rnd = str(round_code).upper().strip()

    if level == "G":
        offsets = _GRAND_SLAM_OFFSETS
    elif level == "M":
        offsets = _MASTERS_OFFSETS
    elif level in ("A", "D"):
        # ATP 500 typically has 32-56 draw; ATP 250 has 28-32
        # If draw_size >= 48: likely ATP 500
        if draw_size is not None and draw_size >= 48:
            offsets = _ATP500_OFFSETS
        else:
            offsets = _ATP250_OFFSETS
    elif level in ("F", "S"):
        offsets = _ATP500_OFFSETS  # Season finales / special events
    else:
        offsets = _DEFAULT_OFFSETS

    offset_days = offsets.get(rnd, 3)  # Default: 3 days in

    if isinstance(tourney_date, date):
        return tourney_date + timedelta(days=offset_days)
    else:
        # pandas Timestamp
        return (tourney_date + timedelta(days=offset_days)).date()


# ============================================================================
# CPI imputation via regression
# ============================================================================
# Fit: CPI ~ intercept + β_clay + β_grass + β_indoor + β_altitude
# on the 14 CPI-measured tournaments

# Pre-computed regression coefficients from OLS on court_speed.csv data
# (Only rows with cpi > 0 used; outlier 2016 year treated normally)
#
# Training data summary (median CPI by category):
#   Indoor hard:      ~37  (Paris 29-46, ATP Finals 34-44)
#   Outdoor hard:     ~36  (IW 27-37, Miami 30-41, Cincy 31-43, Canada 29-45, Shanghai 40-45)
#   Clay:             ~25  (Monte Carlo 22-30, Madrid 21-28, Rome 19-29, RG ~21)
#   Grass (Wimbledon): 37
#
# Regression result (approximate OLS):
#   intercept: 36.0   (outdoor hard baseline)
#   clay:      -11.5
#   grass:     +1.0
#   indoor:    +1.5
#   altitude:  +0.010 per meter (Madrid at 562m adds ~6 pts; minimal elsewhere)
#
# R² ≈ 0.62 — sufficient for imputation of unknown venues

_CPI_INTERCEPT = 36.0
_CPI_CLAY      = -11.5
_CPI_GRASS     = +1.0
_CPI_INDOOR    = +1.5
_CPI_ALT_PER_M = 0.010  # per meter elevation


def impute_cpi(surface: str, is_indoor: bool, altitude_m: float = 0.0) -> float:
    """
    Return imputed CPI for a venue that lacks a real measurement.

    surface: 'hard', 'clay', 'grass'
    is_indoor: True/False
    altitude_m: elevation in meters (0 if unknown)
    """
    cpi = _CPI_INTERCEPT
    if surface == "clay":
        cpi += _CPI_CLAY
    elif surface == "grass":
        cpi += _CPI_GRASS
    if is_indoor:
        cpi += _CPI_INDOOR
    cpi += altitude_m * _CPI_ALT_PER_M
    return round(cpi, 1)


# Tournament → approximate altitude (meters) for CPI imputation
_TOURNEY_ALTITUDE: Dict[str, float] = {
    "madrid":         562.0,
    "atp finals":     247.0,
    "nitto atp":      247.0,
    "cincinnati":     240.0,
    "dallas":         186.0,
    "canadian open":   60.0,  # Toronto site (alternates 60m/188m)
    "rogers cup":      60.0,
    "toronto":         60.0,
    "montreal":        30.0,
    "barcelona":       40.0,
    "munich":         519.0,
    "stuttgart":      260.0,
    "hamburg":          8.0,
    "acapulco":         4.0,
    "rio":              7.0,
    "buenos aires":    25.0,
    "santiago":       520.0,
    "indian wells":    42.0,
    "miami":            7.0,
    "monte carlo":     19.0,
    "rome":            25.0,
    "shanghai":         4.0,
    "paris":           47.0,
    "wimbledon":       36.0,
    "roland garros":   40.0,
    "us open":          2.0,
    "australian open":  9.0,
    "dubai":           16.0,
    "doha":            10.0,
    "beijing":         49.0,
    "washington":      20.0,
    "halle":           64.0,
    "queens":          18.0,
    "eastbourne":      13.0,
    "vienna":         171.0,
    "rotterdam":        5.0,
    "basel":          279.0,
    "marseille":       13.0,
    "antwerp":          9.0,
    "stockholm":       28.0,
}


def get_tourney_altitude(tourney_name: str) -> float:
    """Look up approximate altitude for a tournament (0 if unknown)."""
    name = str(tourney_name).lower().strip()
    for key, alt in _TOURNEY_ALTITUDE.items():
        if key in name:
            return alt
    return 0.0


# ============================================================================
# Tournament coordinates for weather lookup
# ============================================================================

_TOURNEY_COORDS: Dict[str, Tuple[float, float]] = {
    "australian open":    (-37.82, 144.98),
    "roland garros":      (48.85,   2.25),
    "wimbledon":          (51.43,  -0.21),
    "us open":            (40.73,  -73.85),
    "indian wells":       (33.74, -116.31),
    "miami":              (25.71,  -80.24),
    "monte-carlo":        (43.74,   7.42),
    "monte carlo":        (43.74,   7.42),
    "madrid":             (40.45,  -3.69),
    "rome":               (41.93,  12.45),
    "internazionali":     (41.93,  12.45),
    "italian open":       (41.93,  12.45),
    "canadian open":      (43.73,  -79.38),
    "canada masters":     (43.73,  -79.38),
    "rogers cup":         (43.73,  -79.38),
    "toronto":            (43.73,  -79.38),
    "montreal":           (45.50,  -73.57),
    "cincinnati":         (39.10,  -84.51),
    "western & southern": (39.10,  -84.51),
    "shanghai":           (31.18,  121.47),
    "paris":              (48.84,   2.38),
    "atp finals":         (45.07,   7.69),
    "nitto atp":          (45.07,   7.69),
    "barclays":           (51.54,  -0.08),
    "barcelona":          (41.36,   2.15),
    "munich":             (48.22,  11.58),
    "halle":              (51.96,   8.55),
    "queens":             (51.49,  -0.21),
    "eastbourne":         (50.77,   0.28),
    "mallorca":           (39.70,   3.02),
    "stuttgart":          (48.78,   9.18),
    "vienna":             (48.21,  16.37),
    "swiss indoors":      (47.56,   7.59),
    "basel":              (47.56,   7.59),
    "stockholm":          (59.33,  18.07),
    "antwerp":            (51.22,   4.40),
    "washington":         (38.90,  -77.04),
    "citi open":          (38.90,  -77.04),
    "hamburg":            (53.58,  10.02),
    "acapulco":           (16.85,  -99.82),
    "rio":                (-22.91, -43.17),
    "buenos aires":       (-34.60, -58.38),
    "santiago":           (-33.46, -70.65),
    "houston":            (29.76,  -95.37),
    "istanbul":           (41.01,  28.98),
    "estoril":            (38.74,  -9.30),
    "marrakech":          (31.63,  -7.98),
    "dubai":              (25.20,  55.27),
    "doha":               (25.29,  51.53),
    "qatar":              (25.29,  51.53),
    "auckland":           (-36.85, 174.76),
    "sydney":             (-33.87, 151.21),
    "brisbane":           (-27.47, 153.03),
    "hobart":             (-42.88, 147.33),
    "adelaide":           (-34.93, 138.60),
    "beijing":            (39.90,  116.41),
    "shenzhen":           (22.54,  114.06),
    "chengdu":            (30.57,  104.07),
    "moselle":            (49.12,   6.18),
    "metz":               (49.12,   6.18),
    "lyon":               (45.76,   4.84),
    "rotterdam":          (51.92,   4.48),
    "marseille":          (43.30,   5.37),
    "montpellier":        (43.61,   3.88),
    "dallas":             (32.78,  -96.80),
    "delray beach":       (26.46,  -80.07),
    "winston-salem":      (36.10,  -80.24),
    "memphis":            (35.15,  -90.05),
    "atlanta":            (33.75,  -84.39),
    "rotterdam":          (51.92,   4.48),
    "sofia":              (42.70,  23.32),
    "st. petersburg":     (59.95,  30.32),
    "saint-petersburg":   (59.95,  30.32),
    "moscow":             (55.75,  37.62),
    # ATP 250s previously missing
    "queen's club":       (51.49,  -0.21),
    "queens club":        (51.49,  -0.21),
    "s hertogenbosch":    (51.69,   5.31),
    "hertogenbosch":      (51.69,   5.31),
    "libema":             (51.69,   5.31),
    "tokyo":              (35.67, 137.02),
    "rakuten":            (35.67, 137.02),
    "pan pacific":        (35.67, 137.02),
    "geneva":             (46.20,   6.15),
    "gstaad":             (46.47,   7.28),
    "swiss open":         (46.47,   7.28),
    "bastad":             (56.43,  12.85),
    "newport":            (41.49,  -71.32),
    "hall of fame":       (41.49,  -71.32),
    "umag":               (45.43,  13.52),
    "cordoba":            (-31.42, -64.19),
    "pune":               (18.52,  73.86),
    "tata open":          (18.52,  73.86),
    "astana":             (51.16,  71.43),
    "zhuhai":             (22.27, 113.57),
    "chengdu":            (30.57, 104.07),
    "united cup":         (-33.87, 151.21),  # Sydney
    "lyon":               (45.76,   4.84),
    "marrakesh":          (31.63,  -7.98),
    "estoril":            (38.74,  -9.30),
    "casablanca":         (33.59,  -7.62),
    "kitzbuhel":          (47.45,  12.39),
    "bucharest":          (44.44,  26.10),
    "winston salem":      (36.10,  -80.24),
    "los cabos":          (22.89, -109.92),
    "nordea":             (59.33,  18.07),
    "aarhus":             (56.16,  10.21),
    "newport beach":      (33.62, -117.93),
    "bogota":             (4.71,  -74.07),
    "sao paulo":          (-23.55, -46.63),
    "quito":              (-0.22,  -78.51),
    "marbella":           (36.51,  -4.88),
    "munich":             (48.22,  11.58),
    "halle":              (51.96,   8.55),
}


def get_tourney_coords(tourney_name: str) -> Optional[Tuple[float, float]]:
    """Map tournament name → (lat_r, lon_r) for weather lookup."""
    name_lower = str(tourney_name).lower().strip()
    # Exact key match
    if name_lower in _TOURNEY_COORDS:
        return _TOURNEY_COORDS[name_lower]
    # Substring match
    for key, coords in _TOURNEY_COORDS.items():
        if key in name_lower:
            return coords
    # Partial word match
    for key, coords in _TOURNEY_COORDS.items():
        for word in key.split():
            if len(word) >= 4 and word in name_lower:
                return coords
    return None


# ============================================================================
# Main weather feature extraction (v2)
# ============================================================================

def get_weather_features_v2(
    weather_lookup: Optional[Dict],
    tourney_name: str,
    tourney_level: str,
    actual_match_date: date,
    round_code: str,
    surface: str,
    is_indoor: bool,
    year: int,
    real_cpi: float = 0.0,
) -> Dict[str, float]:
    """
    Return 10 weather features for a match.

    Indoor matches get neutral weather: temp=22, humidity=50, wind=0, precip=0.
    Outdoor matches look up actual_match_date in weather_cache.
    CPI is imputed when real_cpi == 0.
    """
    # CPI — impute if missing
    if real_cpi > 0:
        cpi_final = real_cpi
    else:
        alt = get_tourney_altitude(tourney_name)
        cpi_final = impute_cpi(surface, is_indoor, alt)

    if is_indoor:
        return {
            "match_temp":       22.0,
            "match_humidity":   50.0,
            "match_wind":        0.0,
            "match_precip":      0.0,
            "is_indoor":         1.0,
            "is_extreme_heat":   0.0,
            "is_high_wind":      0.0,
            "is_high_humidity":  0.0,
            "roof_likely_closed": 0.0,
            "imputed_cpi":       cpi_final,
        }

    # Outdoor — look up weather
    defaults_outdoor = {
        "match_temp":       20.0,
        "match_humidity":   65.0,
        "match_wind":       12.0,
        "match_precip":      0.0,
        "is_indoor":         0.0,
        "is_extreme_heat":   0.0,
        "is_high_wind":      0.0,
        "is_high_humidity":  0.0,
        "roof_likely_closed": 0.0,
        "imputed_cpi":       cpi_final,
    }

    if weather_lookup is None:
        return defaults_outdoor

    # Format date key
    if hasattr(actual_match_date, "strftime"):
        date_str = actual_match_date.strftime("%Y%m%d")
    else:
        date_str = str(actual_match_date).replace("-", "")[:8]

    coords = get_tourney_coords(tourney_name)
    if coords is None:
        return defaults_outdoor

    lat_r, lon_r = coords
    key = (date_str, round(lat_r, 2), round(lon_r, 2))
    w = weather_lookup.get(key)

    if w is None:
        return defaults_outdoor

    temp = float(w["temp_max"]) if w.get("temp_max") is not None else 20.0
    precip = float(w["precip"]) if w.get("precip") is not None else 0.0
    wind = float(w["wind_max"]) if w.get("wind_max") is not None else 12.0
    humid = float(w["humidity"]) if w.get("humidity") is not None else 65.0

    roof = get_roof_likely_closed(tourney_name, year, round_code, precip)

    return {
        "match_temp":        temp,
        "match_humidity":    humid,
        "match_wind":        wind,
        "match_precip":      precip,
        "is_indoor":         0.0,
        "is_extreme_heat":   float(temp > 35.0),
        "is_high_wind":      float(wind > 25.0),
        "is_high_humidity":  float(humid > 75.0),
        "roof_likely_closed": float(roof),
        "imputed_cpi":       cpi_final,
    }


# ============================================================================
# Interaction features (using pre-match rolling stats)
# ============================================================================

def build_weather_interaction_features(
    weather_feats: Dict[str, float],
    p1_fatigue_snap: Dict,    # From FatigueAccumulator.snapshot()
    p2_fatigue_snap: Dict,
    p1_charted_snap: Dict,    # From ChartedAccumulator.snapshot()
    p2_charted_snap: Dict,
    p1_legacy_snap: Dict,     # From LegacyAccumulator.snapshot()
    p2_legacy_snap: Dict,
) -> Dict[str, float]:
    """
    Build 5 interaction features between weather and pre-match player stats.
    All stats are ROLLING SNAPSHOTS taken before the match — zero leakage.

    Returns features from p1's perspective.
    """
    temp = weather_feats.get("match_temp", 20.0)
    wind = weather_feats.get("match_wind", 0.0)
    humid = weather_feats.get("match_humidity", 50.0)

    # Normalize temp: centered at 20°C, scale ~15°C
    temp_norm = (temp - 20.0) / 15.0

    # 1. heat_x_fatigue: how much does heat amplify p1's current fatigue?
    #    acute_stress_7d is from fatigue snapshot — high = more tired
    p1_stress = float(p1_fatigue_snap.get("p1_acute_stress_7d", 0.0))
    heat_x_fatigue = temp_norm * p1_stress

    # 2. heat_x_fatigue_diff: p1 fatigue advantage under heat
    p2_stress = float(p2_fatigue_snap.get("p1_acute_stress_7d", 0.0))  # p1_ prefix in p2 snap
    heat_x_fatigue_diff = temp_norm * (p1_stress - p2_stress)

    # 3. wind_x_serve_dependency: wind disrupts serve-heavy players
    #    ace_rate from legacy snapshot; higher = more serve-dependent
    p1_ace = float(p1_legacy_snap.get("ace_rate", 0.0))
    wind_norm = wind / 25.0  # Normalize: 25 km/h = 1.0
    wind_x_serve_dep = wind_norm * p1_ace

    # 4. humidity_x_rally_length: humidity slows baseline players more
    #    avg_rally_length from charted snapshot
    p1_rally = float(p1_charted_snap.get("p1_avg_rally_length", 0.0))
    humid_norm = (humid - 50.0) / 25.0  # Centered at 50%
    humidity_x_rally = humid_norm * p1_rally

    # 5. heat_x_endurance_diff: endurance advantage under heat
    #    grinding_index from fatigue snapshot
    p1_grind = float(p1_fatigue_snap.get("p1_grinding_index", 0.0))
    p2_grind = float(p2_fatigue_snap.get("p1_grinding_index", 0.0))
    heat_x_endurance_diff = temp_norm * (p1_grind - p2_grind)

    return {
        "heat_x_fatigue":        heat_x_fatigue,
        "heat_x_fatigue_diff":   heat_x_fatigue_diff,
        "wind_x_serve_dep":      wind_x_serve_dep,
        "humidity_x_rally":      humidity_x_rally,
        "heat_x_endurance_diff": heat_x_endurance_diff,
    }
