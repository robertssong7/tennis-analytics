"""
Charted Point-Level Features for TennisIQ
==========================================
Aggregates parsed_points.parquet (7,160 ATP matches, 1.2M points) into
per-match, per-player statistics and maintains rolling accumulators.

Features extracted:
  1. serve_entropy         — H(serve_direction) when serving; high = unpredictable
  2. first_strike_rate     — win rate on rally_length <= 3 (as server)
  3. pressure_divergence   — hold% on break points minus hold% on normal service points
  4. aggression_index      — (winners + forced_errors_caused) / total points won
  5. long_rally_wr         — win rate on rally_length >= 7
  6. rally_win_slope       — correlation(rally_length, win_indicator); + = grinder, - = striker
  7. pattern_diversity     — H(first-3-shot sequence); high = varied patterns
  8. avg_rally_length      — mean rally length (also fed to fatigue grinding_index)

Integration:
  1. Call aggregate_charted_points() once at script startup → match-level lookup dict
  2. Init ChartedAccumulator() alongside other accumulators
  3. In chronological pass: snapshot BEFORE, update AFTER (if match has charted data)
"""

import math
import numpy as np
import pandas as pd
from collections import deque
from typing import Dict, Optional, List

# Break points from server's perspective: returner has game point
BREAK_POINT_PTS = frozenset(['0-40', '15-40', '30-40', '40-AD'])

# Minimum points to compute a stat (below this → NaN → falls back to default)
MIN_SERVE_PTS = 5
MIN_RALLY_PTS = 15
MIN_BP_PTS = 3

# Defaults for players with no charted history
# Based on ATP tour population medians
CHARTED_DEFAULTS = {
    'serve_entropy': 1.05,      # Near-uniform 3-direction distribution
    'first_strike_rate': 0.68,  # Typical server win rate on short points
    'pressure_divergence': 0.0, # No pressure effect
    'aggression_index': 0.55,   # Slightly above 50% of wins are aggressive
    'long_rally_wr': 0.50,      # Coin flip on long rallies
    'rally_win_slope': 0.0,     # Neutral — neither grinder nor striker
    'pattern_diversity': 1.0,   # Moderate diversity
    'avg_rally_length': 4.0,    # ATP average ~4 shots
}


# ============================================================================
# Per-match aggregation
# ============================================================================

