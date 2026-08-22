"""
Tests for the optional Live Tennis API supplement source.

Run directly (no pytest dependency, matching this repo's tooling):

    python3 tools/test_livetennisapi_source.py

Every fixture here is hand-built to the published OpenAPI v1.1.0 schemas
(Match / Player / Score). No network call is made and no live response was
used to write these — see the PR description.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.livetennisapi_source import (  # noqa: E402
    SUPPLEMENT_COLUMNS,
    _score_string,
    fetch_supplement_rows,
    normalize_matches,
)

FAILURES: list[str] = []


def check(label: str, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {label}")


def match(**over):
    """A spec-shaped completed singles Match."""
    base = {
        "id": 1001,
        "tournament": "Cincinnati Masters",
        "surface": "hard",
        "indoor": False,
        "format": "BO3",
        "round": "QF",
        "status": "completed",
        "is_doubles": False,
        "scheduled_time": "2026-08-01T17:30:00Z",
        "players": {
            "p1": {"id": 1, "name": "Jannik Sinner", "ranking": 1},
            "p2": {"id": 2, "name": "Carlos Alcaraz", "ranking": 2},
        },
        "score": {"sets": [2, 0], "games": [[6, 7], [4, 5]], "points": [None, None]},
        "winner": 1,
    }
    base.update(over)
    return base


def test_happy_path():
    print("\n[happy path]")
    df = normalize_matches([match()])
    check("row count", len(df), 1)
    check("columns", list(df.columns), SUPPLEMENT_COLUMNS)
    r = df.iloc[0]
    check("tourney_name", r["tourney_name"], "Cincinnati Masters")
    check("tourney_date", r["tourney_date"], "20260801")
    check("surface", r["surface"], "hard")
    check("winner_name", r["winner_name"], "Jannik Sinner")
    check("loser_name", r["loser_name"], "Carlos Alcaraz")
    check("winner_rank", int(r["winner_rank"]), 1)
    check("loser_rank", int(r["loser_rank"]), 2)
    check("score (winner first)", r["score"], "6-4 7-5")
    check("best_of", int(r["best_of"]), 3)
    check("court", r["court"], "Outdoor")
    # Never fabricated:
    check("tourney_level blank", r["tourney_level"], "")
    check("location blank", r["location"], "")


def test_winner_is_p2_flips_score():
    print("\n[winner = p2]")
    df = normalize_matches([match(winner=2)])
    r = df.iloc[0]
    check("winner_name", r["winner_name"], "Carlos Alcaraz")
    check("loser_name", r["loser_name"], "Jannik Sinner")
    check("score flipped", r["score"], "4-6 5-7")


def test_empty_games_never_synthesised():
    print("\n[completed match with EMPTY games array]")
    df = normalize_matches([match(score={"sets": [2, 0], "games": [], "points": []})])
    check("row still emitted", len(df), 1)
    check("score is blank, not invented", df.iloc[0]["score"], "")

    df2 = normalize_matches([match(score=None)])
    check("null score object", df2.iloc[0]["score"], "")


def test_unsettled_and_unusable_rows_dropped():
    print("\n[rows that must be dropped]")
    check("winner null", len(normalize_matches([match(winner=None)])), 0)
    check("status live", len(normalize_matches([match(status="live")])), 0)
    check("status cancelled", len(normalize_matches([match(status="cancelled")])), 0)
    check("doubles", len(normalize_matches([match(is_doubles=True)])), 0)
    check("no scheduled_time", len(normalize_matches([match(scheduled_time=None)])), 0)
    check("blank player name",
          len(normalize_matches([match(players={"p1": {"name": ""},
                                                "p2": {"name": "X"}})])), 0)
    check("garbage item", len(normalize_matches([None, "nope", 7])), 0)


def test_nullable_fields_tolerated():
    print("\n[nullable fields per schema]")
    df = normalize_matches([match(
        surface=None, format=None, round=None, tournament=None, indoor=True,
        players={"p1": {"name": "A", "ranking": None},
                 "p2": {"name": "B", "ranking": None}},
    )])
    check("row emitted", len(df), 1)
    r = df.iloc[0]
    check("surface null → blank", r["surface"], "")
    check("format null → best_of 3", int(r["best_of"]), 3)
    check("round null → blank", r["round"], "")
    check("indoor true → Indoor", r["court"], "Indoor")
    check("null ranking is NaN not 0", bool(r["winner_rank"] != r["winner_rank"]), True)


def test_bo5():
    print("\n[BO5]")
    df = normalize_matches([match(format="BO5",
                                  score={"games": [[6, 6, 6], [3, 4, 2]]})])
    check("best_of", int(df.iloc[0]["best_of"]), 5)
    check("three sets", df.iloc[0]["score"], "6-3 6-4 6-2")


def test_score_string_edges():
    print("\n[_score_string edge cases]")
    check("None", _score_string(None, 1), "")
    check("empty list", _score_string([], 1), "")
    check("one side only", _score_string([[6]], 1), "")
    check("non-list members", _score_string(["a", "b"], 1), "")
    check("ragged truncates", _score_string([[6, 6], [3]], 1), "6-3")
    check("non-int skipped", _score_string([[6, None], [3, 4]], 1), "6-3")
    check("bools skipped", _score_string([[True], [False]], 1), "")


def test_disabled_without_key():
    print("\n[inert without LIVETENNISAPI_KEY]")
    saved = os.environ.pop("LIVETENNISAPI_KEY", None)
    try:
        # A session that would explode if touched proves no request is made.
        class Boom:
            def get(self, *a, **k):
                raise AssertionError("network touched with no key configured")

        check("returns None", fetch_supplement_rows(session=Boom()), None)
    finally:
        if saved is not None:
            os.environ["LIVETENNISAPI_KEY"] = saved


def test_http_failures_return_none():
    print("\n[HTTP failures degrade to None]")

    class Resp:
        def __init__(self, code):
            self.status_code = code

        def json(self):
            return {"data": [], "meta": {}}

    class Sess:
        def __init__(self, code):
            self.code = code

        def get(self, *a, **k):
            return Resp(self.code)

    os.environ["LIVETENNISAPI_KEY"] = "test-key-not-real"
    try:
        for code in (401, 403, 429, 500):
            check(f"HTTP {code}", fetch_supplement_rows(session=Sess(code)), None)
    finally:
        os.environ.pop("LIVETENNISAPI_KEY", None)


def test_pagination_uses_has_more():
    print("\n[pagination follows meta.has_more]")

    pages = [
        {"data": [match(id=1)], "meta": {"has_more": True}},
        {"data": [match(id=2, players={"p1": {"name": "C", "ranking": 5},
                                       "p2": {"name": "D", "ranking": 6}})],
         "meta": {"has_more": False}},
        {"data": [match(id=3)], "meta": {"has_more": False}},  # must never load
    ]
    seen = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Sess:
        def get(self, url, params=None, **k):
            seen.append(params["offset"])
            return Resp(pages[len(seen) - 1])

    os.environ["LIVETENNISAPI_KEY"] = "test-key-not-real"
    try:
        df = fetch_supplement_rows(session=Sess())
        check("stopped after has_more=False", seen, [0, 200])
        check("both pages kept", len(df), 2)
    finally:
        os.environ.pop("LIVETENNISAPI_KEY", None)


if __name__ == "__main__":
    for fn in [
        test_happy_path,
        test_winner_is_p2_flips_score,
        test_empty_games_never_synthesised,
        test_unsettled_and_unusable_rows_dropped,
        test_nullable_fields_tolerated,
        test_bo5,
        test_score_string_edges,
        test_disabled_without_key,
        test_http_failures_return_none,
        test_pagination_uses_has_more,
    ]:
        fn()

    print("\n" + "=" * 50)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("All checks passed.")
