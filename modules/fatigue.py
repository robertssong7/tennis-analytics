"""
Rally-Adjusted Physical Load (Fatigue) Module for TennisIQ
===========================================================
Replaces naive match-count fatigue with a physiologically-informed model.

Three components:
1. Rally density proxy → grinding_index per opponent
   - Defensive players (de Minaur) have high grinding index
   - Serve bots (Opelka) have low grinding index
   - Derived from box score stats when charted data unavailable

2. Match stress = minutes × grinding_index × competitiveness
   - Competitiveness derived from set scores (tiebreaks = high, bagels = low)
   - Single number capturing "how hard was this on the body"

3. Accumulation with decay → acute/chronic load ratio
   - Tim Gabbett's ACWR framework from rugby/AFL
   - When acute load spikes vs chronic baseline → performance drops
   - Consecutive match days without rest (Slam grind detection)

Integration:
  Drop into build_edge_features.py chronological pass.
  Accumulator tracks per-player match history with timestamps + stress scores.
  Snapshot exports fatigue features BEFORE each match.
"""

import math
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from datetime import date, timedelta
from collections import deque


@dataclass
class MatchLoad:
    """Record of a single match's physical load."""
    date: date
    minutes: float
    stress: float          # minutes × grinding_index × competitiveness
    opponent_rank: int      # for opponent-quality weighting
    grinding_index: float   # opponent's defensive profile
    competitiveness: float  # how close was the match (from score)
    surface: str


@dataclass
class OpponentProfile:
    """Rolling profile of an opponent's playing style for grinding_index."""
    # Accumulated stats (rolling, from box score data)
    total_points_played: float = 0.0
    total_games_played: float = 0.0
    total_winners: float = 0.0
    total_ue: float = 0.0
    total_1st_serve_return_in_play: float = 0.0
    total_return_points: float = 0.0
    match_count: int = 0

    # From charted data (if available)
    avg_rally_length: float = 0.0
    charted_matches: int = 0

    def grinding_index(self) -> float:
        """
        Compute how physically demanding this opponent is.

        High grinding index (0.7+): defensive players — lots of balls in play,
        few winners, few UE, long rallies. Think de Minaur, Schwartzman, Murray.

        Low grinding index (0.2-0.4): serve-dominant players — short points,
        high ace rate, high winner count. Think Opelka, Isner, Karlovic.

        Returns 0.5 (neutral) if insufficient data.
        """
        if self.match_count < 5:
            return 0.5  # Not enough data, assume neutral

        components = []

        # Component 1: Points per game (more deuce games = more grinding)
        # Typical range: 4.5 (quick games) to 7.0 (lots of deuces)
        if self.total_games_played > 0:
            ppg = self.total_points_played / self.total_games_played
            ppg_score = np.clip((ppg - 4.5) / 2.5, 0, 1)  # Normalize to 0-1
            components.append(ppg_score)

        # Component 2: Low winner rate = more grinding
        # Winners per point: aggressive player ~0.15+, defensive ~0.06
        if self.total_points_played > 0:
            wpp = self.total_winners / self.total_points_played
            winner_score = 1.0 - np.clip((wpp - 0.04) / 0.12, 0, 1)  # Inverse
            components.append(winner_score)

        # Component 3: Low UE rate = more balls come back
        # UE per point: error-prone ~0.15+, clean ~0.06
        if self.total_points_played > 0:
            uepp = self.total_ue / self.total_points_played
            ue_score = 1.0 - np.clip((uepp - 0.04) / 0.12, 0, 1)  # Inverse
            components.append(ue_score)

        # Component 4: High return-in-play rate = more rallies started
        if self.total_return_points > 0:
            rip = self.total_1st_serve_return_in_play / self.total_return_points
            rip_score = np.clip((rip - 0.3) / 0.4, 0, 1)
            components.append(rip_score)

        # Component 5: Average rally length (from charted data if available)
        if self.charted_matches >= 3:
            # Typical range: 2.5 (serve-heavy) to 5.5 (grinding)
            rally_score = np.clip((self.avg_rally_length - 2.5) / 3.0, 0, 1)
            components.append(rally_score * 1.5)  # Weight charted data higher

        if not components:
            return 0.5

        return float(np.clip(np.mean(components), 0.1, 1.0))


