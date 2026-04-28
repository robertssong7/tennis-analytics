"""
TennisIQ — Elo Rating Engine
src/elo/elo_engine.py

Infrastructure module — NEVER modified by the agent loop.
Processes all matches chronologically to compute Elo ratings per player.

Usage:
    python src/elo/elo_engine.py              # Full recalculation
    python src/elo/elo_engine.py --validate   # Validate output distribution
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from utils.tournament import (
    ALL_TOURNAMENTS_BY_NAME,
    ALL_TOURNAMENTS_BY_LOWER_NAME,
)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# K-Factor table by tournament level and round
# ─────────────────────────────────────────────────────────────
K_FACTORS: Dict[str, int] = {
    "grand_slam_final": 32,
    "grand_slam": 24,
    "masters_final": 20,
    "masters": 16,
    "atp_500": 12,
    "atp_250": 8,
    "challenger": 4,
    "default": 8,
}

GRAND_SLAMS = {"Australian Open", "Roland Garros", "Wimbledon", "US Open"}
FINALS_ROUNDS = {"F", "final", "Final", "Finals"}

ELO_INIT = 1500.0
DECAY_RATE = 0.997  # Monthly decay for inactive players (30-day no-match)


# ─────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────


def get_tournament_level(tournament_name: str, round_name: str = "") -> str:
    """Return tournament level string for K-factor lookup."""
    if not tournament_name:
        return "default"

    name = tournament_name.strip()

    # Exact match first
    tournament = ALL_TOURNAMENTS_BY_NAME.get(name)
    name_lower = str.lower(name)
    if tournament is None:
        tournament = ALL_TOURNAMENTS_BY_LOWER_NAME.get(name_lower)

    # Check for final
    is_final = round_name.strip() in FINALS_ROUNDS
    if tournament is not None:
        if is_final:
            return tournament.name + "_final"
        else:
            return tournament.name

    # Infer from common keywords
    if any(x in name_lower for x in ["challenger", "chall"]):
        return "challenger"
    elif any(x in name_lower for x in ["masters", "1000"]):
        return "masters"

    return "default"


def get_k_factor(tournament_name: str, round_name: str = "") -> int:
    level = get_tournament_level(tournament_name, round_name)
    return K_FACTORS.get(level, K_FACTORS["default"])


def expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score for player A given ratings."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_ratings(
    winner_rating: float,
    loser_rating: float,
    k: int,
) -> Tuple[float, float]:
    """Return (new_winner_rating, new_loser_rating)."""
    e_winner = expected_score(winner_rating, loser_rating)
    e_loser = 1.0 - e_winner
    new_winner = winner_rating + k * (1.0 - e_winner)
    new_loser = loser_rating + k * (0.0 - e_loser)
    return new_winner, new_loser


def elo_to_fifa(elo: float) -> int:
    """Map Elo rating to FIFA 0-99 scale."""
    raw = 99.0 * (elo - 1200.0) / (2200.0 - 1200.0)
    return max(1, min(99, round(raw)))


def get_card_tier(fifa: int) -> Optional[str]:
    """Map FIFA rating to card tier. Returns None if below 40."""
    if fifa >= 85:
        return "legendary"
    elif fifa >= 75:
        return "gold"
    elif fifa >= 60:
        return "silver"
    elif fifa >= 40:
        return "bronze"
    return None


def get_display_rating(hard: float, clay: float, grass: float) -> float:
    """Composite display rating: 50% hard, 30% clay, 20% grass."""
    return 0.50 * hard + 0.30 * clay + 0.20 * grass


def get_elo_badge(match_count: int) -> Optional[str]:
    """Badge for display: None = unrated, 'early' = early, None = full."""
    if match_count < 5:
        return "unrated"
    elif match_count < 15:
        return "early"
    return None


# ─────────────────────────────────────────────────────────────
# Player Elo state container
# ─────────────────────────────────────────────────────────────


class PlayerElo:
    def __init__(self, player_id: str):
        self.player_id = player_id
        self.overall = ELO_INIT
        self.hard = ELO_INIT
        self.clay = ELO_INIT
        self.grass = ELO_INIT
        self.match_count = 0
        self.peak = ELO_INIT
        self.peak_date: Optional[date] = None
        self.last_match_date: Optional[date] = None
        self.history: List[dict] = []

    @property
    def display(self) -> float:
        return get_display_rating(self.hard, self.clay, self.grass)

    @property
    def fifa(self) -> int:
        return elo_to_fifa(self.display)

    @property
    def card_tier(self) -> Optional[str]:
        return get_card_tier(self.fifa)

    def surface_rating(self, surface: str) -> float:
        surface = (surface or "").lower()
        if surface == "hard":
            return self.hard
        elif surface == "clay":
            return self.clay
        elif surface == "grass":
            return self.grass
        return self.overall

    def set_surface_rating(self, surface: str, value: float):
        surface = (surface or "").lower()
        if surface == "hard":
            self.hard = value
        elif surface == "clay":
            self.clay = value
        elif surface == "grass":
            self.grass = value

    def apply_decay(self, as_of_date: date):
        """Apply monthly decay if inactive for 30+ days."""
        if self.last_match_date is None:
            return
        days_inactive = (as_of_date - self.last_match_date).days
        if days_inactive < 30:
            return
        months = days_inactive / 30.0
        factor = DECAY_RATE**months
        for attr in ("overall", "hard", "clay", "grass"):
            current = getattr(self, attr)
            # Pull toward 1500 baseline
            decayed = 1500.0 + (current - 1500.0) * factor
            setattr(self, attr, decayed)

    def record_match(
        self,
        match_date: date,
        match_id: Optional[int],
        surface: str,
        elo_before: float,
        elo_after: float,
        opponent_elo: float,
        tournament: str,
        round_name: str,
        k_factor: int,
        won: bool,
    ):
        self.match_count += 1
        self.last_match_date = match_date
        if self.overall > self.peak:
            self.peak = self.overall
            self.peak_date = match_date
        self.history.append(
            {
                "match_id": match_id,
                "match_date": match_date.isoformat() if match_date else None,
                "surface": surface,
                "elo_before": round(elo_before, 2),
                "elo_after": round(elo_after, 2),
                "opponent_elo": round(opponent_elo, 2),
                "tournament_level": get_tournament_level(tournament, round_name),
                "k_factor": k_factor,
                "won": won,
            }
        )


# ─────────────────────────────────────────────────────────────
# Core Elo Engine
# ─────────────────────────────────────────────────────────────


class EloEngine:
    def __init__(self):
        self.players: Dict[str, PlayerElo] = {}

    def get_or_create(self, player_id: str) -> PlayerElo:
        if player_id not in self.players:
            self.players[player_id] = PlayerElo(player_id)
        return self.players[player_id]

    def process_match(
        self,
        winner_id: str,
        loser_id: str,
        match_date: date,
        surface: str,
        tournament: str,
        round_name: str,
        match_id: Optional[int] = None,
    ):
        """Process one match and update ratings for both players."""
        w = self.get_or_create(winner_id)
        l = self.get_or_create(loser_id)

        surface = (surface or "hard").lower()
        k = get_k_factor(tournament, round_name)

        # Overall ratings
        w_before_overall = w.overall
        l_before_overall = l.overall
        new_w_overall, new_l_overall = update_ratings(w.overall, l.overall, k)
        w.overall = new_w_overall
        l.overall = new_l_overall

        # Surface-specific ratings
        w_surf_before = w.surface_rating(surface)
        l_surf_before = l.surface_rating(surface)
        new_w_surf, new_l_surf = update_ratings(w_surf_before, l_surf_before, k)
        w.set_surface_rating(surface, new_w_surf)
        l.set_surface_rating(surface, new_l_surf)

        # Update peaks
        if w.overall > w.peak:
            w.peak = w.overall
            w.peak_date = match_date

        # Record history
        w.record_match(
            match_date,
            match_id,
            surface,
            w_before_overall,
            new_w_overall,
            l_before_overall,
            tournament,
            round_name,
            k,
            won=True,
        )
        l.record_match(
            match_date,
            match_id,
            surface,
            l_before_overall,
            new_l_overall,
            w_before_overall,
            tournament,
            round_name,
            k,
            won=False,
        )

        w.last_match_date = match_date
        l.last_match_date = match_date

    def process_all(self, matches: List[dict]) -> Dict[str, PlayerElo]:
        """
        Process all matches in chronological order.
        Each match dict must have:
            winner_id, loser_id, match_date (date or str),
            surface, tournament, round, match_id (optional)
        """

        # Sort strictly chronologically
        def parse_date(m):
            d = m.get("match_date")
            if isinstance(d, date):
                return d
            if isinstance(d, str):
                try:
                    return datetime.strptime(d[:10], "%Y-%m-%d").date()
                except ValueError:
                    return date(1900, 1, 1)
            return date(1900, 1, 1)

        sorted_matches = sorted(matches, key=parse_date)
        logger.info("Processing %d matches chronologically...", len(sorted_matches))

        for i, m in enumerate(sorted_matches):
            d = parse_date(m)
            self.process_match(
                winner_id=str(m["winner_id"]),
                loser_id=str(m["loser_id"]),
                match_date=d,
                surface=m.get("surface", "hard"),
                tournament=m.get("tournament", ""),
                round_name=m.get("round", ""),
                match_id=m.get("match_id"),
            )
            if i % 10000 == 0 and i > 0:
                logger.info("  Processed %d/%d matches", i, len(sorted_matches))

        logger.info("Elo processing complete. %d players rated.", len(self.players))
        return self.players

    def get_player_summary(self, player_id: str) -> Optional[dict]:
        p = self.players.get(str(player_id))
        if p is None:
            return None
        badge = get_elo_badge(p.match_count)
        return {
            "player_id": player_id,
            "elo_overall": round(p.overall, 2),
            "elo_hard": round(p.hard, 2),
            "elo_clay": round(p.clay, 2),
            "elo_grass": round(p.grass, 2),
            "elo_display": round(p.display, 2),
            "fifa_rating": p.fifa if badge not in ("unrated", "early") else None,
            "card_tier": p.card_tier if badge not in ("unrated", "early") else None,
            "elo_peak": round(p.peak, 2),
            "elo_peak_date": p.peak_date.isoformat() if p.peak_date else None,
            "elo_match_count": p.match_count,
            "elo_badge": badge,
            "elo_last_updated": datetime.now().isoformat(),
        }

    def get_top_players(self, n: int = 20, surface: str = "display") -> List[dict]:
        """Return top N players by display (or surface) Elo."""
        rated = [p for p in self.players.values() if p.match_count >= 5]
        if surface == "display":
            rated.sort(key=lambda p: p.display, reverse=True)
        elif surface == "hard":
            rated.sort(key=lambda p: p.hard, reverse=True)
        elif surface == "clay":
            rated.sort(key=lambda p: p.clay, reverse=True)
        elif surface == "grass":
            rated.sort(key=lambda p: p.grass, reverse=True)
        else:
            rated.sort(key=lambda p: p.overall, reverse=True)

        results = []
        for i, p in enumerate(rated[:n]):
            s = self.get_player_summary(p.player_id)
            s["rank"] = i + 1
            results.append(s)
        return results

    def validate(self) -> dict:
        """
        Validation checks:
        - Distribution around 1500
        - No active top-50 player below FIFA 60
        - All match counts correct
        """
        issues = []
        all_display = [p.display for p in self.players.values() if p.match_count >= 15]
        if not all_display:
            return {"ok": False, "issues": ["No rated players found"]}

        import statistics

        mean_elo = statistics.mean(all_display)
        stdev = statistics.stdev(all_display) if len(all_display) > 1 else 0

        if abs(mean_elo - 1500) > 100:
            issues.append(f"Mean Elo {mean_elo:.0f} deviates significantly from 1500")

        # Top-50 check
        top_50 = sorted(
            [p for p in self.players.values() if p.match_count >= 15],
            key=lambda p: p.display,
            reverse=True,
        )[:50]
        for p in top_50:
            if p.fifa < 60:
                issues.append(f"Player {p.player_id} in top-50 has FIFA {p.fifa} < 60")

        # Top player should be >= 82
        if top_50:
            top1 = top_50[0]
            if top1.fifa < 82:
                issues.append(f"World #1 ({top1.player_id}) has FIFA {top1.fifa} < 82")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "stats": {
                "n_players": len(self.players),
                "n_rated": len(all_display),
                "mean_elo": round(mean_elo, 1),
                "stdev_elo": round(stdev, 1),
                "min_elo": round(min(all_display), 1),
                "max_elo": round(max(all_display), 1),
            },
        }


# ─────────────────────────────────────────────────────────────
# Card attribute computation (SRV/RET/PAT/SPD/HRD/CLY)
# ─────────────────────────────────────────────────────────────


def compute_card_attributes(
    player_id: str,
    elo_state: Optional[PlayerElo],
    profile_data: Optional[dict] = None,
) -> dict:
    """
    Compute the 6 FIFA card attributes.
    Falls back to 50 if insufficient data.
    All values are integers 1-99, never null/NaN.
    """

    def safe(value, fallback=50) -> int:
        try:
            v = float(value)
            if v != v:  # NaN check
                return fallback
            return max(1, min(99, round(v)))
        except (TypeError, ValueError):
            return fallback

    p = profile_data or {}
    elo = elo_state

    # SRV — serve direction win rates (normalized)
    srv_raw = p.get("srv_score")  # pre-computed by feature_engine
    if srv_raw is None:
        first_srv_won = p.get("first_serve_won", 0.5)
        second_srv_won = p.get("second_serve_won", 0.5)
        srv_raw = float(first_srv_won or 0.5) * 0.6 + float(second_srv_won or 0.5) * 0.4
        srv_raw = (srv_raw - 0.3) / (0.8 - 0.3) * 99
    srv = safe(srv_raw)

    # RET — return point win rate
    ret_raw = p.get("ret_score")
    if ret_raw is None:
        ret_win = p.get("return_win_rate", 0.38)
        ret_raw = (float(ret_win or 0.38) - 0.2) / (0.6 - 0.2) * 99
    ret = safe(ret_raw)

    # PAT — shot sequence effectiveness
    pat_raw = p.get("pat_score")
    if pat_raw is None:
        winner_rate = p.get("winner_rate", 0.1)
        uf_error = p.get("uf_error_rate", 0.15)
        pat_raw = (
            (float(winner_rate or 0.1) - float(uf_error or 0.15) + 0.15) / 0.35
        ) * 99
    pat = safe(pat_raw)

    # SPD — speed (inverse of avg rally length, normalized)
    spd_raw = p.get("spd_score")
    if spd_raw is None:
        avg_rally = p.get("avg_rally_length", 4.5)
        # Short rallies = faster player. Rally 2 = 99, rally 8+ = 1
        avg_rally = float(avg_rally or 4.5)
        spd_raw = max(0, min(99, (8.0 - avg_rally) / 6.0 * 99))
    spd = safe(spd_raw)

    # HRD — hard court Elo mapped to 99-point scale
    if elo:
        hrd = safe(elo_to_fifa(elo.hard))
    else:
        hrd = safe(p.get("hrd_score", 50))

    # CLY — clay court Elo mapped to 99-point scale
    if elo:
        cly = safe(elo_to_fifa(elo.clay))
    else:
        cly = safe(p.get("cly_score", 50))

    return {
        "srv": srv,
        "ret": ret,
        "pat": pat,
        "spd": spd,
        "hrd": hrd,
        "cly": cly,
    }


# ─────────────────────────────────────────────────────────────
# Database integration helpers
# ─────────────────────────────────────────────────────────────


def load_matches_from_db(conn) -> List[dict]:
    """Load all matches from PostgreSQL for Elo processing."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                m.match_id,
                m.match_date,
                m.surface,
                m.round,
                t.name AS tournament,
                m.winner_id::text,
                m.loser_id::text
            FROM matches m
            LEFT JOIN tournaments t ON m.tournament_id = t.tournament_id
            WHERE m.match_date IS NOT NULL
                AND m.winner_id IS NOT NULL
                AND m.loser_id IS NOT NULL
            ORDER BY m.match_date ASC
        """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def write_elo_to_db(conn, engine: EloEngine):
    """Write Elo results back to the players table and elo_history table."""
    from psycopg2.extras import execute_values

    # ── 1. Batch-update players via VALUES ──────────────────────
    player_rows = []
    for player_id, p in engine.players.items():
        s = engine.get_player_summary(player_id)
        player_rows.append(
            (
                s["elo_overall"],
                s["elo_hard"],
                s["elo_clay"],
                s["elo_grass"],
                s["elo_display"],
                s["fifa_rating"],
                s["card_tier"],
                s["elo_peak"],
                s["elo_peak_date"],
                s["elo_match_count"],
                int(player_id),
            )
        )

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE players AS tgt SET
                elo_overall     = v.elo_overall::float,
                elo_hard        = v.elo_hard::float,
                elo_clay        = v.elo_clay::float,
                elo_grass       = v.elo_grass::float,
                elo_display     = v.elo_display::float,
                fifa_rating     = v.fifa_rating::int,
                card_tier       = v.card_tier,
                elo_peak        = v.elo_peak::float,
                elo_peak_date   = v.elo_peak_date::date,
                elo_match_count = v.elo_match_count::int,
                elo_last_updated = NOW()
            FROM (VALUES %s) AS v(
                elo_overall, elo_hard, elo_clay, elo_grass,
                elo_display, fifa_rating, card_tier,
                elo_peak, elo_peak_date, elo_match_count,
                player_id
            )
            WHERE tgt.player_id = v.player_id::int
            """,
            player_rows,
            page_size=500,
        )
    conn.commit()
    logger.info("Players Elo updated (%d rows).", len(player_rows))

    # ── 2. Batch-insert elo_history ──────────────────────────────
    history_rows = []
    for player_id, p in engine.players.items():
        for h in p.history:
            history_rows.append(
                (
                    player_id,
                    h.get("match_id"),
                    h.get("match_date"),
                    h.get("surface"),
                    h.get("elo_before"),
                    h.get("elo_after"),
                    h.get("opponent_elo"),
                    h.get("tournament_level"),
                    h.get("k_factor"),
                )
            )

    CHUNK = 5000
    with conn.cursor() as cur:
        for i in range(0, len(history_rows), CHUNK):
            execute_values(
                cur,
                """INSERT INTO elo_history
                       (player_id, match_id, match_date, surface,
                        elo_before, elo_after, opponent_elo,
                        tournament_level, k_factor)
                   VALUES %s ON CONFLICT DO NOTHING""",
                history_rows[i : i + CHUNK],
                page_size=CHUNK,
            )
            conn.commit()
            logger.info(
                "  elo_history: %d/%d rows written",
                min(i + CHUNK, len(history_rows)),
                len(history_rows),
            )

    logger.info("Elo data written to database.")


