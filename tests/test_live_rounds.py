"""Regression snapshot: refresh commit 4672f50, 2026-10-11 21:19 UTC."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from src.live_tournament.rounds import current_round, normalize_round, round_order
from src.api import main as api

FIXTURE = Path(__file__).parent / "fixtures" / "shanghai_2026_10_11.json"

@pytest.fixture
def shanghai():
    return json.loads(FIXTURE.read_text())

@pytest.mark.parametrize("labels,rank", [
    (("R1", "1R", "R128"), 1), (("R2", "2R", "R64"), 2),
    (("R3", "3R", "R32"), 3), (("R4", "4R", "R16"), 4),
    (("QF", "Quarterfinals"), 5), (("SF", "Semifinals"), 6), (("F", "The Final"), 7),
])
def test_aliases(labels, rank):
    assert {round_order(" " + label.lower() + " ") for label in labels} == {rank}
    assert len({normalize_round(label) for label in labels}) == 1


def test_shanghai_progress(shanghai):
    pending = [m for m in shanghai["draw"] if m["status"] == "scheduled"]
    assert sum(m["round"] == "R3" for m in pending) == 8
    assert sum(m["round"] == "R4" for m in pending) == 4
    assert all(m["round_order"] == 0 for m in pending)
    assert current_round(list(reversed(shanghai["draw"]))) == "R3"
    for m in pending:
        if m["round"] == "R3":
            m["status"] = "completed"
    assert current_round(shanghai["draw"]) == "R4"


def test_statuses_and_stale_orders():
    draw = [dict(round="R3", status="scheduled", round_order=99),
            dict(round="R4", status="in_progress", round_order=0),
            dict(round="F", status="scheduled", round_order=0)]
    assert current_round(draw) == "R4"
    draw[1]["status"] = "walkover"
    assert current_round(draw) == "R3"
    draw[0]["status"] = "retired"
    draw[2]["status"] = "completed"
    assert current_round(draw) == "F"
    assert current_round([]) == "1R"
    assert round_order("unknown") == 0
    assert current_round([dict(round="unknown", status="scheduled"),
                          dict(round="R3", status="scheduled")]) == "R3"


@pytest.fixture
def client(monkeypatch, tmp_path, shanghai):
    # Use the exact snapshot and freeze API time so freshness is deterministic.
    from datetime import datetime, timezone
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 11, 22, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "datetime", Clock)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(shanghai))
    monkeypatch.setattr(api, "LIVE_STATE_PATH", path)
    calendar = dict(name="Shanghai Masters", start="2026-10-04", city="Shanghai")
    monkeypatch.setattr(api, "get_live_tournament", lambda today: calendar)
    monkeypatch.setattr(api, "get_just_finished", lambda today: None)
    monkeypatch.setattr(api, "get_next_upcoming", lambda today: None)
    # Feed must be truthy to receive state enrichment.
    monkeypatch.setattr(api, "_calendar_to_feed", lambda *a, **k: {"name": "Shanghai Masters"})
    monkeypatch.setattr(api, "_scrape_results_for_tournament", lambda *a: [])
    monkeypatch.setattr(api, "_fetch_weather", lambda *a: None)
    engine = SimpleNamespace(
        predict=lambda *a: {"player1_win_prob": .6, "player2_win_prob": .4},
        glicko=SimpleNamespace(ratings={}))
    monkeypatch.setattr(api, "_get_engine", lambda: engine)
    return TestClient(api.app), path


def test_live_tournament_endpoint(client):
    http, path = client
    response = http.get("/api/live-tournament")
    assert response.status_code == 200
    body = response.json()
    assert body["state"]["current_round"] == body["live"]["current_round"] == "R3"
    assert body["state"]["data_freshness"] == "live"
    assert all(m["round_order"] == round_order(m["round"]) for m in body["state"]["draw"])
    assert json.loads(path.read_text())["draw"][-1]["round_order"] == 0


def test_key_matchups_endpoint_advances(client):
    http, path = client
    body = http.get("/api/key-matchups-live").json()
    assert body["current_round"] == "R3"
    assert len(body["matchups"]) == 6
    assert {m["round"] for m in body["matchups"]} == {"R3"}
    state = json.loads(path.read_text())
    for m in state["draw"]:
        if m["round"] == "R3":
            m["status"] = "retired"
    path.write_text(json.dumps(state))
    body = http.get("/api/key-matchups-live").json()
    assert body["current_round"] == "R4"
    assert len(body["matchups"]) == 4
    assert {m["round"] for m in body["matchups"]} == {"R4"}


def test_completed_aliases_and_live_priority(monkeypatch):
    monkeypatch.setattr(api, "_get_engine", lambda: SimpleNamespace(
        predict=lambda *a: {"player1_win_prob": .5, "player2_win_prob": .5},
        glicko=SimpleNamespace(ratings={})))
    draw = [dict(round=r, status=s, p1="A", p2="B") for r,s in
            [("R3", "completed"), ("R4", "retired"), ("4R", "walkover")]]
    body = api._key_matchups_from_state({"draw": draw, "current_round": "R3"})
    assert body["current_round"] == "R4"
    assert len(body["matchups"]) == 2
    draw.append(dict(round="R3", status="in_progress", p1="C", p2="D"))
    draw.append(dict(round="F", status="scheduled", p1="E", p2="F"))
    assert api._key_matchups_from_state({"draw": draw})["matchups"][0]["player1"] == "C"


def test_scraper_ranks_source_aliases(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools" / "live_tournament"))
    import scraper
    blob = 'R3: <a>A</a> vs <a>B</a><br/>4R: <a>C</a> vs <a>D</a>'
    draw = scraper._parse_matches(blob, completed=False)
    assert [m["round_order"] for m in draw] == [3, 4]
    assert current_round(draw) == "R3"


def test_history_normalizes_aliases(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools" / "live_tournament"))
    import ingest_to_history as ingest
    assert {ingest._ROUND_ALIASES[ingest.normalize_round(r)] for r in ("R3", "3R", "R32")} == {"3rd Round"}
    assert {ingest._ROUND_ALIASES[ingest.normalize_round(r)] for r in ("R4", "4R", "R16")} == {"4th Round"}