def compute_competitiveness(score_str: str, best_of: int = 3) -> float:
    """
    Derive match competitiveness from score string.

    High competitiveness (0.8-1.0): tiebreaks, 5th set, all close sets.
    Low competitiveness (0.1-0.3): straight-set bagels/breadsticks.
    Moderate (0.4-0.7): mixed — some close sets, some blowouts.

    Args:
        score_str: Score like "6-4 3-6 7-6(5)" or "6-1 6-2".
        best_of: 3 or 5.

    Returns:
        Float 0.0-1.0.
    """
    if not score_str or not isinstance(score_str, str):
        return 0.5  # Default if no score available

    try:
        sets = score_str.strip().split()
        if not sets:
            return 0.5

        set_scores = []
        for s in sets:
            # Handle retirements, walkovers
            if any(x in s.lower() for x in ['ret', 'w/o', 'def', 'wal']):
                return 0.3  # Retirements are low-stress

            # Parse "6-4" or "7-6(5)"
            clean = s.split('(')[0]  # Remove tiebreak score
            parts = clean.split('-')
            if len(parts) == 2:
                try:
                    g1, g2 = int(parts[0]), int(parts[1])
                    set_scores.append((g1, g2))
                except ValueError:
                    continue

        if not set_scores:
            return 0.5

        # Compute per-set closeness
        set_closeness_values = []
        for g1, g2 in set_scores:
            total = g1 + g2
            if total == 0:
                continue
            margin = abs(g1 - g2)

            # Tiebreaks are the most competitive
            is_tiebreak = (g1 == 7 and g2 == 6) or (g1 == 6 and g2 == 7)
            if is_tiebreak:
                set_closeness_values.append(1.0)
            elif margin <= 1:  # 7-5, 6-5 (if exists)
                set_closeness_values.append(0.85)
            elif margin == 2:  # 6-4
                set_closeness_values.append(0.65)
            elif margin == 3:  # 6-3
                set_closeness_values.append(0.45)
            elif margin == 4:  # 6-2
                set_closeness_values.append(0.25)
            else:  # 6-1, 6-0
                set_closeness_values.append(0.1)

        if not set_closeness_values:
            return 0.5

        # More sets = more competitive (went the distance)
        sets_played = len(set_closeness_values)
        distance_bonus = 0.0
        if best_of == 3 and sets_played == 3:
            distance_bonus = 0.15
        elif best_of == 5 and sets_played >= 4:
            distance_bonus = 0.1 * (sets_played - 3)  # 0.1 for 4 sets, 0.2 for 5

        base = float(np.mean(set_closeness_values))
        return float(np.clip(base + distance_bonus, 0.0, 1.0))

    except Exception:
        return 0.5


