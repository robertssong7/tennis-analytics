"""Tennis Abstract scraper → canonical tournament_state.json.

Reads:
  - Tennis Abstract /current/<slug>.html for the active tournament
    (parses the inline `upcomingSingles` / `completedSingles` JS strings).
  - Tennis Abstract /charting/meta.html as a cross-check for charted
    matches the forecast page hasn't propagated yet.
  - data/processed/active_players.json for the active-tournament window.

Writes:
  - data/live/tournament_state.json (the canonical state object the API
    endpoints will read in Phase 3).
  - data/processed/live_matches_ingest.parquet (append-only completed
    matches feed for Phase 4 ratings refresh; pyarrow optional).

Source attribution: Jeff Sackmann / Tennis Abstract (CC BY-NC-SA 4.0).
Non-commercial use. Robots.txt permits /current/ and /charting/.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from rounds import current_round, round_order
from tournaments import meta_for, resolve_active, ta_slug_for, tz_for


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_LIVE = REPO_ROOT / "data" / "live"
DATA_PROC = REPO_ROOT / "data" / "processed"
STATE_PATH = DATA_LIVE / "tournament_state.json"
ACTIVE_PLAYERS = DATA_PROC / "active_players.json"

# Tennis Abstract sits behind Cloudflare. A plain non-browser UA gets
# bot-checked on cloud egress (GitHub Actions). Use a current desktop UA;
# attribution is preserved in the schema's data_source field instead.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SEC = 2  # polite gap between requests
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # No brotli: stdlib `requests` does not decompress it without the
    # optional `brotli` package, and the response would arrive as garbage.
    "Accept-Encoding": "gzip, deflate",
}


def _log(msg: str) -> None:
    sys.stderr.write(f"[live] {msg}\n")


def _fetch(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        _log(f"fetch_error {url}: {e}")
        return None
    if resp.status_code != 200:
        _log(f"fetch_status_{resp.status_code} bytes={len(resp.content)} {url}")
        return None
    body = resp.text
    _log(f"fetch_ok status=200 bytes={len(body)} {url}")
    # Trip an obvious "Cloudflare challenge / interstitial" signal: real pages
    # are >50KB, challenge pages are <10KB. Log so the workflow surfaces it.
    if len(body) < 5000 and "challenge" in body.lower():
        _log(f"likely_cf_challenge {url}")
        return None
    return body


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


# -----------------------------------------------------------------------------
# Tennis Abstract per-tournament forecast page parsing
# -----------------------------------------------------------------------------

_VAR_RE = re.compile(r"var\s+(\w+)\s*=\s*'(.*?)';\s*$", re.MULTILINE | re.DOTALL)
_MATCH_LINE_SEP = re.compile(r"<br\s*/?>")
# Examples handled:
#   "QF: (1)<a ...>Jannik Sinner</a> (ITA) d. (12)<a ...>Andrey Rublev</a> (RUS) 6-2 6-4"
#   "SF: (1)<a ...>Jannik Sinner</a> (ITA) vs (7)<a ...>Daniil Medvedev</a> (RUS) [h2h]"
_COMPLETED_RE = re.compile(
    r"""^(?P<round>R?\d+|QF|SF|F|1R|2R|3R|4R)\s*:\s*
        (?:\([^)]*\))?\s*<a[^>]*>(?P<p1>[^<]+)</a>\s*(?:\([A-Z]{2,3}\))?
        \s*(?:<a[^>]*>)?d\.(?:</a>)?\s*
        (?:\([^)]*\))?\s*<a[^>]*>(?P<p2>[^<]+)</a>\s*(?:\([A-Z]{2,3}\))?
        \s*(?P<score>.+?)$""",
    re.VERBOSE,
)
_UPCOMING_RE = re.compile(
    r"""^(?P<round>R?\d+|QF|SF|F|1R|2R|3R|4R)\s*:\s*
        (?:\([^)]*\))?\s*<a[^>]*>(?P<p1>[^<]+)</a>\s*(?:\([A-Z]{2,3}\))?
        \s*vs\s*
        (?:\([^)]*\))?\s*<a[^>]*>(?P<p2>[^<]+)</a>\s*(?:\([A-Z]{2,3}\))?
        \s*.*$""",
    re.VERBOSE,
)


def _parse_js_var(html: str, name: str) -> str | None:
    for m in _VAR_RE.finditer(html):
        if m.group(1) == name:
            return m.group(2)
    return None


def _parse_matches(blob: str, completed: bool) -> list[dict]:
    """Split a JS string on <br/> and parse each line into a match dict."""
    matches: list[dict] = []
    for line in _MATCH_LINE_SEP.split(blob or ""):
        s = line.strip()
        if not s or s in ("&nbsp;",):
            continue
        if completed:
            mo = _COMPLETED_RE.match(s)
        else:
            mo = _UPCOMING_RE.match(s)
        if not mo:
            continue
        round_label = mo.group("round").upper()
        p1 = _strip_tags(mo.group("p1")).strip()
        p2 = _strip_tags(mo.group("p2")).strip()
        if not p1 or not p2:
            continue
        score = _strip_tags(mo.group("score")).strip() if completed else ""
        # Score field on Tennis Abstract may include a trailing "[h2h]" link
        # in the rendered HTML; the regex strips tags so the value is plain text.
        status, winner = ("completed", p1) if completed else ("scheduled", None)
        ret = walkover = False
        if completed:
            score_lower = score.lower()
            if "ret" in score_lower:
                status = "retired"
                ret = True
            if "w/o" in score_lower or "walkover" in score_lower:
                status = "walkover"
                walkover = True
        m: dict[str, Any] = {
            "round": round_label,
            "round_order": round_order(round_label),
            "p1": p1,
            "p2": p2,
            "status": status,
        }
        if completed:
            m["winner"] = winner
            m["score"] = score
            if ret:
                m["retired"] = True
            if walkover:
                m["walkover"] = True
        matches.append(m)
    return matches


# -----------------------------------------------------------------------------
# Charting meta cross-check
# -----------------------------------------------------------------------------

_META_MATCH_RE = re.compile(r"(20\d{6})-M-([A-Za-z_]+)-(R?\d+|QF|SF|F)-([A-Za-z_-]+)-([A-Za-z_-]+)\.html")


def _charted_keys(meta_html: str, ta_slug: str) -> set[tuple[str, str, str]]:
    """Return a set of (round, p1, p2) tuples for matches charted in the
    given tournament. The slug we receive is "2026ATPRome"; the charting
    URLs use "Rome_Masters" / "Roland_Garros" / etc. We extract the year
    and a tournament keyword from the slug and match permissively.
    """
    keyword = ta_slug.replace("2026", "").replace("ATP", "").strip("_") or ""
    # Map TA forecast slug → charting tournament tokens we expect in URLs.
    KW_MAP = {
        "Rome":          ["Rome_Masters", "Rome"],
        "Madrid":        ["Madrid_Masters", "Madrid"],
        "MonteCarlo":    ["Monte_Carlo_Masters", "Monte_Carlo"],
        "Barcelona":     ["Barcelona"],
        "FrenchOpenMen": ["Roland_Garros"],
        "WimbledonMen":  ["Wimbledon"],
        "USOpenMen":     ["US_Open", "USOpen"],
        "AustralianOpenMen": ["Australian_Open"],
        "Cincinnati":    ["Cincinnati_Masters", "Cincinnati"],
        "Canada":        ["Canada_Masters", "Toronto_Masters", "Montreal_Masters"],
        "Shanghai":      ["Shanghai_Masters", "Shanghai"],
        "Paris":         ["Paris_Masters", "Paris"],
        "IndianWells":   ["Indian_Wells_Masters", "Indian_Wells"],
        "Miami":         ["Miami_Masters", "Miami"],
    }
    keywords = KW_MAP.get(keyword, [keyword])
    found: set[tuple[str, str, str]] = set()
    for ymd, t_name, round_label, p1_slug, p2_slug in _META_MATCH_RE.findall(meta_html):
        if not ymd.startswith(str(datetime.now(timezone.utc).year)):
            continue
        if not any(kw and kw in t_name for kw in keywords):
            continue
        p1 = p1_slug.replace("_", " ")
        p2 = p2_slug.replace("_", " ")
        found.add((round_label.upper(), p1, p2))
    return found


# -----------------------------------------------------------------------------
# Baseline (top ATP) and withdrawal heuristic
# -----------------------------------------------------------------------------

_ELO_PLAYER_RE = re.compile(r'player\.cgi\?p=[A-Za-z]+">([A-Za-z][^<]+)</a>')


def _fetch_top_atp(n: int = 30) -> list[str]:
    """Top N ATP names from Tennis Abstract's Elo ratings page (best-effort).

    Used as a baseline to flag pre-tournament absences in the current draw.
    On fetch failure returns []; the scraper still works, just without the
    absence list. Names are returned with regular spaces (the page uses nbsp).
    """
    html = _fetch("https://www.tennisabstract.com/reports/atp_elo_ratings.html")
    if not html:
        return []
    names: list[str] = []
    for name in _ELO_PLAYER_RE.findall(html):
        clean = name.replace("\xa0", " ").replace("&nbsp;", " ").strip()
        if clean and clean not in names:
            names.append(clean)
            if len(names) >= n:
                break
    return names


def _detect_absences(draw: list[dict], baseline_players: list[str]) -> list[dict]:
    """Players in the baseline (top ATP by Elo) who are NOT in the current
    draw. Reported with reason "not stated" until a future source annotates
    the reason. Honest absence-from-draw flag, not a fabricated claim.
    """
    present: set[str] = set()
    for m in draw:
        present.add(m["p1"])
        present.add(m["p2"])
    absences: list[dict] = []
    for name in baseline_players:
        if name in present:
            continue
        absences.append({
            "player": name,
            "reason": "not stated",
            "source": "absent_from_top_atp_elo_baseline",
        })
    return absences


# -----------------------------------------------------------------------------
# Draw size inference
# -----------------------------------------------------------------------------

def _infer_draw_size(draw: list[dict]) -> int | None:
    """Derive nominal draw size from rounds + match counts.

    Tennis Abstract labels the play-in round of a 96-draw event as R128 even
    though only 32 unseeded matches play (the top 32 seeds bye into R64), so
    we have to look at match counts, not just labels.
    """
    from collections import Counter
    counts = Counter(m["round"] for m in draw)
    r128 = counts.get("R128", 0)
    r64 = counts.get("R64", 0)
    r32 = counts.get("R32", 0)
    # 128-draw: ~64 R128 matches expected
    if r128 >= 50:
        return 128
    # 96-draw: ~32 R128 + ~32 R64 (top 32 seeds bye R128 → R64)
    if r128 > 0 and r64 >= 24:
        return 96
    # 64-draw: ~32 R64 matches
    if r64 >= 24:
        return 64
    # 56-draw / 48-draw / 32-draw: smaller
    if r32 >= 12 and "1R" not in counts:
        return 56
    if "1R" in counts:
        return 32
    return None


# -----------------------------------------------------------------------------
# State assembly
# -----------------------------------------------------------------------------

def _remaining_from_draw(draw: list[dict]) -> list[str]:
    """Players who are scheduled/in_progress, or who won their latest
    completed match without a later loss showing.
    """
    eliminated: set[str] = set()
    for m in draw:
        if m.get("status") == "completed" and m.get("winner"):
            loser = m["p2"] if m["winner"] == m["p1"] else m["p1"]
            eliminated.add(loser)
    survivors: set[str] = set()
    for m in draw:
        if m.get("status") in ("scheduled", "in_progress"):
            survivors.add(m["p1"])
            survivors.add(m["p2"])
        elif m.get("status") == "completed" and m.get("winner"):
            survivors.add(m["winner"])
    return sorted(p for p in survivors if p not in eliminated)


def _read_active_players() -> dict:
    if not ACTIVE_PLAYERS.exists():
        return {"tournaments": [], "all_active_players": []}
    return json.loads(ACTIVE_PLAYERS.read_text())


def build_state(now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    active_doc = _read_active_players()
    active = resolve_active(now.date(), active_doc.get("tournaments", []))
    if not active:
        _log("no_active_tournament_for_date")
        return None
    name = active["name"]
    slug = ta_slug_for(name)
    if not slug:
        _log(f"no_ta_slug_mapping for {name!r}; extend tournaments.TA_SLUG")
        return None

    forecast_url = f"https://www.tennisabstract.com/current/{slug}.html"
    html = _fetch(forecast_url)
    if not html:
        _log(f"could_not_fetch {forecast_url}")
        return None
    time.sleep(REQUEST_DELAY_SEC)
    meta_html = _fetch("https://www.tennisabstract.com/charting/meta.html") or ""

    completed_blob = _parse_js_var(html, "completedSingles") or ""
    upcoming_blob = _parse_js_var(html, "upcomingSingles") or ""
    completed = _parse_matches(completed_blob, completed=True)
    upcoming = _parse_matches(upcoming_blob, completed=False)

    # Cross-check: any upcoming match that already appears in the charting
    # meta as charted is in_progress (charted but not yet propagated to the
    # forecast page).
    charted_keys = _charted_keys(meta_html, slug) if meta_html else set()
    for m in upcoming:
        key_a = (m["round"], m["p1"], m["p2"])
        key_b = (m["round"], m["p2"], m["p1"])
        if key_a in charted_keys or key_b in charted_keys:
            m["status"] = "in_progress"
            m["source_note"] = "charted_on_match_charting_project"

    draw = completed + upcoming
    cur_round = current_round(draw)

    # Withdrawal/absence heuristic: Tennis Abstract top-30 ATP Elo baseline
    # cross-checked with the project's local active-players list.
    baseline = _fetch_top_atp(30)
    if not baseline:
        baseline = active_doc.get("all_active_players", [])
    withdrawals = _detect_absences(draw, baseline)

    remaining = _remaining_from_draw(draw)

    meta = meta_for(name)
    state = {
        "tournament": meta.get("canonical_name", name),
        "year": now.year,
        "surface": active.get("surface", meta.get("surface", "")).lower() if isinstance(active.get("surface"), str) else meta.get("surface", ""),
        "location": meta.get("location", ""),
        "category": active.get("level", meta.get("category", "")),
        "start_date": active.get("start"),
        "end_date": active.get("end"),
        "current_round": cur_round,
        "draw_size": _infer_draw_size(draw),
        "draw": draw,
        "withdrawals": withdrawals,
        "remaining_players": remaining,
        "last_updated_utc": now.strftime("%Y-%m-%dT%H:%MZ"),
        "data_freshness": "live",
        "data_source": "tennis_abstract (current/+charting+atp_elo)",
        "tournament_tz": tz_for(name),
    }
    return state


def write_state(state: dict) -> Path:
    DATA_LIVE.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    _log(f"wrote {STATE_PATH} ({len(state.get('draw', []))} matches, current_round={state.get('current_round')})")
    return STATE_PATH


def append_completed_to_history(state: dict) -> None:
    """Append completed matches to data/processed/live_matches_ingest.parquet.

    No-op if pyarrow is unavailable or no completed matches exist.
    Phase 4 ratings refresh consumes this file.
    """
    completed = [m for m in state.get("draw", []) if m.get("status") in ("completed", "retired", "walkover") and m.get("winner")]
    if not completed:
        return
    try:
        import pandas as pd
    except ImportError:
        _log("pandas_unavailable_skipping_history_append")
        return
    rows = []
    for m in completed:
        rows.append({
            "tournament": state["tournament"],
            "year": state["year"],
            "surface": state.get("surface", ""),
            "round": m["round"],
            "winner": m["winner"],
            "loser": m["p2"] if m["winner"] == m["p1"] else m["p1"],
            "score": m.get("score", ""),
            "status": m["status"],
            "captured_utc": state["last_updated_utc"],
        })
    df = pd.DataFrame(rows)
    out = DATA_PROC / "live_matches_ingest.parquet"
    try:
        if out.exists():
            existing = pd.read_parquet(out)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["tournament", "year", "round", "winner", "loser"], keep="last")
        df.to_parquet(out, index=False)
        _log(f"appended {len(rows)} completed matches to {out}")
    except Exception as e:
        _log(f"history_append_error: {e}")


def main() -> int:
    state = build_state()
    if not state:
        _log("no_state_produced")
        # Halt condition: do not overwrite the previous state file with a
        # blank object — leave whatever was last written so the API keeps
        # serving the most recent good snapshot.
        return 1
    # Halt condition: if the scraper succeeded only at populating the
    # tournament-name shell but found no matches (Tennis Abstract returned
    # a bot challenge, an empty forecast, or its page format changed) AND
    # we already have a populated state file on disk, preserve the existing
    # state and exit 1. Better to serve slightly stale data than to publish
    # an empty draw with current_round defaulting to "1R".
    if not state.get("draw"):
        if STATE_PATH.exists():
            try:
                prev = json.loads(STATE_PATH.read_text())
                if prev.get("draw"):
                    _log("preserving_prior_state empty_scrape suppressed")
                    return 1
            except json.JSONDecodeError:
                pass
    write_state(state)
    append_completed_to_history(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
