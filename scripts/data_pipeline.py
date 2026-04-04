"""
TennisIQ — Data Pipeline
scripts/data_pipeline.py

Ingests Sackmann ATP data + Match Charting Project into PostgreSQL.
Merges with existing /db/ data, deduplicates, preserves all dates.

Usage:
    python scripts/data_pipeline.py --phase init    # Clone repos + first load
    python scripts/data_pipeline.py --phase sync    # Weekly incremental sync
    python scripts/data_pipeline.py --phase validate
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from shot_sequence_parser import ShotSequenceParser

load_dotenv()
logger = logging.getLogger(__name__)

SACKMANN_ATP_URL = "https://github.com/JeffSackmann/tennis_atp"
SACKMANN_MCP_URL = "https://github.com/JeffSackmann/tennis_MatchChartingProject"

DATA_DIR = Path("data")
SACK_DIR = DATA_DIR / "sackmann"
ATP_DIR = SACK_DIR / "tennis_atp"
MCP_DIR = SACK_DIR / "tennis_MatchChartingProject"

# ─────────────────────────────────────────────────────────────
# Tournament surface lookup (from spec doc 6)
# ─────────────────────────────────────────────────────────────

HARD_TOURNAMENTS = {
    "Australian Open": "hard",
    "US Open": "hard",
    "Indian Wells Masters": "hard",
    "Miami Open": "hard",
    "Canadian Open": "hard",
    "Rogers Cup": "hard",
    "Western & Southern Open": "hard",
    "Cincinnati": "hard",
    "Shanghai Masters": "hard",
    "Paris Masters": "hard",
    "Rolex Paris Masters": "hard",
    "BNP Paribas Masters": "hard",
    "Dubai Duty Free Tennis Championships": "hard",
    "Qatar ExxonMobil Open": "hard",
    "Abierto Mexicano Telcel": "hard",
    "Brisbane International": "hard",
    "Sydney International": "hard",
    "Auckland Open": "hard",
    "Delray Beach Open": "hard",
    "Winston-Salem Open": "hard",
    "Erste Bank Open": "hard",
    "Swiss Indoors Basel": "hard",
}

CLAY_TOURNAMENTS = {
    "Roland Garros": "clay",
    "Monte-Carlo Masters": "clay",
    "Madrid Open": "clay",
    "Italian Open": "clay",
    "Barcelona Open": "clay",
    "Geneva Open": "clay",
    "Lyon Open": "clay",
    "Hamburg": "clay",
}

GRASS_TOURNAMENTS = {
    "Wimbledon": "grass",
    "Queens Club": "grass",
    "Halle Open": "grass",
    "Eastbourne International": "grass",
    "Stuttgart Open": "grass",
}

ALL_TOURNAMENT_SURFACES = {**HARD_TOURNAMENTS, **CLAY_TOURNAMENTS, **GRASS_TOURNAMENTS}


def get_surface_for_tournament(name: str) -> Optional[str]:
    """Look up surface for a tournament name (exact then fuzzy)."""
    if not name:
        return None
    exact = ALL_TOURNAMENT_SURFACES.get(name)
    if exact:
        return exact
    name_lower = name.lower()
    for known, surface in ALL_TOURNAMENT_SURFACES.items():
        if known.lower() in name_lower or name_lower in known.lower():
            return surface
    return None


# ─────────────────────────────────────────────────────────────
# Sackmann ATP result parser
# ─────────────────────────────────────────────────────────────


def parse_sackmann_match_row(row: dict) -> Optional[dict]:
    """Parse one row from Sackmann atp_matches_YYYY.csv"""
    try:
        match_date_str = str(row.get("tourney_date", "")).strip()
        if len(match_date_str) == 8:
            match_date = date(
                int(match_date_str[:4]),
                int(match_date_str[4:6]),
                int(match_date_str[6:8]),
            )
        else:
            return None

        winner_name = row.get("winner_name", "").strip()
        loser_name = row.get("loser_name", "").strip()
        if not winner_name or not loser_name:
            return None

        tournament = row.get("tourney_name", "").strip()
        surface_raw = row.get("surface", "").strip().lower()
        surface = get_surface_for_tournament(tournament) or surface_raw or None

        return {
            "match_date": match_date,
            "tournament": tournament,
            "surface": surface,
            "round": row.get("round", ""),
            "winner_name": winner_name,
            "loser_name": loser_name,
            "score": row.get("score", ""),
            "winner_rank": _safe_int(row.get("winner_rank")),
            "loser_rank": _safe_int(row.get("loser_rank")),
            "winner_hand": row.get("winner_hand", ""),
            "loser_hand": row.get("loser_hand", ""),
            "winner_ht": _safe_int(row.get("winner_ht")),
            "loser_ht": _safe_int(row.get("loser_ht")),
            "source": "sackmann_atp",
        }
    except Exception as e:
        return None


def _safe_int(val) -> Optional[int]:
    try:
        v = int(float(str(val).strip()))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────
# Deduplication key
# ─────────────────────────────────────────────────────────────


def dedup_key(tournament: str, match_date, winner: str, loser: str) -> str:
    """Create a deduplication key for a match."""
    d = str(match_date)[:10] if match_date else "unknown"
    t = re.sub(r"[^a-z0-9]", "", tournament.lower()) if tournament else ""
    w = re.sub(r"[^a-z]", "", winner.lower().split()[-1]) if winner else ""
    l = re.sub(r"[^a-z]", "", loser.lower().split()[-1]) if loser else ""
    return f"{d}-{t}-{w}-{l}"


# ─────────────────────────────────────────────────────────────
# Player name normalization
# ─────────────────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    """Normalize player name for matching."""
    return re.sub(r"\s+", " ", name.strip()).title()


# ─────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────


def reconnect(db_url: str) -> psycopg2.extensions.connection:
    """Open a fresh database connection."""
    return psycopg2.connect(db_url, connect_timeout=30)


def bulk_upsert_players(conn, player_map: dict) -> dict:
    """
    Insert all players in one statement, return {normalized_name: player_id}.
    player_map: {normalized_name: (hand, height)}
    """
    if not player_map:
        return {}
    rows = [(name, hand, ht) for name, (hand, ht) in player_map.items()]
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO players (name, hand, height_cm) VALUES %s ON CONFLICT (name) DO NOTHING",
            rows,
        )
        cur.execute(
            "SELECT name, player_id FROM players WHERE name = ANY(%s)",
            (list(player_map.keys()),),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def bulk_upsert_tournaments(conn, tourn_map: dict) -> dict:
    """
    Insert all tournaments in one statement, return {name: tournament_id}.
    tourn_map: {name: surface}
    """
    if not tourn_map:
        return {}
    rows = list(tourn_map.items())
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO tournaments (name, surface) VALUES %s ON CONFLICT (name) DO NOTHING",
            rows,
        )
        cur.execute(
            "SELECT name, tournament_id FROM tournaments WHERE name = ANY(%s)",
            (list(tourn_map.keys()),),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def get_or_create_player(conn, name: str, hand: str = None, height: int = None) -> int:
    """Single-player upsert (used by MCP loader)."""
    ids = bulk_upsert_players(conn, {normalize_name(name): (hand, height)})
    conn.commit()
    return ids[normalize_name(name)]


def get_or_create_tournament(conn, name: str, surface: str = None) -> int:
    """Single-tournament upsert (used by MCP loader)."""
    ids = bulk_upsert_tournaments(conn, {name: surface})
    conn.commit()
    return ids[name]


def upsert_file_read_status(
    conn,
    file_name: str,
    last_read_timestamp: datetime,
    latest_row_timestamp: Optional[datetime],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "FileReadStatus" (file_name, last_read_timestamp, latest_row_timestamp)
            VALUES (%s, %s, %s)
            ON CONFLICT (file_name) DO UPDATE
            SET last_read_timestamp = EXCLUDED.last_read_timestamp,
                latest_row_timestamp = EXCLUDED.latest_row_timestamp
            """,
            (file_name, last_read_timestamp, latest_row_timestamp),
        )


