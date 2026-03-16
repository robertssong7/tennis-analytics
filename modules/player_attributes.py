"""
TennisIQ Player Attribute Calculator
=====================================
Computes 8 FIFA card attributes from rolling player stats.

Attributes:
  1. SERVE       — ace_rate, 1st_serve_pct, 1st_won, 2nd_won, bp_save, serve_entropy
  2. GROUNDSTROKE — aggression_index, pattern_diversity, rally_4+_wr, first_strike_rate
  3. VOLLEY      — net approach wr, volley shot frequency, serve-volley frequency
  4. FOOTWORK    — rally_crossover, long_rally_wr, return_in_play, defensive_shot_success
  5. ENDURANCE   — 3hr+ match wr, deciding_set_wr, set-over-set degradation
  6. DURABILITY  — wr when ACWR high, consecutive_day_wr, wr when acute_load above median
  7. CLUTCH      — pressure_divergence, tiebreak_wr, deciding_set_wr, bp_convert/save diff
  8. MENTAL      — comeback_wr (lost 1st set), wr from break down, momentum maintenance

Each attribute is scored 0-100 using percentile rank against the active player population.
Percentiles are computed from the rolling accumulator, NOT from all-time stats.

Integration:
  Used by the API to populate FIFA card attributes.
  Runs AFTER the chronological pass in build_edge_features.py has finalized accumulators.
  Can also run on player_profiles.parquet for display-only (not model features).
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
from scipy import stats as scipy_stats


# ============================================================================
# Per-player stat accumulator (extends existing rolling accumulators)
# ============================================================================

@dataclass
class PlayerAttributeAccumulator:
    """
    Tracks the raw stats needed to compute all 8 attributes.
    Updated after each match in the chronological pass.
    """
    # Identity
    name: str = ""
    match_count: int = 0

    # --- SERVE raw stats ---
    total_aces: int = 0
    total_dfs: int = 0
    total_1st_serves_in: int = 0
    total_1st_serve_attempts: int = 0
    total_1st_serve_won: int = 0
    total_1st_serve_played: int = 0  # 1st serves that were in play
    total_2nd_serve_won: int = 0
    total_2nd_serve_played: int = 0
    total_bp_faced: int = 0
    total_bp_saved: int = 0
    total_serve_points: int = 0
    # From charted data
    serve_direction_entropy_sum: float = 0.0
    serve_direction_matches: int = 0

    # --- GROUNDSTROKE raw stats ---
    total_winners: int = 0
    total_ue: int = 0
    total_rally_points_4plus: int = 0
    total_rally_wins_4plus: int = 0
    total_first_strike_points: int = 0  # Points won in ≤3 shots after serve
    total_first_strike_won: int = 0
    # From charted data
    aggression_sum: float = 0.0
    pattern_diversity_sum: float = 0.0
    charted_matches: int = 0

    # --- VOLLEY raw stats (from charted data) ---
    total_net_approaches: int = 0
    total_net_approach_won: int = 0
    total_volley_shots: int = 0
    total_shots: int = 0
    serve_volley_attempts: int = 0
    serve_volley_won: int = 0

    # --- FOOTWORK raw stats ---
    # rally_crossover tracked separately (it's a curve, not a sum)
    long_rally_points: int = 0  # rallies 9+ shots
    long_rally_wins: int = 0
    total_return_points: int = 0
    total_returns_in_play: int = 0

    # --- ENDURANCE raw stats ---
    matches_over_3hrs: int = 0
    wins_over_3hrs: int = 0
    deciding_sets_played: int = 0
    deciding_sets_won: int = 0
    # Set-over-set degradation: track per-set win rates
    sets_played_by_number: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0, 5: 0})
    sets_won_by_number: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0, 5: 0})

    # --- DURABILITY raw stats ---
    # These come from fatigue accumulator snapshots at match time
    matches_when_acwr_high: int = 0  # ACWR > 1.3
    wins_when_acwr_high: int = 0
    matches_consecutive_days: int = 0  # played day after previous match
    wins_consecutive_days: int = 0
    matches_high_acute_load: int = 0  # acute_stress_7d above population median
    wins_high_acute_load: int = 0

    # --- CLUTCH raw stats ---
    total_tiebreaks: int = 0
    tiebreaks_won: int = 0
    # Pressure divergence tracked as rolling mean
    pressure_bp_save_sum: float = 0.0
    normal_hold_sum: float = 0.0
    pressure_matches: int = 0
    # BP conversion
    total_bp_opportunities: int = 0  # as returner
    total_bp_converted: int = 0

    # --- MENTAL raw stats ---
    lost_first_set_matches: int = 0
    comeback_wins: int = 0  # won after losing first set
    down_break_situations: int = 0
    won_from_break_down: int = 0
    # Momentum: won 2nd set after winning 1st
    won_first_set_matches: int = 0
    won_second_after_first: int = 0

    def compute_raw_attributes(self) -> Dict[str, float]:
        """
        Compute raw attribute values (0.0-1.0 scale, not yet percentile-ranked).
        Each attribute is a weighted composite of its component stats.
        """
        attrs = {}

        # ---- SERVE ----
        components = []
        if self.total_serve_points > 50:
            ace_rate = self.total_aces / self.total_serve_points
            components.append(('ace_rate', _normalize(ace_rate, 0.02, 0.20), 0.20))

            if self.total_1st_serve_attempts > 50:
                first_pct = self.total_1st_serves_in / self.total_1st_serve_attempts
                components.append(('1st_pct', _normalize(first_pct, 0.50, 0.72), 0.15))

            if self.total_1st_serve_played > 50:
                first_won = self.total_1st_serve_won / self.total_1st_serve_played
                components.append(('1st_won', _normalize(first_won, 0.60, 0.82), 0.25))

            if self.total_2nd_serve_played > 30:
                second_won = self.total_2nd_serve_won / self.total_2nd_serve_played
                components.append(('2nd_won', _normalize(second_won, 0.40, 0.62), 0.20))

            if self.total_bp_faced > 10:
                bp_save = self.total_bp_saved / self.total_bp_faced
                components.append(('bp_save', _normalize(bp_save, 0.50, 0.72), 0.15))

            if self.serve_direction_matches > 3:
                avg_entropy = self.serve_direction_entropy_sum / self.serve_direction_matches
                components.append(('entropy', _normalize(avg_entropy, 0.8, 1.5), 0.05))

        attrs['serve'] = _weighted_mean(components) if components else 0.5

        # ---- GROUNDSTROKE ----
        components = []
        if self.total_serve_points > 100:
            total_pts = self.total_serve_points + self.total_return_points
            if total_pts > 0:
                winner_rate = self.total_winners / total_pts
                components.append(('winner_rate', _normalize(winner_rate, 0.04, 0.16), 0.25))

                ue_rate = self.total_ue / total_pts
                # Lower UE is better — invert
                components.append(('ue_control', 1.0 - _normalize(ue_rate, 0.04, 0.16), 0.20))

        if self.total_rally_points_4plus > 20:
            rally_wr = self.total_rally_wins_4plus / self.total_rally_points_4plus
            components.append(('rally4_wr', _normalize(rally_wr, 0.40, 0.58), 0.20))

        if self.total_first_strike_points > 20:
            fs_rate = self.total_first_strike_won / self.total_first_strike_points
            components.append(('first_strike', _normalize(fs_rate, 0.55, 0.75), 0.15))

        if self.charted_matches > 3:
            avg_agg = self.aggression_sum / self.charted_matches
            components.append(('aggression', _normalize(avg_agg, 0.3, 0.7), 0.10))
            avg_div = self.pattern_diversity_sum / self.charted_matches
            components.append(('diversity', _normalize(avg_div, 0.3, 0.8), 0.10))

        attrs['groundstroke'] = _weighted_mean(components) if components else 0.5

        # ---- VOLLEY ----
        components = []
        if self.total_net_approaches > 15:
            net_wr = self.total_net_approach_won / self.total_net_approaches
            components.append(('net_wr', _normalize(net_wr, 0.55, 0.78), 0.40))

        if self.total_shots > 100:
            volley_freq = self.total_volley_shots / self.total_shots
            components.append(('volley_freq', _normalize(volley_freq, 0.01, 0.12), 0.30))

        if self.serve_volley_attempts > 5:
            sv_wr = self.serve_volley_won / self.serve_volley_attempts
            components.append(('sv_wr', _normalize(sv_wr, 0.50, 0.72), 0.30))

        attrs['volley'] = _weighted_mean(components) if components else 0.5

        # ---- FOOTWORK ----
        components = []
        if self.long_rally_points > 15:
            long_wr = self.long_rally_wins / self.long_rally_points
            components.append(('long_rally_wr', _normalize(long_wr, 0.38, 0.58), 0.35))

        if self.total_return_points > 50:
            rip_rate = self.total_returns_in_play / self.total_return_points
            components.append(('return_in_play', _normalize(rip_rate, 0.40, 0.72), 0.35))

        if self.total_rally_points_4plus > 20:
            rally_wr = self.total_rally_wins_4plus / self.total_rally_points_4plus
            components.append(('sustained_rally', _normalize(rally_wr, 0.40, 0.58), 0.30))

        attrs['footwork'] = _weighted_mean(components) if components else 0.5

        # ---- ENDURANCE ----
        components = []
        if self.matches_over_3hrs > 3:
            endurance_wr = self.wins_over_3hrs / self.matches_over_3hrs
            components.append(('3hr_wr', _normalize(endurance_wr, 0.30, 0.65), 0.35))

        if self.deciding_sets_played > 5:
            dec_wr = self.deciding_sets_won / self.deciding_sets_played
            components.append(('deciding_wr', _normalize(dec_wr, 0.35, 0.62), 0.35))

        # Set degradation: compare set 1 wr vs set 3+ wr
        s1_played = self.sets_played_by_number.get(1, 0)
        s1_won = self.sets_won_by_number.get(1, 0)
        late_played = sum(self.sets_played_by_number.get(i, 0) for i in [3, 4, 5])
        late_won = sum(self.sets_won_by_number.get(i, 0) for i in [3, 4, 5])
        if s1_played > 10 and late_played > 5:
            s1_wr = s1_won / s1_played
            late_wr = late_won / late_played
            # Positive = maintains or improves in late sets
            degradation = late_wr - s1_wr
            components.append(('degradation', _normalize(degradation, -0.15, 0.10), 0.30))

        attrs['endurance'] = _weighted_mean(components) if components else 0.5

        # ---- DURABILITY ----
        components = []
        if self.matches_when_acwr_high > 3:
            acwr_wr = self.wins_when_acwr_high / self.matches_when_acwr_high
            components.append(('acwr_wr', _normalize(acwr_wr, 0.30, 0.60), 0.35))

        if self.matches_consecutive_days > 3:
            consec_wr = self.wins_consecutive_days / self.matches_consecutive_days
            components.append(('consec_wr', _normalize(consec_wr, 0.30, 0.60), 0.35))

        if self.matches_high_acute_load > 5:
            load_wr = self.wins_high_acute_load / self.matches_high_acute_load
            components.append(('load_wr', _normalize(load_wr, 0.35, 0.60), 0.30))

        attrs['durability'] = _weighted_mean(components) if components else 0.5

        # ---- CLUTCH ----
        components = []
        if self.total_tiebreaks > 5:
            tb_wr = self.tiebreaks_won / self.total_tiebreaks
            components.append(('tiebreak_wr', _normalize(tb_wr, 0.35, 0.65), 0.25))

        if self.deciding_sets_played > 5:
            dec_wr = self.deciding_sets_won / self.deciding_sets_played
            components.append(('deciding_wr', _normalize(dec_wr, 0.35, 0.62), 0.20))

        if self.total_bp_faced > 15:
            bp_save = self.total_bp_saved / self.total_bp_faced
            components.append(('bp_save', _normalize(bp_save, 0.50, 0.72), 0.20))

        if self.total_bp_opportunities > 10:
            bp_conv = self.total_bp_converted / self.total_bp_opportunities
            components.append(('bp_convert', _normalize(bp_conv, 0.30, 0.52), 0.15))

        # Pressure divergence: gap between pressure performance and normal
        if self.pressure_matches > 5:
            avg_pressure = self.pressure_bp_save_sum / self.pressure_matches
            avg_normal = self.normal_hold_sum / self.pressure_matches
            divergence = avg_pressure - avg_normal
            # Positive = rises in pressure (Djokovic). Negative = chokes.
            components.append(('pressure_div', _normalize(divergence, -0.08, 0.08), 0.20))

        attrs['clutch'] = _weighted_mean(components) if components else 0.5

        # ---- MENTAL ----
        components = []
        if self.lost_first_set_matches > 5:
            comeback_rate = self.comeback_wins / self.lost_first_set_matches
            components.append(('comeback', _normalize(comeback_rate, 0.20, 0.50), 0.35))

        if self.down_break_situations > 10:
            break_recovery = self.won_from_break_down / self.down_break_situations
            components.append(('break_recovery', _normalize(break_recovery, 0.15, 0.40), 0.30))

        if self.won_first_set_matches > 10:
            momentum = self.won_second_after_first / self.won_first_set_matches
            components.append(('momentum', _normalize(momentum, 0.60, 0.85), 0.35))

        attrs['mental'] = _weighted_mean(components) if components else 0.5

        return attrs


def _normalize(value: float, low: float, high: float) -> float:
    """Normalize a raw stat to 0.0-1.0 range based on observed min/max."""
    if high <= low:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _weighted_mean(components: List[Tuple[str, float, float]]) -> float:
    """Compute weighted mean from (name, value, weight) tuples."""
    if not components:
        return 0.5
    total_weight = sum(w for _, _, w in components)
    if total_weight <= 0:
        return 0.5
    return sum(v * w for _, v, w in components) / total_weight


# ============================================================================
# Percentile ranking against population
# ============================================================================

class AttributeRanker:
    """
    Converts raw 0-1 attribute values to 0-100 percentile-based scores.

    Call fit() with all players' raw attributes to establish the distribution,
    then score() for individual players.

    The percentile approach ensures that:
    - A "50" always means median
    - A "90+" means top 10% of the population
    - Attributes with different raw distributions are comparable on the card
    """

    def __init__(self):
        self.distributions: Dict[str, np.ndarray] = {}
        self.attr_names = [
            'serve', 'groundstroke', 'volley', 'footwork',
            'endurance', 'durability', 'clutch', 'mental'
        ]

    def fit(self, all_player_attrs: Dict[str, Dict[str, float]],
            min_matches: int = 30):
        """
        Build percentile distributions from all players.

        Args:
            all_player_attrs: {player_name: {attr_name: raw_value}}
            min_matches: Minimum matches to include in distribution.
        """
        for attr in self.attr_names:
            values = [
                attrs[attr]
                for attrs in all_player_attrs.values()
                if attr in attrs
            ]
            if values:
                self.distributions[attr] = np.sort(values)
            else:
                self.distributions[attr] = np.array([0.5])

    def score(self, raw_attrs: Dict[str, float]) -> Dict[str, int]:
        """
        Convert raw attributes to 0-100 scores via percentile rank.

        Applies floor/ceiling adjustments:
        - Minimum 30 (no one gets a 5 on anything with enough matches)
        - Maximum 99 (leave room at the top)
        - Rounded to nearest integer
        """
        scores = {}
        for attr in self.attr_names:
            raw = raw_attrs.get(attr, 0.5)
            dist = self.distributions.get(attr, np.array([0.5]))

            # Percentile rank: what fraction of the population is below this value
            pct = np.searchsorted(dist, raw, side='right') / len(dist)

            # Map to 30-99 range
            # 30 = worst plausible score, 99 = all-time great
            score = int(np.clip(30 + pct * 69, 30, 99))
            scores[attr] = score

        return scores

    def score_display(self, raw_attrs: Dict[str, float]) -> Dict[str, dict]:
        """
        Full display info for each attribute — score, tier color, bar fill class.

        Returns:
            {
                'serve': {'score': 88, 'fill': 'fill-high', 'val_class': 'attr-val-high'},
                'groundstroke': {'score': 94, 'fill': 'fill-plat', 'val_class': 'attr-val-plat'},
                ...
            }
        """
        scores = self.score(raw_attrs)
        display = {}
        for attr, score in scores.items():
            if score >= 90:
                fill = 'fill-plat'
                val_class = 'attr-val-plat'
            elif score >= 80:
                fill = 'fill-high'
                val_class = 'attr-val-high'
            else:
                fill = 'fill-tier'  # Caller appends tier suffix
                val_class = 'attr-val-tier'
            display[attr] = {
                'score': score,
                'fill': fill,
                'val_class': val_class,
            }
        return display


# ============================================================================
# Match update helper — call after each match in chronological pass
# ============================================================================

def update_attributes_from_match(
    acc: PlayerAttributeAccumulator,
    match: dict,
    is_winner: bool,
    fatigue_snapshot: Optional[dict] = None,
    charted_data: Optional[dict] = None,
) -> None:
    """
    Update a player's attribute accumulator from a single match result.

    Args:
        acc: The player's accumulator (modified in-place).
        match: Dict with match data. Expected keys vary by data availability:
            Required: 'minutes', 'score', 'best_of', 'surface'
            Box score (214K matches): 'w_ace', 'w_df', 'w_1stIn', 'w_svpt',
                'w_1stWon', 'w_2ndWon', 'w_bpFaced', 'w_bpSaved',
                'l_ace', 'l_df', etc.
            Score-derived: 'n_sets', 'tiebreaks_won_by_winner', etc.
        is_winner: Whether this player won the match.
        fatigue_snapshot: Fatigue accumulator snapshot from BEFORE the match.
            Used for durability calculations.
        charted_data: Per-match charted data if available.
            Keys: 'aggression', 'pattern_diversity', 'serve_entropy',
                  'net_approaches', 'net_approach_won', 'volley_shots',
                  'total_shots', 'rally_lengths', 'rally_outcomes'
    """
    acc.match_count += 1
    prefix = 'w_' if is_winner else 'l_'
    opp_prefix = 'l_' if is_winner else 'w_'

    # ---- SERVE stats from box score ----
    svpt = match.get(f'{prefix}svpt', 0)
    if svpt and not pd.isna(svpt) and svpt > 0:
        acc.total_serve_points += int(svpt)
        acc.total_aces += int(match.get(f'{prefix}ace', 0) or 0)
        acc.total_dfs += int(match.get(f'{prefix}df', 0) or 0)

        first_in = match.get(f'{prefix}1stIn', 0) or 0
        acc.total_1st_serves_in += int(first_in)
        acc.total_1st_serve_attempts += int(svpt)  # Approximate

        first_won = match.get(f'{prefix}1stWon', 0) or 0
        acc.total_1st_serve_won += int(first_won)
        acc.total_1st_serve_played += int(first_in)

        second_won = match.get(f'{prefix}2ndWon', 0) or 0
        second_played = int(svpt) - int(first_in)
        acc.total_2nd_serve_won += int(second_won)
        acc.total_2nd_serve_played += max(0, second_played)

        bp_faced = match.get(f'{prefix}bpFaced', 0) or 0
        bp_saved = match.get(f'{prefix}bpSaved', 0) or 0
        acc.total_bp_faced += int(bp_faced)
        acc.total_bp_saved += int(bp_saved)

    # ---- GROUNDSTROKE stats ----
    # Winners and UE from box score (if available in extended stats)
    w_count = match.get(f'{prefix}winners', 0) or 0
    ue_count = match.get(f'{prefix}ue', 0) or 0
    acc.total_winners += int(w_count)
    acc.total_ue += int(ue_count)

    # Return stats (for footwork)
    opp_svpt = match.get(f'{opp_prefix}svpt', 0) or 0
    acc.total_return_points += int(opp_svpt) if opp_svpt and not pd.isna(opp_svpt) else 0

    # ---- BP conversion (as returner) ----
    opp_bp_faced = match.get(f'{opp_prefix}bpFaced', 0) or 0
    opp_bp_saved = match.get(f'{opp_prefix}bpSaved', 0) or 0
    if opp_bp_faced > 0:
        acc.total_bp_opportunities += int(opp_bp_faced)
        acc.total_bp_converted += int(opp_bp_faced) - int(opp_bp_saved)

    # ---- ENDURANCE: match length ----
    minutes = match.get('minutes', 0) or 0
    if minutes > 180:
        acc.matches_over_3hrs += 1
        if is_winner:
            acc.wins_over_3hrs += 1

    # ---- ENDURANCE: deciding sets ----
    score_str = match.get('score', '')
    best_of = int(match.get('best_of', 3) or 3)
    n_sets = _count_sets(score_str)
    if n_sets == best_of:  # Went the distance
        acc.deciding_sets_played += 1
        if is_winner:
            acc.deciding_sets_won += 1

    # Set-by-set tracking
    set_results = _parse_set_results(score_str, is_winner)
    for set_num, won in set_results:
        if set_num <= 5:
            acc.sets_played_by_number[set_num] = acc.sets_played_by_number.get(set_num, 0) + 1
            if won:
                acc.sets_won_by_number[set_num] = acc.sets_won_by_number.get(set_num, 0) + 1

    # ---- TIEBREAKS ----
    tb_count = _count_tiebreaks(score_str)
    tb_won = _count_tiebreaks_won(score_str, is_winner)
    acc.total_tiebreaks += tb_count
    acc.tiebreaks_won += tb_won

    # ---- MENTAL: comebacks and momentum ----
    first_set_won = len(set_results) > 0 and set_results[0][1]
    first_set_lost = len(set_results) > 0 and not set_results[0][1]

    if first_set_lost:
        acc.lost_first_set_matches += 1
        if is_winner:
            acc.comeback_wins += 1

    if first_set_won:
        acc.won_first_set_matches += 1
        if len(set_results) > 1 and set_results[1][1]:
            acc.won_second_after_first += 1

    # ---- DURABILITY: from fatigue snapshot ----
    if fatigue_snapshot:
        acwr = fatigue_snapshot.get('acwr', 0)
        if acwr > 1.3:
            acc.matches_when_acwr_high += 1
            if is_winner:
                acc.wins_when_acwr_high += 1

        consec = fatigue_snapshot.get('consecutive_match_days', 0)
        if consec > 0:
            acc.matches_consecutive_days += 1
            if is_winner:
                acc.wins_consecutive_days += 1

        acute = fatigue_snapshot.get('acute_stress_7d', 0)
        if acute > 0:  # Caller should pass population median threshold
            acc.matches_high_acute_load += 1
            if is_winner:
                acc.wins_high_acute_load += 1

    # ---- CHARTED DATA enrichment ----
    if charted_data:
        acc.charted_matches += 1

        if 'aggression' in charted_data:
            acc.aggression_sum += charted_data['aggression']
        if 'pattern_diversity' in charted_data:
            acc.pattern_diversity_sum += charted_data['pattern_diversity']
        if 'serve_entropy' in charted_data:
            acc.serve_direction_entropy_sum += charted_data['serve_entropy']
            acc.serve_direction_matches += 1
        if 'net_approaches' in charted_data:
            acc.total_net_approaches += charted_data['net_approaches']
            acc.total_net_approach_won += charted_data.get('net_approach_won', 0)
        if 'volley_shots' in charted_data:
            acc.total_volley_shots += charted_data['volley_shots']
            acc.total_shots += charted_data.get('total_shots', 0)
        if 'serve_volley_attempts' in charted_data:
            acc.serve_volley_attempts += charted_data['serve_volley_attempts']
            acc.serve_volley_won += charted_data.get('serve_volley_won', 0)

        # Rally-length stats
        if 'rally_lengths' in charted_data and 'rally_outcomes' in charted_data:
            for length, won in zip(charted_data['rally_lengths'],
                                   charted_data['rally_outcomes']):
                if length >= 4:
                    acc.total_rally_points_4plus += 1
                    if won:
                        acc.total_rally_wins_4plus += 1
                if length >= 9:
                    acc.long_rally_points += 1
                    if won:
                        acc.long_rally_wins += 1
                if length <= 3:
                    acc.total_first_strike_points += 1
                    if won:
                        acc.total_first_strike_won += 1

            # Returns in play (rally length > 1 on opponent serve)
            returns_total = sum(1 for l in charted_data['rally_lengths'])
            returns_in_play = sum(1 for l in charted_data['rally_lengths'] if l > 1)
            acc.total_returns_in_play += returns_in_play

    # ---- CLUTCH: pressure tracking ----
    if svpt and not pd.isna(svpt) and bp_faced and not pd.isna(bp_faced) and int(bp_faced) > 0:
        # BP save rate for this match
        bp_save_rate = int(bp_saved) / int(bp_faced) if int(bp_faced) > 0 else 0
        # Normal hold rate (non-BP service games approximation)
        normal_pts = int(svpt) - int(bp_faced)
        if normal_pts > 0:
            # Approximate: points won on serve minus BP saves, divided by non-BP points
            normal_won = int(match.get(f'{prefix}1stWon', 0) or 0) + int(match.get(f'{prefix}2ndWon', 0) or 0)
            normal_hold_rate = (normal_won - int(bp_saved)) / normal_pts if normal_pts > 0 else 0
            acc.pressure_bp_save_sum += bp_save_rate
            acc.normal_hold_sum += normal_hold_rate
            acc.pressure_matches += 1


# ============================================================================
# Score parsing helpers
# ============================================================================

def _count_sets(score_str) -> int:
    if not score_str or not isinstance(score_str, str):
        return 0
    return len([s for s in score_str.strip().split()
                if '-' in s and not any(x in s.lower() for x in ['ret', 'w/o', 'def'])])


def _count_tiebreaks(score_str) -> int:
    if not score_str or not isinstance(score_str, str):
        return 0
    return score_str.count('(')


def _count_tiebreaks_won(score_str, is_winner: bool) -> int:
    """Count tiebreaks won. Winner's game count is always listed first in Sackmann data."""
    if not score_str or not isinstance(score_str, str):
        return 0
    won = 0
    for s in score_str.strip().split():
        if '(' not in s:
            continue
        clean = s.split('(')[0]
        parts = clean.split('-')
        if len(parts) == 2:
            try:
                g1, g2 = int(parts[0]), int(parts[1])
                # In Sackmann, winner's score is first
                if is_winner and g1 > g2:
                    won += 1
                elif not is_winner and g2 > g1:
                    won += 1
            except ValueError:
                continue
    return won