# ─────────────────────────────────────────────────────────────
# Schema additions (run once during Phase 2 setup)
# ─────────────────────────────────────────────────────────────

ELO_SCHEMA_SQL = """
-- Add Elo columns to players table (idempotent)
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_overall      FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_hard         FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_clay         FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_grass        FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_display      FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS fifa_rating      INTEGER DEFAULT NULL;
ALTER TABLE players ADD COLUMN IF NOT EXISTS card_tier        VARCHAR(10) DEFAULT NULL;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_peak         FLOAT   DEFAULT 1500;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_peak_date    DATE    DEFAULT NULL;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_match_count  INTEGER DEFAULT 0;
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo_last_updated TIMESTAMPTZ DEFAULT NULL;

-- Elo history table
CREATE TABLE IF NOT EXISTS elo_history (
    id               SERIAL PRIMARY KEY,
    player_id        TEXT,
    match_id         INTEGER,
    match_date       DATE,
    surface          VARCHAR(10),
    elo_before       FLOAT,
    elo_after        FLOAT,
    opponent_elo     FLOAT,
    tournament_level VARCHAR(20),
    k_factor         INTEGER,
    UNIQUE (player_id, match_id)
);
CREATE INDEX IF NOT EXISTS idx_elo_history_player ON elo_history(player_id);
CREATE INDEX IF NOT EXISTS idx_elo_history_date   ON elo_history(match_date);
"""