# ─────────────────────────────────────────────────────────────
# Clone or update Sackmann repos
# ─────────────────────────────────────────────────────────────


def clone_or_update(url: str, target: Path):
    if target.exists():
        logger.info("Updating %s...", target)
        subprocess.run(["/usr/bin/git", "pull"], cwd=target, capture_output=True)
    else:
        logger.info("Cloning %s...", url)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["/usr/bin/git", "clone", "--depth=1", url, str(target)], check=True
        )


# ─────────────────────────────────────────────────────────────
# Load Sackmann ATP matches
# ─────────────────────────────────────────────────────────────


def load_atp_matches(conn, unmatched_log: list, file_read_statuses: dict):
    """Load all Sackmann ATP match result CSVs into database."""
    match_files = sorted(ATP_DIR.glob("atp_matches_????.csv"))
    logger.info("Found %d ATP match result files", len(match_files))

    total_loaded = 0
    total_skipped = 0
    seen_keys = set()

    for fpath in match_files:
        logger.info("  Loading %s...", fpath.name)
        file_last_modified_timestamp = datetime.fromtimestamp(
            fpath.stat().st_mtime, tz=timezone.utc
        )
        file_last_read_status = file_read_statuses.get(fpath.name)
        if (
            file_last_read_status is None
            or file_last_modified_timestamp
            < file_last_read_status.get("last_read_timestamp")
        ):
            logger.info(
                f"Skipping {fpath.name} as it was last updated at {file_last_modified_timestamp}"
            )
            continue

        file_latest_match_date: Optional[date] = None

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                parsed = parse_sackmann_match_row(row)
                if not parsed:
                    total_skipped += 1
                    continue
                key = dedup_key(
                    parsed["tournament"],
                    parsed["match_date"],
                    parsed["winner_name"],
                    parsed["loser_name"],
                )
                if key in seen_keys:
                    total_skipped += 1
                    continue
                seen_keys.add(key)
                batch.append(parsed)
                row_match_date = parsed.get("match_date")
                if row_match_date and (
                    file_latest_match_date is None
                    or row_match_date > file_latest_match_date
                ):
                    file_latest_match_date = row_match_date

            if not batch:
                logger.info("  Done %s: 0 inserted", fpath.name)
                latest_row_ts = (
                    datetime(
                        file_latest_match_date.year,
                        file_latest_match_date.month,
                        file_latest_match_date.day,
                        tzinfo=timezone.utc,
                    )
                    if file_latest_match_date
                    else None
                )
                upsert_file_read_status(
                    conn=conn,
                    file_name=fpath.name,
                    last_read_timestamp=datetime.now(timezone.utc),
                    latest_row_timestamp=latest_row_ts,
                )
                conn.commit()
                continue

            try:
                # Collect unique players and tournaments across the whole file
                player_map = {}
                for m in batch:
                    wn = normalize_name(m["winner_name"])
                    ln = normalize_name(m["loser_name"])
                    if wn not in player_map:
                        player_map[wn] = (m.get("winner_hand"), m.get("winner_ht"))
                    if ln not in player_map:
                        player_map[ln] = (m.get("loser_hand"), m.get("loser_ht"))
                tourn_map = {m["tournament"]: m.get("surface") for m in batch}

                player_ids = bulk_upsert_players(conn, player_map)
                tourn_ids = bulk_upsert_tournaments(conn, tourn_map)

                match_rows = []
                for m in batch:
                    wn = normalize_name(m["winner_name"])
                    ln = normalize_name(m["loser_name"])
                    w_id = player_ids.get(wn)
                    l_id = player_ids.get(ln)
                    t_id = tourn_ids.get(m["tournament"])
                    if not w_id or not l_id or not t_id:
                        total_skipped += 1
                        continue
                    match_rows.append(
                        (
                            t_id,
                            m["match_date"],
                            m["round"],
                            m.get("surface"),
                            w_id,
                            l_id,
                            m.get("score"),
                            m["source"],
                        )
                    )

                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """INSERT INTO matches
                               (tournament_id, match_date, round, surface,
                                winner_id, loser_id, score, source)
                           VALUES %s ON CONFLICT DO NOTHING""",
                        match_rows,
                    )
                conn.commit()
                file_loaded = len(match_rows)
                total_loaded += file_loaded
                latest_row_ts = (
                    datetime(
                        file_latest_match_date.year,
                        file_latest_match_date.month,
                        file_latest_match_date.day,
                        tzinfo=timezone.utc,
                    )
                    if file_latest_match_date
                    else None
                )
                upsert_file_read_status(
                    conn=conn,
                    file_name=fpath.name,
                    last_read_timestamp=datetime.now(timezone.utc),
                    latest_row_timestamp=latest_row_ts,
                )
                conn.commit()
                logger.info("  Done %s: %d inserted", fpath.name, file_loaded)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                unmatched_log.append({"file": str(fpath.name), "error": str(e)})
                logger.error("  Error in %s: %s", fpath.name, e)
                total_skipped += len(batch)

    logger.info("ATP matches: %d loaded, %d skipped", total_loaded, total_skipped)
    return total_loaded


