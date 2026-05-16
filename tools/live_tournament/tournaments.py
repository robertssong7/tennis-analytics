"""Active-tournament resolver + timezone table.

Maps the project's active-tournament name (from active_players.json or the
ATP calendar) to a Tennis Abstract /current/ page slug and an IANA timezone.
"""
from __future__ import annotations

from datetime import date


# Tennis Abstract URL slugs for /current/<slug>.html pages.
# Keys are the canonical tournament name used by the project's ATP calendar.
TA_SLUG = {
    "Italian Open":                "2026ATPRome",
    "Internazionali BNL d'Italia": "2026ATPRome",
    "Rome":                        "2026ATPRome",
    "Mutua Madrid Open":           "2026ATPMadrid",
    "Madrid":                      "2026ATPMadrid",
    "Monte Carlo Masters":         "2026ATPMonteCarlo",
    "Barcelona Open":              "2026ATPBarcelona",
    "Roland Garros":               "2026FrenchOpenMen",
    "French Open":                 "2026FrenchOpenMen",
    "Wimbledon":                   "2026WimbledonMen",
    "US Open":                     "2026USOpenMen",
    "Australian Open":             "2026AustralianOpenMen",
    "Cincinnati Masters":          "2026ATPCincinnati",
    "Canadian Open":               "2026ATPCanada",
    "Shanghai Masters":            "2026ATPShanghai",
    "Paris Masters":               "2026ATPParis",
    "Indian Wells Masters":        "2026ATPIndianWells",
    "Miami Masters":               "2026ATPMiami",
}


# IANA timezone per tournament. Default UTC for anything not listed;
# the scraper logs a warning so the table can be extended.
TOURNAMENT_TZ = {
    "Italian Open":                "Europe/Rome",
    "Internazionali BNL d'Italia": "Europe/Rome",
    "Mutua Madrid Open":           "Europe/Madrid",
    "Monte Carlo Masters":         "Europe/Monaco",
    "Barcelona Open":              "Europe/Madrid",
    "Roland Garros":               "Europe/Paris",
    "French Open":                 "Europe/Paris",
    "Wimbledon":                   "Europe/London",
    "US Open":                     "America/New_York",
    "Australian Open":             "Australia/Melbourne",
    "Cincinnati Masters":          "America/New_York",
    "Canadian Open":               "America/Toronto",
    "Shanghai Masters":            "Asia/Shanghai",
    "Paris Masters":               "Europe/Paris",
    "Indian Wells Masters":        "America/Los_Angeles",
    "Miami Masters":               "America/New_York",
}


# Surface + level by tournament name. Used to backfill the canonical state
# when Tennis Abstract's page does not expose these explicitly.
TOURNAMENT_META = {
    "Italian Open":                {"surface": "clay",  "category": "ATP Masters 1000", "location": "Rome, Italy",          "canonical_name": "Internazionali BNL d'Italia"},
    "Internazionali BNL d'Italia": {"surface": "clay",  "category": "ATP Masters 1000", "location": "Rome, Italy",          "canonical_name": "Internazionali BNL d'Italia"},
    "Mutua Madrid Open":           {"surface": "clay",  "category": "ATP Masters 1000", "location": "Madrid, Spain",        "canonical_name": "Mutua Madrid Open"},
    "Monte Carlo Masters":         {"surface": "clay",  "category": "ATP Masters 1000", "location": "Roquebrune-Cap-Martin", "canonical_name": "Monte Carlo Masters"},
    "Barcelona Open":              {"surface": "clay",  "category": "ATP 500",          "location": "Barcelona, Spain",     "canonical_name": "Barcelona Open"},
    "Roland Garros":               {"surface": "clay",  "category": "Grand Slam",       "location": "Paris, France",        "canonical_name": "Roland Garros"},
    "Wimbledon":                   {"surface": "grass", "category": "Grand Slam",       "location": "London, UK",           "canonical_name": "Wimbledon"},
    "US Open":                     {"surface": "hard",  "category": "Grand Slam",       "location": "New York, USA",        "canonical_name": "US Open"},
    "Australian Open":             {"surface": "hard",  "category": "Grand Slam",       "location": "Melbourne, Australia", "canonical_name": "Australian Open"},
}


def resolve_active(now: date, active_tournaments: list[dict]) -> dict | None:
    """Pick the active tournament from active_players.json's `tournaments` list.

    Returns the first tournament whose [start, end] window contains `now`.
    None if no tournament is currently active.
    """
    for t in active_tournaments:
        try:
            start = date.fromisoformat(t["start"])
            end = date.fromisoformat(t["end"])
        except (KeyError, ValueError):
            continue
        if start <= now <= end:
            return t
    return None


def ta_slug_for(name: str) -> str | None:
    return TA_SLUG.get(name)


def tz_for(name: str) -> str:
    return TOURNAMENT_TZ.get(name, "UTC")


def meta_for(name: str) -> dict:
    return TOURNAMENT_META.get(name, {})