def _compute_player_stats(match_pts: pd.DataFrame, player_num: int) -> dict:
    """
    Compute all charted stats for one player in one match.

    Args:
        match_pts: All points in the match (full match DataFrame slice).
        player_num: 1 or 2 (as encoded in Svr / PtWinner columns).

    Returns:
        Dict of stat_name → float, or NaN if insufficient data.
    """
    n = len(match_pts)
    if n == 0:
        return {}

    serve_pts = match_pts[match_pts['Svr'] == player_num]
    n_serve = len(serve_pts)
    player_wins_mask = match_pts['PtWinner'] == player_num

    # ---- 1. Serve direction entropy ----
    if n_serve >= MIN_SERVE_PTS:
        dir_valid = serve_pts['serve_direction'].dropna()
        if len(dir_valid) >= MIN_SERVE_PTS:
            dir_counts = dir_valid.value_counts(normalize=True)
            # Shannon entropy (nats, capped at ln(3) ≈ 1.099)
            entropy = float(-np.sum(dir_counts.values * np.log(dir_counts.values + 1e-10)))
        else:
            entropy = float('nan')
    else:
        entropy = float('nan')

    # ---- 2. First strike rate (rally_length <= 3 when serving) ----
    if n_serve >= MIN_SERVE_PTS:
        fs_pts = serve_pts[serve_pts['rally_length'] <= 3]
        if len(fs_pts) >= 3:
            first_strike_rate = float((fs_pts['PtWinner'] == player_num).mean())
        else:
            first_strike_rate = float('nan')
    else:
        first_strike_rate = float('nan')

    # ---- 3. Pressure divergence (break points when serving) ----
    if n_serve >= MIN_SERVE_PTS * 2:
        bp_mask = serve_pts['Pts'].isin(BREAK_POINT_PTS)
        bp_pts = serve_pts[bp_mask]
        normal_pts = serve_pts[~bp_mask]
        if len(bp_pts) >= MIN_BP_PTS and len(normal_pts) >= MIN_BP_PTS:
            bp_hold = float((bp_pts['PtWinner'] == player_num).mean())
            normal_hold = float((normal_pts['PtWinner'] == player_num).mean())
            pressure_divergence = bp_hold - normal_hold
        else:
            pressure_divergence = float('nan')
    else:
        pressure_divergence = float('nan')

    # ---- 4. Aggression index ----
    # (winners + forced errors the player CAUSED) / total_points_won
    total_wins = player_wins_mask.sum()
    if total_wins >= 5:
        aggressive_wins = (
            player_wins_mask &
            match_pts['point_outcome'].isin(['winner', 'forced_error'])
        ).sum()
        aggression_index = float(aggressive_wins / total_wins)
    else:
        aggression_index = float('nan')

    # ---- 5. Long rally win rate (rally_length >= 7) ----
    if n >= MIN_RALLY_PTS:
        long_pts = match_pts[match_pts['rally_length'] >= 7]
        if len(long_pts) >= 5:
            long_rally_wr = float((long_pts['PtWinner'] == player_num).mean())
        else:
            long_rally_wr = float('nan')
    else:
        long_rally_wr = float('nan')

    # ---- 6. Rally win slope (correlation with rally length) ----
    if n >= MIN_RALLY_PTS:
        win_ind = player_wins_mask.values.astype(float)
        lengths = match_pts['rally_length'].values.astype(float)
        # Pearson correlation — positive = wins more as rally lengthens (grinder)
        cov = np.cov(lengths, win_ind)
        if cov.shape == (2, 2) and cov[0, 0] > 0 and cov[1, 1] > 0:
            slope = float(cov[0, 1] / (math.sqrt(cov[0, 0]) * math.sqrt(cov[1, 1])))
            if math.isnan(slope):
                slope = 0.0
        else:
            slope = 0.0
    else:
        slope = float('nan')

    # ---- 7. Pattern diversity (entropy of first-3-char shot sequence) ----
    if 'shot_sequence' in match_pts.columns and n >= 10:
        seq_first3 = match_pts['shot_sequence'].str[:3].dropna()
        if len(seq_first3) >= 10:
            seq_counts = seq_first3.value_counts(normalize=True)
            pattern_diversity = float(
                -np.sum(seq_counts.values * np.log(seq_counts.values + 1e-10))
            )
        else:
            pattern_diversity = float('nan')
    else:
        pattern_diversity = float('nan')

    # ---- 8. Average rally length ----
    avg_rally = float(match_pts['rally_length'].mean())

    return {
        'serve_entropy': entropy,
        'first_strike_rate': first_strike_rate,
        'pressure_divergence': pressure_divergence,
        'aggression_index': aggression_index,
        'long_rally_wr': long_rally_wr,
        'rally_win_slope': slope,
        'pattern_diversity': pattern_diversity,
        'avg_rally_length': avg_rally,
        'n_pts': n,
        'n_serve_pts': n_serve,
    }


def aggregate_charted_points(points_df: pd.DataFrame) -> Dict:
    """
    Aggregate point-level charted data to match-level per-player stats.
    Run once at script startup — ~5-10 seconds for 1.2M points.

    Returns:
        lookup dict: (date_str, frozenset({player1_norm, player2_norm}))
                     → {'p1': stats_dict, 'p2': stats_dict,
                        'p1_name_norm': str, 'p2_name_norm': str}

    The frozenset key allows matching regardless of winner/loser order.
    """
    def _norm(name: str) -> str:
        return name.replace('_', ' ').strip().lower()

    print("Aggregating charted points data...")
    t0 = pd.Timestamp.now()

    lookup = {}
    n_matched = 0

    for match_id, group in points_df.groupby('match_id', sort=False):
        # Parse match_id: YYYYMMDD-M-Tournament-Round-Player1-Player2
        parts = match_id.split('-')
        if len(parts) < 5:
            continue

        date_str = parts[0]

        # Player names are last two underscore-delimited fields
        # But tournament and round names may also have underscores — parse from end
        # Format: {date}-M-{tourney...}-{round}-{Player1}-{Player2}
        # Player 1 and 2 are always the last two '-' separated tokens
        # However player names with '_' are already joined by '_'
        # The match_id splits on '-', but player names use '_'
        # So we need the 'Player 1' and 'Player 2' columns from the data
        p1_raw = group['Player 1'].iloc[0]
        p2_raw = group['Player 2'].iloc[0]
        p1_norm = _norm(p1_raw)
        p2_norm = _norm(p2_raw)

        key = (date_str, frozenset([p1_norm, p2_norm]))

        if key in lookup:
            continue  # Skip duplicates (shouldn't happen but be safe)

        match_pts = group.reset_index(drop=True)
        stats_p1 = _compute_player_stats(match_pts, 1)
        stats_p2 = _compute_player_stats(match_pts, 2)

        lookup[key] = {
            p1_norm: stats_p1,
            p2_norm: stats_p2,
        }
        n_matched += 1

    elapsed = (pd.Timestamp.now() - t0).total_seconds()
    print(f"  Aggregated {n_matched:,} charted matches in {elapsed:.1f}s")
    return lookup


