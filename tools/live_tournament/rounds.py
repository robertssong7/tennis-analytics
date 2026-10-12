"""Compatibility exports for direct scraper execution."""
import sys
from pathlib import Path

# The cron invokes scraper.py by path, so the repository root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.live_tournament.rounds import current_round, normalize_round, round_order

__all__ = ["current_round", "normalize_round", "round_order"]
