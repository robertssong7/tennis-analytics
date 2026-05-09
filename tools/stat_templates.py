"""Category-specific copy templates for the Stat of the Day engine.

Voice: ESPN/538. Body is at most three sentences. No em dashes, no
emojis. Templates only reference numbers from the facts dict — they
never invent values.
"""
from __future__ import annotations


def active_streak(f: dict) -> tuple:
    headline = f"{f['player']} rolls into {f['today']} on a {f['n']}-match win streak"
    body = (
        f"{f['player']}'s active win streak now stands at {f['n']} matches, "
        f"the longest active run on tour. "
        f"The streak began at {f['start_tourney']} on {f['start_date']}."
    )
    return headline, body


def surface_specialist(f: dict) -> tuple:
    headline = f"{f['player']}'s {f['best_surface']} edge is the widest on tour"
    body = (
        f"{f['player']}'s rating on {f['best_surface']} sits {f['gap']} points above "
        f"their {f['worst_surface']} rating, the largest cross-surface gap among active "
        f"top-50 players. Best surface rating: {f['best_rating']}. "
        f"Worst: {f['worst_rating']}."
    )
    return headline, body


def age_anomaly(f: dict) -> tuple:
    headline = f"{f['player']} wins at age {f['age']} at {f['tourney']}"
    body = (
        f"{f['player']} took down {f['opponent']} at age {f['age']} on {f['date']}, "
        f"a result that cuts against typical ATP age curves where match-win rates drop "
        f"sharply after 33."
    )
    return headline, body


def rating_jump(f: dict) -> tuple:
    direction_word = "above" if f["direction"] == "above" else "below"
    headline = (
        f"{f['player']}'s rating sits {abs(f['delta'])} points "
        f"{direction_word} their career peak"
    )
    body = (
        f"{f['player']}'s current Glicko rating is {f['current_rating']}, "
        f"versus a career peak of {f['peak_rating']} set in {f['peak_year']}. "
        f"That is a {abs(f['delta'])}-point swing from prime form."
    )
    return headline, body


def h2h_breakthrough(f: dict) -> tuple:
    headline = (
        f"{f['winner']} finally beats {f['loser']} after {f['previous_meetings']} "
        f"prior losses"
    )
    body = (
        f"{f['winner']}'s win at {f['tourney']} on {f['date']} broke a "
        f"{f['previous_meetings']}-match losing streak against {f['loser']}. "
        f"It is the first career win in this head-to-head."
    )
    return headline, body


def tournament_pattern(f: dict) -> tuple:
    headline = f"{f['player']}'s {f['career_wins_here']}th career win at {f['tourney']}"
    body = (
        f"{f['player']} now has {f['career_wins_here']} career match wins at "
        f"{f['tourney']}, a single-event total reached by very few active players."
    )
    return headline, body


TEMPLATES = {
    "active_streak": active_streak,
    "surface_specialist": surface_specialist,
    "age_anomaly": age_anomaly,
    "rating_jump": rating_jump,
    "h2h_breakthrough": h2h_breakthrough,
    "tournament_pattern": tournament_pattern,
}


def render(category: str, facts: dict) -> dict:
    fn = TEMPLATES.get(category)
    if fn is None:
        return {
            "headline": "",
            "body": "",
            "category": category,
            "rendered": False,
        }
    headline, body = fn(facts)
    return {
        "headline": headline,
        "body": body,
        "category": category,
        "rendered": True,
    }
