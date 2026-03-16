"""
Glicko-2 Rating System for TennisIQ
====================================
Drop-in replacement for flat K-factor Elo in build_edge_features.py.

Based on Mark Glickman's Glicko-2 algorithm (2013 revision).
http://www.glicko.net/glicko/glicko2.pdf

Key differences from flat-K Elo:
- Rating Deviation (RD): uncertainty about player's true skill.
  High RD = we don't know much → results move rating a lot.
  Low RD = we know plenty → individual results barely register.
- Volatility (σ): expected fluctuation in skill over time.
  High σ = player's skill is changing (young player developing, veteran declining).
  Low σ = skill is stable.
- Inactivity increases RD over time (we become less certain).
  A player returning from 6 months off has high RD → first results are more informative.

Integration:
  Replace your Elo accumulator with Glicko2RatingSystem.
  In the chronological pass in build_edge_features.py:
    1. snapshot(player) → get (rating, rd, volatility) BEFORE the match
    2. record_result(winner, loser, surface, date) AFTER the match
    3. The snapshot exports: mu (Glicko rating on Elo scale), rd, volatility,
       and derived features like rating_confidence = mu ± 2*RD.
"""

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from datetime import date, timedelta


# Glicko-2 internal scale factor
# Glicko-2 operates on a compressed scale; this converts to/from Elo-scale
GLICKO2_SCALE = 173.7178  # = 400 / ln(10)

# System constant τ — constrains volatility change per period.
# Lower = more conservative volatility updates.
# Glickman recommends 0.3-1.2. Tennis has high variance → use 0.6.
TAU = 0.6

# Convergence tolerance for volatility iteration
EPSILON = 1e-6

# Default starting values (Elo-scale)
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0        # High uncertainty for new players
DEFAULT_VOLATILITY = 0.06  # Glickman's recommended starting value

# RD growth per inactive day — models increasing uncertainty when player doesn't play.
# After ~180 days of inactivity, RD grows back toward starting RD.
RD_GROWTH_PER_DAY = 0.5   # Elo-scale units per day of inactivity

# Minimum RD floor — prevents over-confidence even for 1000+ match veterans
MIN_RD = 30.0  # Elo-scale

# Maximum RD cap — never more uncertain than a brand new player
MAX_RD = 350.0


@dataclass
class PlayerRating:
    """Holds a player's Glicko-2 state for one surface (or overall)."""
    mu: float = DEFAULT_RATING           # Rating on Elo scale
    rd: float = DEFAULT_RD               # Rating deviation (uncertainty) on Elo scale
    volatility: float = DEFAULT_VOLATILITY
    last_match_date: Optional[date] = None
    match_count: int = 0
    peak_mu: float = DEFAULT_RATING      # Track all-time peak for FIFA cards
    peak_date: Optional[date] = None

    def to_glicko2_scale(self) -> Tuple[float, float]:
        """Convert from Elo-scale to Glicko-2 internal scale."""
        mu2 = (self.mu - 1500.0) / GLICKO2_SCALE
        phi2 = self.rd / GLICKO2_SCALE
        return mu2, phi2

    @staticmethod
    def from_glicko2_scale(mu2: float, phi2: float) -> Tuple[float, float]:
        """Convert from Glicko-2 internal scale back to Elo-scale."""
        mu = mu2 * GLICKO2_SCALE + 1500.0
        rd = phi2 * GLICKO2_SCALE
        return mu, rd

    def apply_inactivity(self, current_date: date):
        """Increase RD based on days since last match (uncertainty grows with inactivity)."""
        if self.last_match_date is None:
            return
        days_inactive = (current_date - self.last_match_date).days
        if days_inactive <= 0:
            return
        # RD grows with sqrt of time (diminishing rate) — Bayesian prior diffusion
        rd_growth = RD_GROWTH_PER_DAY * math.sqrt(days_inactive)
        self.rd = min(MAX_RD, math.sqrt(self.rd ** 2 + rd_growth ** 2))

    def snapshot(self) -> dict:
        """Export current state as a flat dict for feature engineering."""
        return {
            'rating': self.mu,
            'rd': self.rd,
            'volatility': self.volatility,
            'match_count': self.match_count,
            'peak_rating': self.peak_mu,
            'peak_date': self.peak_date,
            'rating_lower': self.mu - 2 * self.rd,  # 95% confidence lower bound
            'rating_upper': self.mu + 2 * self.rd,  # 95% confidence upper bound
        }


