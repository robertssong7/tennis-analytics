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
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SACKMANN_ATP_URL = "https://github.com/JeffSackmann/tennis_atp"
SACKMANN_MCP_URL = "https://github.com/JeffSackmann/tennis_MatchChartingProject"

DATA_DIR  = Path("data")
SACK_DIR  = DATA_DIR / "sackmann"
ATP_DIR   = SACK_DIR / "tennis_atp"
MCP_DIR   = SACK_DIR / "tennis_MatchChartingProject"

# ─────────────────────────────────────────────────────────────
# Tournament surface lookup (from spec doc 6)
# ─────────────────────────────────────────────────────────────

HARD_TOURNAMENTS = {
    "Australian Open": "hard", "US Open": "hard",
    "Indian Wells Masters": "hard", "Miami Open": "hard",
    "Canadian Open": "hard", "Rogers Cup": "hard",
    "Western & Southern Open": "hard", "Cincinnati": "hard",
    "Shanghai Masters": "hard", "Paris Masters": "hard",
    "Rolex Paris Masters": "hard", "BNP Paribas Masters": "hard",
    "Dubai Duty Free Tennis Championships": "hard",
    "Qatar ExxonMobil Open": "hard",
    "Abierto Mexicano Telcel": "hard",
    "Brisbane International": "hard", "Sydney International": "hard",
    "Auckland Open": "hard", "Delray Beach Open": "hard",
    "Winston-Salem Open": "hard",
    "Erste Bank Open": "hard", "Swiss Indoors Basel": "hard",
}

CLAY_TOURNAMENTS = {
    "Roland Garros": "clay", "Monte-Carlo Masters": "clay",
    "Madrid Open": "clay", "Italian Open": "clay",
    "Barcelona Open": "clay", "Geneva Open": "clay",
    "Lyon Open": "clay", "Hamburg": "clay",
}