def get_charted_for_match(
    lookup: Dict,
    match_date,  # date object or anything with strftime
    player_a: str,
    player_b: str,
) -> Optional[Dict]:
    """
    Look up charted stats for a specific match.

    Returns:
        dict: {player_a_norm: stats_dict, player_b_norm: stats_dict}
        or None if match not in charted data.
    """
    def _norm(name: str) -> str:
        return str(name).replace('_', ' ').strip().lower()

    if hasattr(match_date, 'strftime'):
        date_str = match_date.strftime('%Y%m%d')
    else:
        date_str = str(match_date).replace('-', '')[:8]

    a_norm = _norm(player_a)
    b_norm = _norm(player_b)
    key = (date_str, frozenset([a_norm, b_norm]))

    return lookup.get(key)


# ============================================================================
# Rolling charted accumulator
# ============================================================================

CHARTED_FEATURES = list(CHARTED_DEFAULTS.keys())


class ChartedAccumulator:
    """
    Per-player rolling accumulator for charted match statistics.

    Usage in chronological pass:
        charted_acc = ChartedAccumulator()

        # BEFORE match:
        w_snap = charted_acc.snapshot(winner)
        l_snap = charted_acc.snapshot(loser)

        # Build features from snapshots ...

        # AFTER match (only if charted data available):
        if charted_match_data:
            charted_acc.update(winner, charted_match_data[winner_norm])
            charted_acc.update(loser,  charted_match_data[loser_norm])
    """

    def __init__(self, window: int = 30):
        # player_name → deque of stat dicts (most recent last)
        self.history: Dict[str, deque] = {}
        self.window = window

    def update(self, player: str, stats: dict) -> None:
        """Record charted stats from a completed match."""
        if stats is None:
            return
        if player not in self.history:
            self.history[player] = deque(maxlen=self.window)
        self.history[player].append(stats)

    def snapshot(self, player: str) -> dict:
        """
        Get rolling charted features BEFORE the next match.
        Returns defaults for players with no charted history.
        """
        h = list(self.history.get(player, []))
        result = {'charted_match_count': len(h)}

        for feat in CHARTED_FEATURES:
            vals = [
                m[feat] for m in h
                if feat in m
                and m[feat] is not None
                and not (isinstance(m[feat], float) and math.isnan(m[feat]))
            ]
            if vals:
                result[f'charted_{feat}'] = float(np.mean(vals))
            else:
                result[f'charted_{feat}'] = CHARTED_DEFAULTS[feat]

        return result


# ============================================================================
# Feature builder (called in chronological pass)
# ============================================================================

def build_charted_features(p1_snap: dict, p2_snap: dict) -> dict:
    """
    Build model features from two charted snapshots.
    Produces per-player features + differential features.
    """
    features = {}

    for prefix, snap in [('p1', p1_snap), ('p2', p2_snap)]:
        for key, val in snap.items():
            features[f'{prefix}_{key}'] = val

    # Differential features — direction matters for prediction
    diff_keys = [
        'charted_serve_entropy',
        'charted_first_strike_rate',
        'charted_pressure_divergence',
        'charted_aggression_index',
        'charted_long_rally_wr',
        'charted_rally_win_slope',
        'charted_pattern_diversity',
        'charted_avg_rally_length',
    ]
    for key in diff_keys:
        features[f'{key}_diff'] = (
            p1_snap.get(key, CHARTED_DEFAULTS.get(key.replace('charted_', ''), 0.0)) -
            p2_snap.get(key, CHARTED_DEFAULTS.get(key.replace('charted_', ''), 0.0))
        )

    # Composite: style clash — high when one is striker (low slope) vs grinder (high slope)
    p1_slope = p1_snap.get('charted_rally_win_slope', 0.0)
    p2_slope = p2_snap.get('charted_rally_win_slope', 0.0)
    features['style_clash'] = abs(p1_slope - p2_slope)

    return features
