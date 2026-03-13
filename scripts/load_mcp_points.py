"""
TennisIQ — Load MCP Point-by-Point Data
scripts/load_mcp_points.py

Loads charting-m-points-*.csv into the points table.

Columns in source files:
  match_id, Pt, Set1, Set2, Gm1, Gm2, Pts, Gm#, TbSet,
  Svr, 1st, 2nd, Notes, PtWinner

Usage:
    python scripts/load_mcp_points.py
"""

import csv
import logging
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MCP_DIR = Path("data/sackmann/tennis_MatchChartingProject")
POINT_FILES = [
    MCP_DIR / "charting-m-points-to-2009.csv",
    MCP_DIR / "charting-m-points-2010s.csv",
    MCP_DIR / "charting-m-points-2020s.csv",
]
CHUNK = 5000

SERVE_DIR_MAP = {
    "4": "wide", "5": "body", "6": "T",
    "7": "wide", "8": "body", "9": "T",
}

TERMINAL_OUTCOME = {
    "@": "uf_error", "#": "forced_error",
    "!": "winner",   "*": "error",
}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def parse_serve(seq_1st: str, seq_2nd: str) -> tuple:
    """
    Return (serve_num, serve_dir, rally_sequence, rally_length, outcome).
    serve_num=1 if 2nd is empty (first serve won or lost the rally)
    serve_num=2 if 2nd is non-empty (first serve was a fault)
    """
    if seq_2nd:
        seq = seq_2nd
        serve_num = 2
    elif seq_1st:
        seq = seq_1st
        serve_num = 1
    else:
        return 1, None, None, 0, None

    serve_dir = SERVE_DIR_MAP.get(seq[0]) if seq else None

    # Rally length: count alphabetic shot chars (rough proxy)
    rally_length = sum(1 for c in seq if c.isalpha())

    # Outcome from terminal char
    outcome = None
    for c in reversed(seq):
        if c in TERMINAL_OUTCOME:
            outcome = TERMINAL_OUTCOME[c]
            break
        if c.isalpha():
            break  # last letter with no terminal marker → in_play

    return serve_num, serve_dir, seq, rally_length, outcome


def parse_score_state(pts: str) -> tuple:
    """
    Return (is_break_point, is_set_point, is_match_point).
    pts is "server_score-returner_score" (e.g. "40-30", "0-40", "40-Ad").
    Set/match points require set/match context we don't have here → False.
    """
    if not pts or "-" not in pts:
        return False, False, False

    parts = pts.split("-", 1)
    if len(parts) != 2:
        return False, False, False

    srv, ret = parts[0].strip(), parts[1].strip()

    # Break point: returner one point from winning the game
    bp = (
        (ret == "40" and srv in ("0", "15", "30")) or
        ret == "Ad"  # deuce, returner has advantage
    )

    return bp, False, False


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url, connect_timeout=30)

    # ── Build match lookup: mcp_match_id_str → (db_match_id, winner_id, loser_id)
    logger.info("Building MCP match lookup from DB...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT match_id, winner_id, loser_id, source
            FROM matches
            WHERE source LIKE 'mcp:%'
        """)
        rows = cur.fetchall()

    match_lookup = {}
    for db_mid, winner_id, loser_id, source in rows:
        mcp_id = source[4:]  # strip "mcp:" prefix
        match_lookup[mcp_id] = (db_mid, winner_id, loser_id)

    logger.info("Loaded %d MCP match records", len(match_lookup))

    # ── Check existing points to avoid re-inserting
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM points")
        existing = cur.fetchone()[0]
    if existing > 0:
        logger.info("points table already has %d rows — truncating for clean load", existing)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE points RESTART IDENTITY")
        conn.commit()

    # ── Load each points file
    total_inserted = 0
    total_skipped  = 0

    for fpath in POINT_FILES:
        if not fpath.exists():
            logger.warning("Missing: %s", fpath)
            continue

        logger.info("Loading %s...", fpath.name)
        file_rows = 0
        file_skipped = 0
        batch = []

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mcp_id = row.get("match_id", "").strip()
                info = match_lookup.get(mcp_id)
                if not info:
                    file_skipped += 1
                    continue

                db_match_id, winner_id, loser_id = info

                # Server / returner from Svr field
                svr = row.get("Svr", "").strip()
                if svr == "1":
                    server_id   = winner_id
                    returner_id = loser_id
                elif svr == "2":
                    server_id   = loser_id
                    returner_id = winner_id
                else:
                    file_skipped += 1
                    continue

                # Point winner
                pw = row.get("PtWinner", "").strip()
                if pw == "1":
                    pt_winner_id = winner_id
                elif pw == "2":
                    pt_winner_id = loser_id
                else:
                    file_skipped += 1
                    continue

                seq_1st = row.get("1st", "").strip()
                seq_2nd = row.get("2nd", "").strip()
                serve_num, serve_dir, rally_seq, rally_length, outcome = parse_serve(seq_1st, seq_2nd)

                pts = row.get("Pts", "").strip()
                is_bp, is_sp, is_mp = parse_score_state(pts)

                # Set number: sum of completed sets + 1
                try:
                    set_num = int(row.get("Set1", 0) or 0) + int(row.get("Set2", 0) or 0) + 1
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

                batch.append((
                    db_match_id,
                    set_num,
                    game_num,
                    point_num,
                    server_id,
                    returner_id,
                    serve_num,
                    serve_dir,
                    None,           # serve_depth (not in source)
                    rally_seq,
                    rally_length,
                    outcome,
                    pt_winner_id,
                    pts,            # score_before
                    is_bp,
                    is_sp,
                    is_mp,
                ))

                if len(batch) >= CHUNK:
                    with conn.cursor() as cur:
                        execute_values(cur, """
                            INSERT INTO points
                                (match_id, set_num, game_num, point_num,
                                 server_id, returner_id, serve_num, serve_dir,
                                 serve_depth, rally_sequence, rally_length, outcome,
                                 winner_id, score_before,
                                 is_break_point, is_set_point, is_match_point)
                            VALUES %s
                        """, batch, page_size=CHUNK)
                    conn.commit()
                    file_rows += len(batch)
                    batch = []

        # Flush remainder
        if batch:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO points
                        (match_id, set_num, game_num, point_num,
                         server_id, returner_id, serve_num, serve_dir,
                         serve_depth, rally_sequence, rally_length, outcome,
                         winner_id, score_before,
                         is_break_point, is_set_point, is_match_point)
                    VALUES %s
                """, batch, page_size=CHUNK)
            conn.commit()
            file_rows += len(batch)

        logger.info("  %s: %d inserted, %d skipped", fpath.name, file_rows, file_skipped)
        total_inserted += file_rows
        total_skipped  += file_skipped

    conn.close()
    logger.info("Done — %d points inserted, %d skipped", total_inserted, total_skipped)


if __name__ == "__main__":
    main()