GRASS_TOURNAMENTS = {
    "Wimbledon": "grass", "Queens Club": "grass",
    "Halle Open": "grass", "Eastbourne International": "grass",
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
# Shot sequence parser
# ─────────────────────────────────────────────────────────────

SHOT_TYPE_MAP = {
    "f": "forehand", "b": "backhand",
    "r": "slice", "s": "overhead",
    "v": "forehand_volley", "z": "backhand_volley",
    "o": "lob", "p": "swinging_volley", "u": "half_volley",
    "y": "stick_volley", "k": "stroke",
}

DIRECTION_MAP = {
    "1": "down_line", "2": "crosscourt", "3": "middle",
    "4": "down_line_short", "5": "crosscourt_short", "6": "middle_short",
    "7": "down_line_wide", "8": "crosscourt_wide", "9": "middle_wide",
}

OUTCOME_MAP = {
    "@": "uf_error", "#": "forced_error",
    "!": "winner", "*": "error_long_wide",
    ";": "in", " ": "in",
}


def parse_shot_sequence(seq: str) -> List[dict]:
    """
    Parse Sackmann MCP shot sequence string into list of shot events.
    Returns list of dicts with shot_type, direction, depth, outcome.
    """
    if not seq or not isinstance(seq, str):
        return []

    shots = []
    i = 0
    shot_num = 1

    while i < len(seq):
        c = seq[i]
        shot = {
            "shot_num":  shot_num,
            "shot_type": None,
            "direction": None,
            "depth":     None,
            "outcome":   "in",
        }

        # Shot type
        if c.lower() in SHOT_TYPE_MAP:
            shot["shot_type"] = SHOT_TYPE_MAP[c.lower()]
            i += 1
        elif c in ("S", "Q"):
            shot["shot_type"] = "serve"
            i += 1
        elif c in ("A",):
            shot["shot_type"] = "serve"
            shot["outcome"]   = "winner"
            i += 1
        else:
            i += 1
            continue

        # Direction (next char, if digit)
        if i < len(seq) and seq[i].isdigit():
            shot["direction"] = DIRECTION_MAP.get(seq[i], seq[i])
            i += 1

        # Outcome modifiers
        while i < len(seq) and seq[i] in OUTCOME_MAP:
            shot["outcome"] = OUTCOME_MAP[seq[i]]
            i += 1

        if shot["shot_type"]:
            shots.append(shot)
            shot_num += 1

    return shots


# ─────────────────────────────────────────────────────────────
# Sackmann ATP result parser
# ─────────────────────────────────────────────────────────────

def parse_sackmann_match_row(row: dict) -> Optional[dict]:
    """Parse one row from Sackmann atp_matches_YYYY.csv"""
    try:
        match_date_str = str(row.get("tourney_date", "")).strip()
        if len(match_date_str) == 8:
            match_date = date(int(match_date_str[:4]),
                              int(match_date_str[4:6]),
                              int(match_date_str[6:8]))
        else:
            return None

        winner_name = row.get("winner_name", "").strip()
        loser_name  = row.get("loser_name", "").strip()
        if not winner_name or not loser_name:
            return None

        tournament = row.get("tourney_name", "").strip()
        surface_raw = row.get("surface", "").strip().lower()
        surface = get_surface_for_tournament(tournament) or surface_raw or None

        return {
            "match_date":    match_date,
            "tournament":    tournament,
            "surface":       surface,
            "round":         row.get("round", ""),
            "winner_name":   winner_name,
            "loser_name":    loser_name,
            "score":         row.get("score", ""),
            "winner_rank":   _safe_int(row.get("winner_rank")),
            "loser_rank":    _safe_int(row.get("loser_rank")),
            "winner_hand":   row.get("winner_hand", ""),
            "loser_hand":    row.get("loser_hand", ""),
            "winner_ht":     _safe_int(row.get("winner_ht")),
            "loser_ht":      _safe_int(row.get("loser_ht")),
            "source":        "sackmann_atp",
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
        subprocess.run(["/usr/bin/git", "clone", "--depth=1", url, str(target)], check=True)


# ─────────────────────────────────────────────────────────────
# Load Sackmann ATP matches
# ─────────────────────────────────────────────────────────────

def load_atp_matches(conn, unmatched_log: list):
    """Load all Sackmann ATP match result CSVs into database."""
    match_files = sorted(ATP_DIR.glob("atp_matches_????.csv"))
    logger.info("Found %d ATP match result files", len(match_files))

    total_loaded = 0
    total_skipped = 0
    seen_keys = set()

    for fpath in match_files:
        logger.info("  Loading %s...", fpath.name)
        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                parsed = parse_sackmann_match_row(row)
                if not parsed:
                    total_skipped += 1
                    continue
                key = dedup_key(
                    parsed["tournament"], parsed["match_date"],
                    parsed["winner_name"], parsed["loser_name"]
                )
                if key in seen_keys:
                    total_skipped += 1
                    continue
                seen_keys.add(key)
                batch.append(parsed)

            if not batch:
                logger.info("  Done %s: 0 inserted", fpath.name)
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
                tourn_ids  = bulk_upsert_tournaments(conn, tourn_map)

                match_rows = []
                for m in batch:
                    wn  = normalize_name(m["winner_name"])
                    ln  = normalize_name(m["loser_name"])
                    w_id = player_ids.get(wn)
                    l_id = player_ids.get(ln)
                    t_id = tourn_ids.get(m["tournament"])
                    if not w_id or not l_id or not t_id:
                        total_skipped += 1
                        continue
                    match_rows.append((
                        t_id, m["match_date"], m["round"], m.get("surface"),
                        w_id, l_id, m.get("score"), m["source"]
                    ))

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

def load_mcp_matches(conn, unmatched_log: list) -> int:
    """Load MCP charting-m-matches.csv metadata into database."""
    mcp_matches_file = MCP_DIR / "charting-m-matches.csv"
    if not mcp_matches_file.exists():
        logger.warning("MCP matches file not found: %s", mcp_matches_file)
        return 0

    batch = []
    with open(mcp_matches_file, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            match_id_str = row.get("match_id", "")
            # Use the dedicated Date column (YYYYMMDD) — more reliable than parsing match_id
            date_str = row.get("Date", "").strip()
            if len(date_str) != 8 or not date_str.isdigit():
                continue
            try:
                match_date = date(int(date_str[:4]),
                                  int(date_str[4:6]),
                                  int(date_str[6:8]))
            except ValueError:
                continue

            # Actual column names from charting-m-matches.csv
            player1 = normalize_name(row.get("Player 1", ""))
            player2 = normalize_name(row.get("Player 2", ""))
            if not player1 or not player2:
                continue

            tournament  = row.get("Tournament", "").strip()
            surface_raw = row.get("Surface", "").strip().lower()
            surface     = get_surface_for_tournament(tournament) or surface_raw or None
            round_name  = row.get("Round", "").strip()
            hand1       = row.get("Pl 1 hand", "").strip() or None
            hand2       = row.get("Pl 2 hand", "").strip() or None

            # In MCP men's data, Player 1 is always the winner
            winner_name = player1
            loser_name  = player2

            batch.append({
                "match_id_str": match_id_str,
                "match_date":   match_date,
                "tournament":   tournament,
                "surface":      surface,
                "round":        round_name,
                "winner_name":  winner_name,
                "loser_name":   loser_name,
                "hand1":        hand1,
                "hand2":        hand2,
            })

    if not batch:
        logger.info("MCP matches: 0 loaded")
        return 0

    try:
        # Build player map with handedness from MCP
        player_map = {}
        for m in batch:
            wn, ln = m["winner_name"], m["loser_name"]
            if wn not in player_map:
                player_map[wn] = (m["hand1"], None)
            if ln not in player_map:
                player_map[ln] = (m["hand2"], None)
        tourn_map = {m["tournament"]: m["surface"] for m in batch}

        player_ids = bulk_upsert_players(conn, player_map)
        tourn_ids  = bulk_upsert_tournaments(conn, tourn_map)

        match_rows = []
        for m in batch:
            w_id = player_ids.get(m["winner_name"])
            l_id = player_ids.get(m["loser_name"])
            t_id = tourn_ids.get(m["tournament"])
            if not w_id or not l_id or not t_id:
                continue
            match_rows.append((
                t_id, m["match_date"], m["round"], m["surface"],
                w_id, l_id, f"mcp:{m['match_id_str']}"
            ))

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
        total = len(match_rows)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        unmatched_log.append({"file": "charting-m-matches.csv", "error": str(e)})
        logger.error("MCP load error: %s", e)
        total = 0

    logger.info("MCP matches: %d loaded", total)
    return total


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
        cur.execute("""
            SELECT m.match_id, m.match_date, m.surface, m.winner_id, m.loser_id
            FROM matches m
            WHERE m.match_date >= '2023-01-01'
              AND m.match_date <= '2023-12-31'
              AND m.has_charting = TRUE
              AND LOWER(m.surface) = 'hard'
            ORDER BY m.match_date
        """)
        val_rows = cur.fetchall()
        val_cols = [d[0] for d in cur.description]

        # Test set: 2024+ hard court charted matches — LOCKED
        cur.execute("""
            SELECT m.match_id, m.match_date, m.surface, m.winner_id, m.loser_id
            FROM matches m
            WHERE m.match_date >= '2024-01-01'
              AND m.has_charting = TRUE
              AND LOWER(m.surface) = 'hard'
            ORDER BY m.match_date
        """)
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
        cur.execute("SELECT COUNT(*) FROM matches WHERE winner_id IS NULL OR loser_id IS NULL")
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
        "n_points":  n_points,
        "charted_matches": charted,
        "issues": issues,
        "ok": len(issues) == 0,
    }


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["init", "sync", "validate", "splits"],
                        default="validate")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
        n_atp = load_atp_matches(conn, unmatched)
        conn.close()
        conn = reconnect(db_url)
        n_mcp = load_mcp_matches(conn, unmatched)

        if unmatched:
            Path("data/unmatched_names.log").write_text(
                json.dumps(unmatched, indent=2, default=str)
            )
            logger.warning("%d unmatched records — see data/unmatched_names.log", len(unmatched))

        logger.info("Phase 1 complete — ATP: %d, MCP: %d", n_atp, n_mcp)

    if args.phase == "splits":
        create_splits(conn)

    if args.phase in ("validate", "init"):
        checks = run_quality_checks(conn)
        print(json.dumps(checks, indent=2))
        if checks["ok"]:
            logger.info("Phase 1 complete — all quality checks passed")
            logger.info("Matches: %d  Players: %d  Points: %d",
                        checks["n_matches"], checks["n_players"], checks["n_points"])
        else:
            for issue in checks["issues"]:
                logger.error("  %s", issue)

    conn.close()


if __name__ == "__main__":
    main()