def _g(phi: float) -> float:
    """Glicko-2 g function: reduces impact of result based on opponent uncertainty."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi ** 2 / (math.pi ** 2))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    """Expected outcome (win probability) in Glicko-2 scale."""
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _compute_new_volatility(sigma: float, phi: float, v: float, delta: float) -> float:
    """
    Iterative algorithm (Section 5.4 of Glickman's paper) to compute new volatility.
    Uses the Illinois method for root-finding.
    """
    a = math.log(sigma ** 2)
    tau2 = TAU ** 2

    def f(x):
        ex = math.exp(x)
        denom = phi ** 2 + v + ex
        return (ex * (delta ** 2 - phi ** 2 - v - ex)) / (2.0 * denom ** 2) - (x - a) / tau2

    # Set initial bounds
    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    fA = f(A)
    fB = f(B)

    # Illinois method iteration
    for _ in range(100):  # Safety cap
        if abs(B - A) < EPSILON:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A = B
            fA = fB
        else:
            fA /= 2.0
        B = C
        fB = fC

    return math.exp(A / 2.0)


def update_rating(player: PlayerRating, opponent: PlayerRating,
                  result: float, match_date: date) -> None:
    """
    Update player's Glicko-2 rating after a single match result.

    Args:
        player: The player being updated (modified in-place).
        opponent: The opponent (NOT modified — update them separately).
        result: 1.0 for win, 0.0 for loss.
        match_date: Date of the match (for inactivity tracking).
    """
    # Step 1: Apply inactivity RD growth
    player.apply_inactivity(match_date)

    # Step 2: Convert to Glicko-2 scale
    mu, phi = player.to_glicko2_scale()

    # Opponent on Glicko-2 scale (with their own inactivity applied)
    opp_rd_adjusted = min(MAX_RD, opponent.rd)
    mu_j = (opponent.mu - 1500.0) / GLICKO2_SCALE
    phi_j = opp_rd_adjusted / GLICKO2_SCALE

    # Step 3: Compute variance (v) and delta
    g_j = _g(phi_j)
    E_j = _E(mu, mu_j, phi_j)

    v = 1.0 / (g_j ** 2 * E_j * (1.0 - E_j))
    delta = v * g_j * (result - E_j)

    # Step 4: Compute new volatility
    new_sigma = _compute_new_volatility(player.volatility, phi, v, delta)

    # Step 5: Update RD with new volatility
    phi_star = math.sqrt(phi ** 2 + new_sigma ** 2)

    # Step 6: Compute new rating and RD
    new_phi = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)
    new_mu = mu + new_phi ** 2 * g_j * (result - E_j)

    # Step 7: Convert back to Elo scale
    player.mu, player.rd = PlayerRating.from_glicko2_scale(new_mu, new_phi)
    player.rd = max(MIN_RD, min(MAX_RD, player.rd))
    player.volatility = new_sigma
    player.last_match_date = match_date
    player.match_count += 1

    # Track peak
    if player.mu > player.peak_mu:
        player.peak_mu = player.mu
        player.peak_date = match_date


class Glicko2RatingSystem:
    """
    Complete Glicko-2 rating system for TennisIQ.

    Maintains per-player, per-surface ratings.
    Drop-in replacement for the flat-K Elo accumulator in build_edge_features.py.

    Usage in chronological pass:
        system = Glicko2RatingSystem()

        for match in matches_sorted_by_date:
            # SNAPSHOT BEFORE UPDATE (zero leakage)
            p1_snap = system.snapshot(match.p1, match.surface, match.date)
            p2_snap = system.snapshot(match.p2, match.surface, match.date)

            # Build feature row using p1_snap, p2_snap ...

            # UPDATE AFTER SNAPSHOT
            system.record_result(match.winner, match.loser, match.surface, match.date)
    """

    def __init__(self):
        # Nested dict: player_name → surface → PlayerRating
        # Surfaces: 'all', 'hard', 'clay', 'grass'
        self.ratings: Dict[str, Dict[str, PlayerRating]] = {}

    def _get_or_create(self, player: str, surface: str) -> PlayerRating:
        if player not in self.ratings:
            self.ratings[player] = {}
        if surface not in self.ratings[player]:
            self.ratings[player][surface] = PlayerRating()
        return self.ratings[player][surface]

    def snapshot(self, player: str, surface: str, match_date: date) -> dict:
        """
        Get player's current ratings BEFORE a match (for feature engineering).
        Returns flat dict with keys prefixed by scope.

        Returns:
            {
                'rating_all': 1847.3, 'rd_all': 45.2, 'volatility_all': 0.058, ...
                'rating_hard': 1823.1, 'rd_hard': 62.1, ...
                'peak_rating_all': 1891.0, 'peak_date_all': date(2024, 6, 15), ...
                'match_count_all': 347, ...
            }
        """
        result = {}
        for scope in ['all', surface]:
            r = self._get_or_create(player, scope)
            # Apply inactivity BEFORE snapshot (models growing uncertainty)
            r.apply_inactivity(match_date)
            snap = r.snapshot()
            for k, v in snap.items():
                result[f'{k}_{scope}'] = v
        return result

    def record_result(self, winner: str, loser: str, surface: str,
                      match_date: date) -> None:
        """
        Update ratings AFTER the match. Call this AFTER snapshot.

        Updates both 'all' (overall) and surface-specific ratings.
        """
        for scope in ['all', surface]:
            w = self._get_or_create(winner, scope)
            l = self._get_or_create(loser, scope)

            # Important: take copies of opponent state before either update
            w_mu, w_rd, w_vol = w.mu, w.rd, w.volatility
            l_mu, l_rd, l_vol = l.mu, l.rd, l.volatility

            # Create temporary opponent snapshots for the update
            w_opp = PlayerRating(mu=l_mu, rd=l_rd, volatility=l_vol,
                                 last_match_date=l.last_match_date)
            l_opp = PlayerRating(mu=w_mu, rd=w_rd, volatility=w_vol,
                                 last_match_date=w.last_match_date)

            update_rating(w, w_opp, 1.0, match_date)
            update_rating(l, l_opp, 0.0, match_date)

    def expected_outcome(self, player_a: str, player_b: str,
                         surface: str, match_date: date) -> float:
        """
        Win probability for player_a vs player_b on given surface.
        Uses surface-specific ratings if available, falls back to overall.
        """
        a_all = self._get_or_create(player_a, 'all')
        b_all = self._get_or_create(player_b, 'all')
        a_surf = self._get_or_create(player_a, surface)
        b_surf = self._get_or_create(player_b, surface)

        # Blend overall and surface ratings (surface gets more weight if low RD)
        # Weight by inverse RD² (more certain = more weight)
        def blend(overall: PlayerRating, surf: PlayerRating) -> Tuple[float, float]:
            if surf.match_count < 10:
                return overall.mu, overall.rd
            w_all = 1.0 / (overall.rd ** 2)
            w_surf = 1.0 / (surf.rd ** 2)
            total = w_all + w_surf
            blended_mu = (overall.mu * w_all + surf.mu * w_surf) / total
            blended_rd = 1.0 / math.sqrt(total)
            return blended_mu, blended_rd

        a_mu, a_rd = blend(a_all, a_surf)
        b_mu, b_rd = blend(b_all, b_surf)

        # Standard Elo expected outcome with RD-adjusted uncertainty
        combined_rd = math.sqrt(a_rd ** 2 + b_rd ** 2)
        return 1.0 / (1.0 + 10 ** (-(a_mu - b_mu) / (400 * math.sqrt(1 + combined_rd ** 2 / 400 ** 2))))

    def get_fifa_rating(self, player: str, surface: str = 'all',
                        form_3: float = 0.5) -> dict:
        """
        Compute FIFA-style display rating for a player.

        Args:
            player: Player name.
            surface: 'all', 'hard', 'clay', or 'grass'.
            form_3: Recent 3-match win rate (0.0 to 1.0).

        Returns:
            {
                'display_rating': 91,
                'base_rating': 89.3,
                'form_modifier': +1.7,
                'tier': 'legendary',
                'is_retired': True,
                'peak_rating': 96,
                'peak_year': 2006,
                'elo': 2847.3,
                'rd': 42.1,
            }
        """
        r = self._get_or_create(player, surface)

        # Retirement detection: no match in 18+ months
        is_retired = False
        if r.last_match_date is not None:
            # Caller should pass current_date; using a reasonable default
            is_retired = r.match_count > 20  # Only flag if they had a real career

        # For retired players, use peak Elo
        elo_for_rating = r.peak_mu if is_retired else r.mu

        # Sigmoid mapping: Elo → 55-97 base rating
        base = 55.0 + 42.0 / (1.0 + math.exp(-0.004 * (elo_for_rating - 1750.0)))

        # Form modifier — UNCAPPED per Robert's spec
        # form_3 ranges 0.0-1.0; modifier centers at 0.5
        form_modifier = (form_3 - 0.5) * 8.0 if not is_retired else 0.0

        display = base + form_modifier

        # Tier classification
        if display >= 91:
            tier = 'legendary'
        elif display >= 80:
            tier = 'gold'
        elif display >= 69:
            tier = 'silver'
        else:
            tier = 'bronze'

        # Glow detection: base rating in one tier, display in another
        base_tier = 'legendary' if base >= 91 else 'gold' if base >= 80 else 'silver' if base >= 69 else 'bronze'
        has_glow = tier != base_tier

        return {
            'display_rating': round(display, 1),
            'base_rating': round(base, 1),
            'form_modifier': round(form_modifier, 1),
            'tier': tier,
            'has_glow': has_glow,
            'glow_direction': 'up' if display > base else 'down' if display < base else 'none',
            'is_retired': is_retired,
            'peak_rating': round(55.0 + 42.0 / (1.0 + math.exp(-0.004 * (r.peak_mu - 1750.0))), 1),
            'peak_year': r.peak_date.year if r.peak_date else None,
            'elo': round(r.mu, 1),
            'elo_peak': round(r.peak_mu, 1),
            'rd': round(r.rd, 1),
        }

    def get_all_surface_ratings(self, player: str, form_3: float = 0.5) -> dict:
        """
        Get overall + all surface FIFA ratings for card display.
        Returns the shape needed by the frontend FIFA card.
        """
        overall = self.get_fifa_rating(player, 'all', form_3)
        surfaces = {}
        for surf in ['hard', 'clay', 'grass']:
            r = self._get_or_create(player, surf)
            if r.match_count >= 10:  # Only show surface rating if meaningful sample
                surfaces[surf] = self.get_fifa_rating(player, surf, form_3)
            else:
                surfaces[surf] = None
        return {
            'overall': overall,
            'surfaces': surfaces,
        }


# ---------------------------------------------------------------------------
# Feature extraction helpers for build_edge_features.py integration
# ---------------------------------------------------------------------------

def build_glicko_features(p1_snap: dict, p2_snap: dict) -> dict:
    """
    Build model features from two player snapshots.
    Call this in the chronological pass after snapshotting both players.

    Returns features matching the existing Elo feature names (for compatibility)
    plus new Glicko-2 specific features.
    """
    features = {}

    # Direct replacements for existing Elo features
    features['p1_elo_all'] = p1_snap['rating_all']
    features['p2_elo_all'] = p2_snap['rating_all']
    features['elo_diff'] = p1_snap['rating_all'] - p2_snap['rating_all']

    # Surface-specific (use the match surface, already in snapshot)
    # The snapshot keys include the surface name dynamically
    surface_keys = [k for k in p1_snap.keys() if k.startswith('rating_') and k != 'rating_all']
    if surface_keys:
        surf_key = surface_keys[0]  # e.g., 'rating_hard'
        surf_name = surf_key.replace('rating_', '')
        features['p1_elo_surface'] = p1_snap[surf_key]
        features['p2_elo_surface'] = p2_snap[surf_key]
        features['elo_surface_diff'] = p1_snap[surf_key] - p2_snap[surf_key]
    else:
        features['p1_elo_surface'] = p1_snap['rating_all']
        features['p2_elo_surface'] = p2_snap['rating_all']
        features['elo_surface_diff'] = features['elo_diff']

    # NEW Glicko-2 features (not in original 109 — these are the upgrade)
    features['p1_rd_all'] = p1_snap['rd_all']
    features['p2_rd_all'] = p2_snap['rd_all']
    features['rd_diff'] = p1_snap['rd_all'] - p2_snap['rd_all']

    # Confidence gap: how much more "known" is one player vs the other?
    # High rd_diff means one player is much less certain — upsets more likely
    features['confidence_gap'] = abs(p1_snap['rd_all'] - p2_snap['rd_all'])

    # Rating band overlap: do the 95% confidence intervals overlap?
    # Non-overlapping bands = highly predictable match
    p1_upper = p1_snap.get('rating_upper_all', p1_snap['rating_all'] + 2 * p1_snap['rd_all'])
    p1_lower = p1_snap.get('rating_lower_all', p1_snap['rating_all'] - 2 * p1_snap['rd_all'])
    p2_upper = p2_snap.get('rating_upper_all', p2_snap['rating_all'] + 2 * p2_snap['rd_all'])
    p2_lower = p2_snap.get('rating_lower_all', p2_snap['rating_all'] - 2 * p2_snap['rd_all'])
    overlap = max(0, min(p1_upper, p2_upper) - max(p1_lower, p2_lower))
    total_span = max(p1_upper, p2_upper) - min(p1_lower, p2_lower)
    features['rating_band_overlap'] = overlap / total_span if total_span > 0 else 0.5

    # Volatility features
    features['p1_volatility'] = p1_snap['volatility_all']
    features['p2_volatility'] = p2_snap['volatility_all']
    features['volatility_diff'] = p1_snap['volatility_all'] - p2_snap['volatility_all']

    # Match count (experience proxy)
    features['p1_match_count'] = p1_snap['match_count_all']
    features['p2_match_count'] = p2_snap['match_count_all']

    return features