# ─────────────────────────────────────────────────────────────
# Load Match Charting Project data
# ─────────────────────────────────────────────────────────────


def _parse_score_state(pts: str) -> Tuple[bool, bool, bool]:
    """
    Return (is_break_point, is_set_point, is_match_point) from "Pts" text.
    """
    if not pts or "-" not in pts:
        return False, False, False
    srv, ret = [p.strip() for p in pts.split("-", 1)]
    is_break_point = (ret == "40" and srv in ("0", "15", "30")) or ret == "Ad"
    return is_break_point, False, False


def _serve_num(seq_1st: str, seq_2nd: str) -> int:
    return 2 if (seq_2nd or "").strip() else 1


def _normalize_serve_direction(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    v = str(val).strip().upper()
    if v in ("WIDE", "BODY"):
        return v.lower()
    if v == "T":
        return "T"
    return None


def load_mcp_matches(conn, unmatched_log: list, file_read_statuses: dict) -> int:
    """Load MCP matches, points, and parsed shots into database."""
    mcp_matches_file = MCP_DIR / "charting-m-matches.csv"
    if not mcp_matches_file.exists():
        logger.warning("MCP matches file not found: %s", mcp_matches_file)
        return 0

    batch = []
    latest_match_date = date.min
    mcp_matches_file_last_modified = datetime.fromtimestamp(
        mcp_matches_file.stat().st_mtime, tz=timezone.utc
    )
    mcp_matches_file_last_read_status = file_read_statuses.get(mcp_matches_file.name)
    if (
        mcp_matches_file_last_read_status is None
        or mcp_matches_file_last_read_status.get("last_read_timestamp")
        < mcp_matches_file_last_modified
    ):
        with open(mcp_matches_file, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                match_id_str = row.get("match_id", "").strip()
                date_str = row.get("Date", "").strip()
                if not match_id_str or len(date_str) != 8 or not date_str.isdigit():
                    continue
                try:
                    match_date = date(
                        int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                    )
                    latest_match_date = (
                        match_date
                        if match_date > latest_match_date
                        else latest_match_date
                    )
                except ValueError:
                    continue

                player1 = normalize_name(row.get("Player 1", ""))
                player2 = normalize_name(row.get("Player 2", ""))
                if not player1 or not player2:
                    continue

                tournament = row.get("Tournament", "").strip()
                surface_raw = row.get("Surface", "").strip().lower()
                surface = get_surface_for_tournament(tournament) or surface_raw or None
                round_name = row.get("Round", "").strip()
                hand1 = row.get("Pl 1 hand", "").strip() or None
                hand2 = row.get("Pl 2 hand", "").strip() or None

                batch.append(
                    {
                        "match_id_str": match_id_str,
                        "match_date": match_date,
                        "tournament": tournament,
                        "surface": surface,
                        "round": round_name,
                        "winner_name": player1,
                        "loser_name": player2,
                        "hand1": hand1,
                        "hand2": hand2,
                    }
                )

                upsert_file_read_status(
                    conn=conn,
                    file_name=mcp_matches_file.name,
                    last_read_timestamp=datetime.now(timezone.utc),
                    latest_row_timestamp=datetime(
                        latest_match_date.year,
                        latest_match_date.month,
                        latest_match_date.day,
                    ),
                )

    if not batch:
        logger.info("MCP matches: 0 loaded")
        return 0

    total_matches = 0

    try:
        player_map = {}
        for m in batch:
            wn, ln = m["winner_name"], m["loser_name"]
            if wn not in player_map:
                player_map[wn] = (m["hand1"], None)
            if ln not in player_map:
                player_map[ln] = (m["hand2"], None)
        tourn_map = {m["tournament"]: m["surface"] for m in batch}

        player_ids = bulk_upsert_players(conn, player_map)
        tourn_ids = bulk_upsert_tournaments(conn, tourn_map)

        match_rows = []
        for m in batch:
            w_id = player_ids.get(m["winner_name"])
            l_id = player_ids.get(m["loser_name"])
            t_id = tourn_ids.get(m["tournament"])
            if not w_id or not l_id or not t_id:
                continue
            match_rows.append(
                (
                    t_id,
                    m["match_date"],
                    m["round"],
                    m["surface"],
                    w_id,
                    l_id,
                    f"mcp:{m['match_id_str']}",
                )
            )

        with conn.cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO matches
                       (tournament_id, match_date, round, surface, winner_id, loser_id,
                        has_charting, source)
                   VALUES %s ON CONFLICT DO NOTHING""",
                [(t, d, r, s, w, l, True, src) for t, d, r, s, w, l, src in match_rows],
            )
        conn.commit()
        total_matches = len(match_rows)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        unmatched_log.append({"file": "charting-m-matches.csv", "error": str(e)})
        logger.error("MCP load error: %s", e)

    logger.info(
        "MCP matches: %d loaded | points: %d | shots: %d",
        total_matches,
    )
    return total_matches


# TODO(zifanxiang): Confirm the logic here that's writing out shot sequences.
def parse_shots_strings(conn):
    total_points = 0
    total_shots = 0

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT match_id, winner_id, loser_id, source
            FROM matches
            WHERE source LIKE 'mcp:%'
            """
        )
        match_lookup = {
            src[4:]: (match_id, winner_id, loser_id)
            for match_id, winner_id, loser_id, src in cur.fetchall()
        }

        point_files = sorted(MCP_DIR.glob("charting-m-points*.csv"))
        if not point_files:
            logger.warning("No MCP point files found in %s", MCP_DIR)

        # Ensure sync is idempotent for MCP point/shot loads.
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM shots
                WHERE point_id IN (
                    SELECT p.point_id
                    FROM points p
                    JOIN matches m ON m.match_id = p.match_id
                    WHERE m.source LIKE 'mcp:%'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM points
                WHERE match_id IN (
                    SELECT match_id FROM matches WHERE source LIKE 'mcp:%'
                )
                """
            )
        conn.commit()

        parser = ShotSequenceParser()
        for fpath in point_files:
            logger.info("Loading MCP points/shots from %s...", fpath.name)
            file_points = 0
            file_shots = 0
            file_skipped = 0
            pending_writes = 0

            with open(fpath, encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mcp_id = row.get("match_id", "").strip()
                    match_info = match_lookup.get(mcp_id)
                    if not match_info:
                        file_skipped += 1
                        continue

                    db_match_id, winner_id, loser_id = match_info

                    svr = row.get("Svr", "").strip()
                    if svr == "1":
                        server_id, returner_id = winner_id, loser_id
                    elif svr == "2":
                        server_id, returner_id = loser_id, winner_id
                    else:
                        file_skipped += 1
                        continue

                    pw = row.get("PtWinner", "").strip()
                    if pw == "1":
                        pt_winner_id = winner_id
                    elif pw == "2":
                        pt_winner_id = loser_id
                    else:
                        file_skipped += 1
                        continue

                    seq_1st = (row.get("1st") or "").strip()
                    seq_2nd = (row.get("2nd") or "").strip()
                    if not seq_1st and not seq_2nd:
                        file_skipped += 1
                        continue

                    pts = (row.get("Pts") or "").strip()
                    is_bp, is_sp, is_mp = _parse_score_state(pts)

                    try:
                        set_num = (
                            int(row.get("Set1", 0) or 0)
                            + int(row.get("Set2", 0) or 0)
                            + 1
                        )
                    except ValueError:
                        set_num = None
                    try:
                        game_num = int(row.get("Gm#", 0) or 0)
                    except ValueError:
                        game_num = None
                    try:
                        point_num = int(row.get("Pt", 0) or 0)
                    except ValueError:
                        point_num = None

                    parsed_shots = parser.parse_shot_string_into_arr(
                        first_shot_str=seq_1st,
                        second_shot_str=seq_2nd,
                        point_number=point_num,
                        point_match_id=db_match_id,
                    )
                    if not parsed_shots:
                        file_skipped += 1
                        continue

                    serve_shot = parsed_shots[0].as_dict()
                    serve_dir = _normalize_serve_direction(
                        serve_shot.get("serve_direction")
                    )
                    rally_sequence = seq_2nd if seq_2nd else seq_1st
                    rally_length = len(
                        [s for s in parsed_shots if s.shot_type.value != "SERVE"]
                    )
                    point_outcome = parsed_shots[-1].outcome.value

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO points
                                (match_id, set_num, game_num, point_num,
                                 server_id, returner_id, serve_num, serve_dir,
                                 serve_depth, rally_sequence, rally_length, outcome,
                                 winner_id, score_before,
                                 is_break_point, is_set_point, is_match_point)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING point_id
                            """,
                            (
                                db_match_id,
                                set_num,
                                game_num,
                                point_num,
                                server_id,
                                returner_id,
                                _serve_num(seq_1st, seq_2nd),
                                serve_dir,
                                None,
                                rally_sequence,
                                rally_length,
                                point_outcome,
                                pt_winner_id,
                                pts,
                                is_bp,
                                is_sp,
                                is_mp,
                            ),
                        )
                        point_id = cur.fetchone()[0]

                        next_non_serve_hitter = returner_id
                        for shot in parsed_shots:
                            shot_data = shot.as_dict()
                            shot_type = shot_data.get("shot_type")
                            court_position = shot_data.get("court_position")
                            if shot_type == "SERVE":
                                hitter_id = server_id
                            else:
                                hitter_id = next_non_serve_hitter
                                next_non_serve_hitter = (
                                    server_id
                                    if next_non_serve_hitter == returner_id
                                    else returner_id
                                )

                            is_approach = court_position == "APPROACH"
                            came_to_net = court_position in ("APPROACH", "NET")

                            cur.execute(
                                """
                                INSERT INTO shots
                                    (point_id, shot_num, player_id, shot_type, direction, depth,
                                     outcome, is_approach, came_to_net)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    point_id,
                                    shot_data.get("shot_num"),
                                    hitter_id,
                                    shot_type,
                                    shot_data.get("direction"),
                                    shot_data.get("depth"),
                                    shot_data.get("outcome"),
                                    is_approach,
                                    came_to_net,
                                ),
                            )
                    pending_writes += 1
                    if pending_writes >= 1000:
                        conn.commit()
                        pending_writes = 0
                    file_points += 1
                    file_shots += len(parsed_shots)

            if pending_writes > 0:
                conn.commit()
            logger.info(
                "  %s: %d points, %d shots, %d skipped",
                fpath.name,
                file_points,
                file_shots,
                file_skipped,
            )
            total_points += file_points
            total_shots += file_shots


# ─────────────────────────────────────────────────────────────
# Create train/val/test splits
# ─────────────────────────────────────────────────────────────


def create_splits(conn):
    """Create and lock train/val/test data splits."""
    DATA_DIR.mkdir(exist_ok=True)
    locked_dir = Path("data/locked")

    logger.info("Creating data splits...")
    with conn.cursor() as cur:
        # Validation set: 2023 hard court charted matches
        cur.execute(
            """
            SELECT m.match_id, m.match_date, m.surface, m.winner_id, m.loser_id
            FROM matches m
            WHERE m.match_date >= '2023-01-01'
              AND m.match_date <= '2023-12-31'
              AND m.has_charting = TRUE
              AND LOWER(m.surface) = 'hard'
            ORDER BY m.match_date
        """
        )
        val_rows = cur.fetchall()
        val_cols = [d[0] for d in cur.description]

        # Test set: 2024+ hard court charted matches — LOCKED
        cur.execute(
            """
            SELECT m.match_id, m.match_date, m.surface, m.winner_id, m.loser_id
            FROM matches m
            WHERE m.match_date >= '2024-01-01'
              AND m.has_charting = TRUE
              AND LOWER(m.surface) = 'hard'
            ORDER BY m.match_date
        """
        )
        test_rows = cur.fetchall()
        test_cols = [d[0] for d in cur.description]

    # Write validation CSV
    import csv

    val_path = DATA_DIR / "validation_2023_hard.csv"
    with open(val_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(val_cols)
        w.writerows(val_rows)
    logger.info("Validation set: %d matches → %s", len(val_rows), val_path)

    # Write and lock test set
    test_path = locked_dir / "test_set_LOCKED.csv"
    with open(test_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(test_cols)
        w.writerows(test_rows)

    # Lock test set
    import stat

    test_path.chmod(stat.S_IRUSR | stat.S_IRGRP)  # 444
    logger.info("Test set: %d matches → %s (LOCKED)", len(test_rows), test_path)

    # Write checksums
    checksum_dir = Path("checksums")
    checksum_dir.mkdir(exist_ok=True)
    for p in [val_path, test_path]:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        (checksum_dir / f"{p.name}.sha256").write_text(sha)
    logger.info("Checksums written.")


# ─────────────────────────────────────────────────────────────
# Data quality checks
# ─────────────────────────────────────────────────────────────


def run_quality_checks(conn) -> dict:
    issues = []
    with conn.cursor() as cur:
        # Row counts
        cur.execute("SELECT COUNT(*) FROM matches")
        n_matches = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM players")
        n_players = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM points")
        n_points = cur.fetchone()[0]

        # Null player IDs
        cur.execute(
            "SELECT COUNT(*) FROM matches WHERE winner_id IS NULL OR loser_id IS NULL"
        )
        null_players = cur.fetchone()[0]
        if null_players > 0:
            issues.append(f"{null_players} matches have null winner/loser IDs")

        # Future dates
        cur.execute("SELECT COUNT(*) FROM matches WHERE match_date > CURRENT_DATE")
        future = cur.fetchone()[0]
        if future > 0:
            issues.append(f"{future} matches have future dates")

        # Charted match coverage
        cur.execute("SELECT COUNT(*) FROM matches WHERE has_charting = TRUE")
        charted = cur.fetchone()[0]

    return {
        "n_matches": n_matches,
        "n_players": n_players,
        "n_points": n_points,
        "charted_matches": charted,
        "issues": issues,
        "ok": len(issues) == 0,
    }


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=["init", "sync", "validate", "splits"], default="validate"
    )
    parser.add_argument("--sources", choices=["all", "atp", "mcp"], default="all")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in .env")
        sys.exit(1)

    conn = psycopg2.connect(db_url, connect_timeout=30)

    if args.phase in ("init", "sync"):
        clone_or_update(SACKMANN_ATP_URL, ATP_DIR)
        clone_or_update(SACKMANN_MCP_URL, MCP_DIR)

        # Run schema
        schema_sql = Path("schema.sql").read_text()
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()

        unmatched = []
        # Reconnect after schema DDL to get a clean transaction state
        conn.close()
        conn = reconnect(db_url)
        n_atp = 0

        file_read_statuses = {}
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM "FileReadStatus"')
            file_read_statuses = {
                row[0]: {"last_read_timestamp": row[1], "latest_row_timestamp": row[2]}
                for row in cursor.fetchall()
            }
        if args.sources in ("all", "atp"):
            n_atp = load_atp_matches(
                conn, unmatched, file_read_statuses=file_read_statuses
            )
        conn.close()
        conn = reconnect(db_url)

        n_mcp = 0
        if args.sources in ("all", "mcp"):
            n_mcp = load_mcp_matches(
                conn, unmatched, file_read_statuses=file_read_statuses
            )

        if unmatched:
            Path("data/unmatched_names.log").write_text(
                json.dumps(unmatched, indent=2, default=str)
            )
            logger.warning(
                "%d unmatched records — see data/unmatched_names.log", len(unmatched)
            )

        logger.info("Phase 1 complete — ATP: %d, MCP: %d", n_atp, n_mcp)

    if args.phase == "splits":
        create_splits(conn)

    if args.phase in ("validate", "init"):
        checks = run_quality_checks(conn)
        print(json.dumps(checks, indent=2))
        if checks["ok"]:
            logger.info("Phase 1 complete — all quality checks passed")
            logger.info(
                "Matches: %d  Players: %d  Points: %d",
                checks["n_matches"],
                checks["n_players"],
                checks["n_points"],
            )
        else:
            for issue in checks["issues"]:
                logger.error("  %s", issue)

    conn.close()


if __name__ == "__main__":
    main()