class FatigueAccumulator:
    """
    Per-player fatigue accumulator for the chronological pass.

    Tracks match history with stress scores and exports fatigue features.
    Uses Tim Gabbett's Acute:Chronic Workload Ratio (ACWR) framework.

    Usage:
        acc = FatigueAccumulator()

        # In chronological pass:
        features = acc.snapshot(player, match_date)  # BEFORE match
        acc.record_match(player, match_load)          # AFTER match
    """

    def __init__(self):
        # player_name → list of MatchLoad (sorted by date, recent last)
        self.history: Dict[str, List[MatchLoad]] = {}
        # player_name → OpponentProfile (their own playing style)
        self.player_profiles: Dict[str, OpponentProfile] = {}

    def get_or_create_profile(self, player: str) -> OpponentProfile:
        if player not in self.player_profiles:
            self.player_profiles[player] = OpponentProfile()
        return self.player_profiles[player]

    def update_opponent_profile(self, player: str, match_stats: dict) -> None:
        """
        Update a player's grinding profile from box score stats.
        Call for both players after each match.

        match_stats should contain keys like:
            'total_points', 'total_games', 'winners', 'ue',
            '1st_serve_return_in_play', 'return_points',
            'avg_rally_length' (if charted)
        """
        p = self.get_or_create_profile(player)
        p.total_points_played += match_stats.get('total_points', 0)
        p.total_games_played += match_stats.get('total_games', 0)
        p.total_winners += match_stats.get('winners', 0)
        p.total_ue += match_stats.get('ue', 0)
        p.total_1st_serve_return_in_play += match_stats.get('1st_serve_return_in_play', 0)
        p.total_return_points += match_stats.get('return_points', 0)
        p.match_count += 1

        if 'avg_rally_length' in match_stats and match_stats['avg_rally_length'] > 0:
            # Running average of rally length from charted data
            n = p.charted_matches
            p.avg_rally_length = (p.avg_rally_length * n + match_stats['avg_rally_length']) / (n + 1)
            p.charted_matches += 1

    def record_match(self, player: str, match_date: date, minutes: float,
                     opponent: str, opponent_rank: int, score_str: str,
                     best_of: int, surface: str) -> None:
        """Record a completed match's physical load."""
        if player not in self.history:
            self.history[player] = []

        # Get opponent's grinding index
        opp_profile = self.get_or_create_profile(opponent)
        grinding = opp_profile.grinding_index()

        # Compute competitiveness from score
        competitiveness = compute_competitiveness(score_str, best_of)

        # Handle missing minutes
        if minutes is None or math.isnan(minutes) or minutes <= 0:
            # Estimate from best_of and competitiveness
            base_mins = 90 if best_of == 3 else 150
            minutes = base_mins * (0.5 + competitiveness)

        # Match stress = minutes × grinding_index × competitiveness
        # This is the core metric: a 3-hour grind against de Minaur is
        # fundamentally more taxing than a 3-hour match against Opelka
        stress = minutes * grinding * competitiveness

        # Opponent rank weighting (existing compound fatigue concept)
        # Playing top-50 is more stressful than playing qualifiers
        rank_weight = 1.0 + max(0, (100 - opponent_rank)) / 100.0
        stress *= rank_weight

        load = MatchLoad(
            date=match_date,
            minutes=minutes,
            stress=stress,
            opponent_rank=opponent_rank,
            grinding_index=grinding,
            competitiveness=competitiveness,
            surface=surface,
        )

        self.history[player].append(load)

    def snapshot(self, player: str, match_date: date) -> dict:
        """
        Export fatigue features BEFORE a match.

        Returns dict of features ready for the training matrix.
        """
        features = {}
        history = self.history.get(player, [])

        if not history:
            return self._default_features()

        # --- Acute load (last 7 days) ---
        acute_window = timedelta(days=7)
        acute_matches = [m for m in history
                         if 0 < (match_date - m.date).days <= 7]

        features['acute_stress_7d'] = sum(m.stress for m in acute_matches)
        features['acute_minutes_7d'] = sum(m.minutes for m in acute_matches)
        features['acute_match_count_7d'] = len(acute_matches)

        # --- Chronic load (last 28 days) ---
        chronic_matches = [m for m in history
                           if 0 < (match_date - m.date).days <= 28]

        features['chronic_stress_28d'] = sum(m.stress for m in chronic_matches)
        features['chronic_minutes_28d'] = sum(m.minutes for m in chronic_matches)
        features['chronic_match_count_28d'] = len(chronic_matches)

        # --- ACWR: Acute-to-Chronic Workload Ratio ---
        # Gabbett's "sweet spot": ACWR 0.8-1.3 is optimal
        # Below 0.8 = undertrained (cold start). Above 1.5 = overloaded.
        # This is THE key injury/performance predictor in sports science.
        weekly_chronic = features['chronic_stress_28d'] / 4.0  # Weekly average
        if weekly_chronic > 0:
            features['acwr'] = features['acute_stress_7d'] / weekly_chronic
        else:
            features['acwr'] = 0.0  # No chronic baseline

        # --- Medium-term load (14 days) ---
        mid_matches = [m for m in history
                       if 0 < (match_date - m.date).days <= 14]
        features['stress_14d'] = sum(m.stress for m in mid_matches)
        features['minutes_14d'] = sum(m.minutes for m in mid_matches)

        # --- Days since last match ---
        recent = [m for m in history if m.date < match_date]
        if recent:
            last_match = max(recent, key=lambda m: m.date)
            features['days_rest'] = (match_date - last_match.date).days
            features['last_match_minutes'] = last_match.minutes
            features['last_match_stress'] = last_match.stress
            features['last_opponent_grinding'] = last_match.grinding_index
            features['last_match_competitiveness'] = last_match.competitiveness
        else:
            features['days_rest'] = 30  # Assume well-rested if no history
            features['last_match_minutes'] = 0
            features['last_match_stress'] = 0
            features['last_opponent_grinding'] = 0.5
            features['last_match_competitiveness'] = 0.5

        # --- Consecutive match days (Slam grind detector) ---
        # Count backwards from match_date: how many consecutive days had a match?
        consecutive = 0
        check_date = match_date - timedelta(days=1)
        match_dates = {m.date for m in history}
        while check_date in match_dates:
            consecutive += 1
            check_date -= timedelta(days=1)
            if consecutive > 14:  # Safety cap
                break
        features['consecutive_match_days'] = consecutive

        # --- Consecutive day stress (not just count — accumulated stress) ---
        consec_stress = 0.0
        check_date = match_date - timedelta(days=1)
        for _ in range(consecutive):
            day_matches = [m for m in history if m.date == check_date]
            consec_stress += sum(m.stress for m in day_matches)
            check_date -= timedelta(days=1)
        features['consecutive_day_stress'] = consec_stress

        # --- 30-day tournament load (matches in rolling month) ---
        month_matches = [m for m in history
                         if 0 < (match_date - m.date).days <= 30]
        features['tournament_load_30d'] = len(month_matches)

        # --- Average grinding index of recent opponents ---
        recent_5 = sorted(
            [m for m in history if m.date < match_date],
            key=lambda m: m.date, reverse=True
        )[:5]
        if recent_5:
            features['avg_recent_grinding'] = np.mean([m.grinding_index for m in recent_5])
            features['avg_recent_competitiveness'] = np.mean([m.competitiveness for m in recent_5])
        else:
            features['avg_recent_grinding'] = 0.5
            features['avg_recent_competitiveness'] = 0.5

        # --- Surface-specific recent load ---
        # Playing 5 clay matches in a row vs mixing surfaces matters
        if recent_5:
            surface_counts = {}
            for m in recent_5:
                surface_counts[m.surface] = surface_counts.get(m.surface, 0) + 1
            features['surface_consistency_recent'] = max(surface_counts.values()) / len(recent_5)
        else:
            features['surface_consistency_recent'] = 0.5

        return features

    def _default_features(self) -> dict:
        """Default fatigue features for players with no match history."""
        return {
            'acute_stress_7d': 0.0,
            'acute_minutes_7d': 0.0,
            'acute_match_count_7d': 0,
            'chronic_stress_28d': 0.0,
            'chronic_minutes_28d': 0.0,
            'chronic_match_count_28d': 0,
            'acwr': 0.0,
            'stress_14d': 0.0,
            'minutes_14d': 0.0,
            'days_rest': 30,
            'last_match_minutes': 0,
            'last_match_stress': 0,
            'last_opponent_grinding': 0.5,
            'last_match_competitiveness': 0.5,
            'consecutive_match_days': 0,
            'consecutive_day_stress': 0.0,
            'tournament_load_30d': 0,
            'avg_recent_grinding': 0.5,
            'avg_recent_competitiveness': 0.5,
            'surface_consistency_recent': 0.5,
        }


def build_fatigue_features(p1_snap: dict, p2_snap: dict) -> dict:
    """
    Build model features from two player fatigue snapshots.
    Creates both per-player features and differential features.
    """
    features = {}

    # Per-player features
    for prefix, snap in [('p1', p1_snap), ('p2', p2_snap)]:
        for key, val in snap.items():
            features[f'{prefix}_{key}'] = val

    # Differential features (where p1-p2 differences matter)
    diff_keys = [
        'acute_stress_7d', 'chronic_stress_28d', 'acwr',
        'days_rest', 'consecutive_match_days', 'minutes_14d',
        'last_match_stress', 'tournament_load_30d',
    ]
    for key in diff_keys:
        features[f'{key}_diff'] = p1_snap.get(key, 0) - p2_snap.get(key, 0)

    # Composite: fatigue asymmetry
    # When one player is much more fatigued, the fresher player has an edge
    p1_load = p1_snap.get('acute_stress_7d', 0)
    p2_load = p2_snap.get('acute_stress_7d', 0)
    total = p1_load + p2_load
    if total > 0:
        features['fatigue_asymmetry'] = (p1_load - p2_load) / total
    else:
        features['fatigue_asymmetry'] = 0.0

    return features