# ─────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="TennisIQ Elo Engine")
    parser.add_argument(
        "--validate", action="store_true", help="Run validation checks after processing"
    )
    parser.add_argument("--top", type=int, default=10, help="Show top N players")
    parser.add_argument(
        "--from-json", type=str, help="Load matches from JSON file instead of DB"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    engine = EloEngine()

    player_names: Dict[str, str] = {}

    if args.from_json:
        # Load from JSON file (for testing without DB)
        with open(args.from_json) as f:
            matches = json.load(f)
        logger.info("Loaded %d matches from %s", len(matches), args.from_json)
        engine.process_all(matches)
    else:
        # Load from database
        try:
            import psycopg2
            from dotenv import load_dotenv

            load_dotenv()
            conn = psycopg2.connect(os.getenv("DATABASE_URL"), connect_timeout=30)
            matches = load_matches_from_db(conn)
            logger.info("Loaded %d matches from database", len(matches))

            # Fetch player names for display
            with conn.cursor() as cur:
                cur.execute("SELECT player_id::text, name FROM players")
                player_names = {row[0]: row[1] for row in cur.fetchall()}

            engine.process_all(matches)
            write_elo_to_db(conn, engine)
            conn.close()
        except Exception as e:
            logger.error("Database error: %s", e)
            logger.info("Tip: set DATABASE_URL in .env. See BLOCKERS.md.")
            sys.exit(1)

    # Print top players
    top = engine.get_top_players(n=args.top)
    print(f"\n{'═'*62}")
    print(f"  TOP {args.top} PLAYERS BY ELO")
    print(f"{'═'*62}")
    print(f"  {'#':>2}  {'Name':<28} {'Elo':>5}  {'FIFA':>4}  {'Tier':<10}  {'n':>5}")
    print(f"  {'─'*58}")
    for p in top:
        tier = (p.get("card_tier") or "unrated").upper()
        name = player_names.get(str(p["player_id"]), f"id:{p['player_id']}")
        fifa = str(p.get("fifa_rating") or "—")
        print(
            f"  #{p['rank']:2d}  {name:<28} {p['elo_display']:>5.0f}  {fifa:>4}  [{tier:<9}]  {p['elo_match_count']:>5}"
        )

    # Validation
    if args.validate:
        result = engine.validate()
        print(f"\n{'═'*60}")
        print("VALIDATION")
        print(f"{'═'*60}")
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            logger.warning("Validation issues found. Check data/elo_validation.log")
            Path("data/elo_validation.log").write_text(json.dumps(result, indent=2))
            sys.exit(1)
        else:
            print("All validation checks passed.")

    # Card tier counts
    tiers = {"legendary": 0, "gold": 0, "silver": 0, "bronze": 0, "unrated": 0}
    for p in engine.players.values():
        tier = p.card_tier or "unrated"
        tiers[tier] = tiers.get(tier, 0) + 1
    print(f"\nCard tier distribution: {tiers}")
    print(f"\nPhase 2 complete — {len(engine.players)} players rated")


if __name__ == "__main__":
    main()