def _parse_set_results(score_str, is_winner: bool) -> List[Tuple[int, bool]]:
    """
    Parse score string into list of (set_number, player_won_set).
    Winner's score is always first in Sackmann format.
    """
    results = []
    if not score_str or not isinstance(score_str, str):
        return results

    set_num = 0
    for s in score_str.strip().split():
        if any(x in s.lower() for x in ['ret', 'w/o', 'def']):
            continue
        clean = s.split('(')[0]
        parts = clean.split('-')
        if len(parts) == 2:
            try:
                g1, g2 = int(parts[0]), int(parts[1])
                set_num += 1
                # Winner's score first: g1 > g2 means winner took this set
                winner_took_set = g1 > g2
                player_won = winner_took_set if is_winner else not winner_took_set
                results.append((set_num, player_won))
            except ValueError:
                continue
    return results


# ============================================================================
# API integration helper
# ============================================================================

def get_card_data(
    player_name: str,
    accumulators: Dict[str, PlayerAttributeAccumulator],
    ranker: AttributeRanker,
    glicko_system=None,
    form_3: float = 0.5,
) -> dict:
    """
    Generate complete FIFA card data for a player.

    Returns the shape needed by the frontend component:
    {
        'name': 'Carlos Alcaraz',
        'overall': 93,
        'tier': 'legendary',
        'form_modifier': +3.5,
        'is_retired': False,
        'peak_year': 2024,
        'surfaces': {'hard': 90, 'clay': 94, 'grass': 86},
        'attributes': {
            'serve': 88, 'groundstroke': 94, 'volley': 82, 'footwork': 92,
            'endurance': 85, 'durability': 83, 'clutch': 91, 'mental': 89,
        },
        'attribute_display': {
            'serve': {'score': 88, 'fill': 'fill-high', ...},
            ...
        },
    }
    """
    acc = accumulators.get(player_name)
    if acc is None:
        return {'name': player_name, 'error': 'Player not found'}

    raw = acc.compute_raw_attributes()
    scores = ranker.score(raw)
    display = ranker.score_display(raw)

    result = {
        'name': player_name,
        'attributes': scores,
        'attribute_display': display,
    }

    if glicko_system:
        card = glicko_system.get_all_surface_ratings(player_name, form_3)
        result['overall'] = card['overall']['display_rating']
        result['tier'] = card['overall']['tier']
        result['form_modifier'] = card['overall']['form_modifier']
        result['is_retired'] = card['overall']['is_retired']
        result['peak_year'] = card['overall']['peak_year']
        result['surfaces'] = {
            s: card['surfaces'][s]['display_rating']
            for s in ['hard', 'clay', 'grass']
            if card['surfaces'][s] is not None
        }

    return result
