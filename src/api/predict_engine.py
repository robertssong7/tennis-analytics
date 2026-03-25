"""
TennisIQ Prediction Engine
===========================
Loads all ML models and player data at startup (singleton).
Provides fuzzy player lookup and feature vector construction for inference.

Feature construction follows training_edge_v4.pkl (152 features):
  - Rating features from glicko2_state.pkl (Glicko-2 ratings)
  - Serve/return stats from player_attributes_v2.pkl accumulators
  - Rolling form from recent Sackmann CSV matches (2023-2024)
  - Weather: neutral defaults (no match-specific data at inference)
  - Fatigue: neutral defaults

CPU-only: torch.set_num_threads(1) called before any PyTorch ops (if ever used).
"""

import math
import pickle
import difflib
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent.parent  # project root
ENSEMBLE_DIR = BASE / 'models' / 'ensemble'
DATA_DIR     = BASE / 'data' / 'processed'
SACKMANN_DIR = BASE / 'data' / 'sackmann' / 'tennis_atp'

# ── Surface code mapping ─────────────────────────────────────────────────────
SURFACE_CODE = {'hard': 0, 'clay': 1, 'grass': 2}
SURFACE_CPI  = {'hard': 36.0, 'clay': 24.5, 'grass': 37.0}

# Retirement threshold: 18 months (548 days)
RETIREMENT_THRESHOLD_DAYS = 548

# Will be set dynamically after Glicko data loads (max last_match_date across all players).
# Fallback to Dec 2024 if Glicko hasn't loaded yet.
LATEST_DATA_DATE = date(2024, 12, 31)

SUPPLEMENTAL_CSV = DATA_DIR / 'supplemental_matches_2025_2026.csv'


def _build_supplemental_name_map(glicko_names: list) -> dict:
    """
    Build mapping from tennis-data.co.uk format ("Sinner J.") to canonical
    Sackmann/Glicko names ("Jannik Sinner") using last name + first initial.

    Returns dict: { "Sinner J." : "Jannik Sinner", ... }
    """
    # Build a lookup: (last_name_lower, first_initial_lower) -> list of canonical names
    canonical_by_key = {}
    for name in glicko_names:
        parts = name.split()
        if len(parts) < 2:
            continue
        first_name = parts[0]
        last_name = ' '.join(parts[1:])
        key = (last_name.lower(), first_name[0].lower())
        if key not in canonical_by_key:
            canonical_by_key[key] = []
        canonical_by_key[key].append(name)

    suppl_csv = SUPPLEMENTAL_CSV
    if not suppl_csv.exists():
        return {}

    df = pd.read_csv(suppl_csv, usecols=['winner_name', 'loser_name'])
    all_suppl_names = sorted(
        set(df['winner_name'].dropna().unique()) | set(df['loser_name'].dropna().unique())
    )

    name_map = {}
    for sname in all_suppl_names:
        sname = str(sname).strip()
        if not sname or sname == 'nan':
            continue
        # Format: "LastName F." or "Hyphen-Last F." or "Two Word F."
        parts = sname.rsplit(' ', 1)
        if len(parts) != 2:
            continue
        last_part = parts[0].strip()
        initial_part = parts[1].strip().rstrip('.')
        if not initial_part:
            continue
        key = (last_part.lower(), initial_part[0].lower())
        candidates = canonical_by_key.get(key, [])
        if len(candidates) == 1:
            name_map[sname] = candidates[0]
        elif len(candidates) > 1:
            # Multiple matches (e.g. "Zverev A." could be Alexander or Mischa)
            # Pick the one with best Glicko rating (most relevant)
            name_map[sname] = candidates[0]  # they're sorted by insertion order
    return name_map


# ── Singleton ────────────────────────────────────────────────────────────────

class PredictEngine:
    """
    Singleton ML inference engine. Call load() once at app startup.
    All predict() calls are then pure in-memory lookups.
    """

    _instance: Optional['PredictEngine'] = None

    def __init__(self):
        self.xgb_model = None
        self.lgb_model = None
        self.ensemble  = None          # dict from stacked_ensemble.pkl
        self.glicko    = None          # Glicko2RatingSystem
        self.attributes = None         # dict: player_name → PlayerAttributeAccumulator
        self.player_form: dict = {}    # player_name → {form_3, form_5, form_15, form_50, surface_form_hard/clay/grass, win_rate_vs_top50}
        self.player_names: list = []   # sorted list for fuzzy matching
        self.feature_cols: list = []   # 168 feature column names (in order)
        self.win_loss: dict = {}       # player_name → {"wins": int, "losses": int}
        self.h2h: dict = {}            # (nameA, nameB) sorted → H2H stats
        self.player_ages: dict = {}    # player_name → latest known age (float)
        self.attribute_averages: dict = {}  # attribute_name → float average (excluding defaults)
        self.attribute_proxies: dict = {}  # player_name → {footwork: int, volley: int}
        self._loaded = False

    @classmethod
    def get(cls) -> 'PredictEngine':
        if cls._instance is None:
            cls._instance = PredictEngine()
        return cls._instance

    # ── Loading ────────────────────────────────────────────────────────────

    def load(self):
        """Load all models and player data. Call once at startup."""
        if self._loaded:
            return

        logger.info("PredictEngine: loading models ...")

        # Feature column order from training_edge_v4.pkl
        self.feature_cols = self._get_feature_cols()

        # ML models
        with open(ENSEMBLE_DIR / 'xgb_model.pkl', 'rb') as f:
            self.xgb_model = pickle.load(f)
        logger.info("  ✓ XGBoost loaded")

        with open(ENSEMBLE_DIR / 'lgb_model.pkl', 'rb') as f:
            self.lgb_model = pickle.load(f)
        logger.info("  ✓ LightGBM loaded")

        # Load stacked ensemble — prefer JSON (stable) over pickle (fragile)
        stacked_json = ENSEMBLE_DIR / 'stacked_meta.json'
        if stacked_json.exists():
            import json as _json
            from sklearn.linear_model import LogisticRegression
            with open(stacked_json) as f:
                meta = _json.load(f)
            stacker = LogisticRegression()
            stacker.coef_ = np.array(meta['coef'])
            stacker.intercept_ = np.array(meta['intercept'])
            stacker.classes_ = np.array([0, 1])
            self.ensemble = {
                'type': 'stacked',
                'stacker': stacker,
                'model_names': meta.get('model_names', ['xgboost', 'lightgbm'])
            }
            logger.info(f"  ✓ Stacked ensemble loaded from JSON (coef shape: {stacker.coef_.shape})")
        else:
            with open(ENSEMBLE_DIR / 'stacked_ensemble.pkl', 'rb') as f:
                self.ensemble = pickle.load(f)
            logger.info(f"  ✓ Stacked ensemble loaded from pickle (legacy fallback)")

        # Player state
        with open(DATA_DIR / 'glicko2_state.pkl', 'rb') as f:
            self.glicko = pickle.load(f)
        logger.info(f"  ✓ Glicko-2 loaded ({len(self.glicko.ratings):,} players)")

        # Compute latest data date from max(last_match_date) across all players
        global LATEST_DATA_DATE
        max_date = None
        for _pname, surfaces in self.glicko.ratings.items():
            r = surfaces.get('all')
            if r and r.last_match_date and (max_date is None or r.last_match_date > max_date):
                max_date = r.last_match_date
        if max_date:
            LATEST_DATA_DATE = max_date
            self.latest_data_date = max_date
            logger.info(f"  ✓ Latest data date: {LATEST_DATA_DATE} (from max last_match_date)")
        else:
            self.latest_data_date = LATEST_DATA_DATE
            logger.info(f"  ✓ Latest data date: {LATEST_DATA_DATE} (fallback)")

        # Check supplemental data for a later date
        if SUPPLEMENTAL_CSV.exists():
            try:
                sup_df = pd.read_csv(SUPPLEMENTAL_CSV, usecols=['tourney_date'])
                sup_max = sup_df['tourney_date'].dropna().astype(int).max()
                sup_date = pd.to_datetime(str(sup_max), format='%Y%m%d').date()
                if sup_date > LATEST_DATA_DATE:
                    LATEST_DATA_DATE = sup_date
                    self.latest_data_date = sup_date
                    logger.info(f"  ✓ Latest data date updated to {LATEST_DATA_DATE} (from supplemental CSV)")
            except Exception as e:
                logger.warning(f"  Could not parse supplemental dates: {e}")

        with open(DATA_DIR / 'player_attributes_v2.pkl', 'rb') as f:
            self.attributes = pickle.load(f)
        logger.info(f"  ✓ Player attributes loaded ({len(self.attributes):,} players)")

        # Build player name list from glicko2 (has all players)
        self.player_names = sorted(self.glicko.ratings.keys())

        # Build rolling form cache from recent Sackmann matches
        logger.info("  Building player form cache from recent matches ...")
        self._build_form_cache()
        logger.info(f"  ✓ Form cache built ({len(self.player_form):,} players)")

        # Build win/loss, H2H, and age caches from all Sackmann matches
        logger.info("  Building win/loss, H2H, and age caches ...")
        self._build_match_caches()
        logger.info(f"  ✓ Win/loss cache built ({len(self.win_loss):,} players)")
        logger.info(f"  ✓ H2H cache built ({len(self.h2h):,} pairs)")
        logger.info(f"  ✓ Age cache built ({len(self.player_ages):,} players)")

        # Compute ATP-wide attribute averages (excluding defaults)
        logger.info("  Computing ATP attribute averages ...")
        self._compute_attribute_averages()
        logger.info(f"  ✓ Attribute averages computed ({len(self.attribute_averages)} attributes)")

        # Compute footwork/volley proxies from charted data
        logger.info("  Computing footwork/volley proxies from charted data ...")
        self._compute_attribute_proxies()
        logger.info(f"  ✓ Attribute proxies computed ({len(self.attribute_proxies)} players)")

        self._loaded = True
        logger.info("PredictEngine: ready.")

    def _compute_attribute_averages(self):
        """Compute ATP-wide averages for each attribute, excluding default values."""
        attr_names = ('serve', 'groundstroke', 'volley', 'footwork',
                      'endurance', 'durability', 'clutch', 'mental')
        # Collect mapped values per attribute
        buckets = {a: [] for a in attr_names}

        for player_name, acc in self.attributes.items():
            try:
                raw = acc.compute_raw_attributes()
                for attr_name in attr_names:
                    mapped = int(np.clip(30 + raw[attr_name] * 69, 30, 99))
                    # Exclude values <= 35 (defaults: footwork=30, volley=64 is kept)
                    if mapped > 35:
                        buckets[attr_name].append(mapped)
            except Exception:
                continue

        self.attribute_averages = {}
        for attr_name in attr_names:
            vals = buckets[attr_name]
            if vals:
                self.attribute_averages[attr_name] = round(float(np.mean(vals)), 1)
            else:
                self.attribute_averages[attr_name] = None

    def _compute_attribute_proxies(self):
        """Compute footwork and volley proxies from charted match data."""
        PARQUET = BASE / 'data' / 'processed' / 'parsed_points.parquet'
        if not PARQUET.exists():
            logger.warning("  parsed_points.parquet not found — skipping attribute proxies")
            return

        pts = pd.read_parquet(PARQUET)
        self.attribute_proxies = {}

        all_names = sorted(set(pts["Player 1"].unique()) | set(pts["Player 2"].unique()))
        processed = 0

        for player_name in all_names:
            mask = (pts["Player 1"] == player_name) | (pts["Player 2"] == player_name)
            pp = pts[mask]
            if len(pp) < 200:  # Need substantial data
                continue

            # FOOTWORK PROXY: win rate in long rallies (7+ shots) — measures movement/coverage
            footwork = None
            if "rally_length" in pp.columns:
                long_rallies = pp[pp["rally_length"] >= 7]
                if len(long_rallies) >= 50:
                    long_won = (
                        ((long_rallies["PtWinner"] == 1) & (long_rallies["Player 1"] == player_name)) |
                        ((long_rallies["PtWinner"] == 2) & (long_rallies["Player 2"] == player_name))
                    )
                    long_wr = float(long_won.mean())
                    # Normalize: 0.30 -> 30, 0.70 -> 99
                    footwork = int(min(99, max(30, 30 + (long_wr - 0.30) / 0.40 * 69)))

            # VOLLEY PROXY: win rate on net points (last_shot_type contains 'volley')
            volley = None
            if "last_shot_type" in pp.columns:
                volley_types = ['fh_volley', 'bh_volley', 'fh_half_volley']
                net_pts = pp[pp["last_shot_type"].isin(volley_types)]
                if len(net_pts) >= 30:
                    net_won = (
                        ((net_pts["PtWinner"] == 1) & (net_pts["Player 1"] == player_name)) |
                        ((net_pts["PtWinner"] == 2) & (net_pts["Player 2"] == player_name))
                    )
                    net_wr = float(net_won.mean())
                    volley = int(min(99, max(30, 30 + (net_wr - 0.30) / 0.40 * 69)))

            if footwork is not None or volley is not None:
                # Match charted name to Glicko canonical name
                canonical = self.find_player(player_name)
                if canonical:
                    self.attribute_proxies[canonical] = {}
                    if footwork is not None:
                        self.attribute_proxies[canonical]['footwork'] = footwork
                    if volley is not None:
                        self.attribute_proxies[canonical]['volley'] = volley
                    processed += 1

        logger.info(f"    Processed {processed} players with attribute proxies")

    def _get_feature_cols(self) -> list:
        """Return the 168 feature columns in the exact training order."""
        return [
            'surface_code', 'tourney_level_code', 'best_of', 'ball_type',
            'match_temp', 'match_humidity', 'match_wind', 'match_precip',
            'is_indoor', 'is_extreme_heat', 'is_high_wind', 'is_high_humidity',
            'roof_likely_closed', 'imputed_cpi', 'cpi', 'rank_diff',
            'p1_elo_all', 'p2_elo_all', 'elo_diff',
            'p1_elo_surface', 'p2_elo_surface', 'elo_surface_diff',
            'p1_rd_all', 'p2_rd_all', 'rd_diff', 'confidence_gap', 'rating_band_overlap',
            'p1_volatility', 'p2_volatility', 'volatility_diff',
            'p1_match_count', 'p2_match_count',
            'p1_acute_stress_7d', 'p1_acute_minutes_7d', 'p1_acute_match_count_7d',
            'p1_chronic_stress_28d', 'p1_chronic_minutes_28d', 'p1_chronic_match_count_28d',
            'p1_acwr', 'p1_stress_14d', 'p1_minutes_14d', 'p1_days_rest',
            'p1_last_match_minutes', 'p1_last_match_stress',
            'p1_last_opponent_grinding', 'p1_last_match_competitiveness',
            'p1_consecutive_match_days', 'p1_consecutive_day_stress',
            'p1_tournament_load_30d', 'p1_avg_recent_grinding',
            'p1_avg_recent_competitiveness', 'p1_surface_consistency_recent',
            'p2_acute_stress_7d', 'p2_acute_minutes_7d', 'p2_acute_match_count_7d',
            'p2_chronic_stress_28d', 'p2_chronic_minutes_28d', 'p2_chronic_match_count_28d',
            'p2_acwr', 'p2_stress_14d', 'p2_minutes_14d', 'p2_days_rest',
            'p2_last_match_minutes', 'p2_last_match_stress',
            'p2_last_opponent_grinding', 'p2_last_match_competitiveness',
            'p2_consecutive_match_days', 'p2_consecutive_day_stress',
            'p2_tournament_load_30d', 'p2_avg_recent_grinding',
            'p2_avg_recent_competitiveness', 'p2_surface_consistency_recent',
            'acute_stress_7d_diff', 'chronic_stress_28d_diff', 'acwr_diff',
            'days_rest_diff', 'consecutive_match_days_diff', 'minutes_14d_diff',
            'last_match_stress_diff', 'tournament_load_30d_diff', 'fatigue_asymmetry',
            'p1_charted_match_count', 'p1_charted_serve_entropy',
            'p1_charted_first_strike_rate', 'p1_charted_pressure_divergence',
            'p1_charted_aggression_index', 'p1_charted_long_rally_wr',
            'p1_charted_rally_win_slope', 'p1_charted_pattern_diversity',
            'p1_charted_avg_rally_length',
            'p2_charted_match_count', 'p2_charted_serve_entropy',
            'p2_charted_first_strike_rate', 'p2_charted_pressure_divergence',
            'p2_charted_aggression_index', 'p2_charted_long_rally_wr',
            'p2_charted_rally_win_slope', 'p2_charted_pattern_diversity',
            'p2_charted_avg_rally_length',
            'charted_serve_entropy_diff', 'charted_first_strike_rate_diff',
            'charted_pressure_divergence_diff', 'charted_aggression_index_diff',
            'charted_long_rally_wr_diff', 'charted_rally_win_slope_diff',
            'charted_pattern_diversity_diff', 'charted_avg_rally_length_diff',
            'style_clash',
            'p1_form_3', 'p1_form_5', 'p1_form_15', 'p1_form_50',
            'p1_surface_form', 'p1_h2h_pct',
            'p1_ace_rate', 'p1_df_rate', 'p1_first_serve_pct',
            'p1_first_serve_won_pct', 'p1_second_serve_won_pct',
            'p1_bp_save_pct', 'p1_win_rate_vs_top50',
            'p2_form_3', 'p2_form_5', 'p2_form_15', 'p2_form_50',
            'p2_surface_form', 'p2_h2h_pct',
            'p2_ace_rate', 'p2_df_rate', 'p2_first_serve_pct',
            'p2_first_serve_won_pct', 'p2_second_serve_won_pct',
            'p2_bp_save_pct', 'p2_win_rate_vs_top50',
            'form_3_diff', 'form_5_diff', 'form_15_diff', 'form_50_diff',
            'surface_form_diff', 'h2h_pct_diff',
            'ace_rate_diff', 'df_rate_diff', 'first_serve_pct_diff',
            'first_serve_won_pct_diff', 'second_serve_won_pct_diff',
            'bp_save_pct_diff', 'win_rate_vs_top50_diff',
            'heat_x_fatigue', 'heat_x_fatigue_diff', 'wind_x_serve_dep',
            'humidity_x_rally', 'heat_x_endurance_diff',
            # H2H features
            'h2h_wins_p1', 'h2h_wins_p2', 'h2h_total', 'h2h_has_history',
            'h2h_win_rate_p1', 'h2h_surface_wins_p1', 'h2h_surface_total',
            'h2h_surface_win_rate_p1', 'h2h_recency_p1', 'h2h_streak_p1',
            # Age & career
            'p1_age', 'p2_age', 'age_diff',
            'p1_career_matches', 'p2_career_matches', 'career_match_diff',
        ]

    def _build_form_cache(self):
        """
        Compute rolling form from Sackmann CSVs (2022–2024).
        For each player: form_3, form_5, form_15, form_50, and per-surface form.
        Also computes win_rate_vs_top50 (vs players with Glicko-2 > 1900).
        """
        # Load recent matches (last 3 years)
        dfs = []
        for year in [2022, 2023, 2024]:
            csv_path = SACKMANN_DIR / f'atp_matches_{year}.csv'
            if csv_path.exists():
                df = pd.read_csv(csv_path, usecols=[
                    'tourney_date', 'surface', 'winner_name', 'loser_name',
                    'winner_rank', 'loser_rank',
                ])
                df['date'] = pd.to_datetime(df['tourney_date'].astype(str), format='%Y%m%d', errors='coerce')
                dfs.append(df)

        if not dfs:
            logger.warning("No recent CSV files found for form cache")
            return

        matches = pd.concat(dfs, ignore_index=True)
        matches = matches.dropna(subset=['date']).sort_values('date').reset_index(drop=True)

        # Build ordered match history per player
        # Each match: (date, won, surface, opponent)
        player_history: dict = {}

        for _, row in matches.iterrows():
            w, l = row['winner_name'], row['loser_name']
            surf = str(row.get('surface', 'Hard')).lower()
            if surf not in ('hard', 'clay', 'grass'):
                surf = 'hard'
            d = row['date']

            for pname, won, opp in [(w, True, l), (l, False, w)]:
                if pname not in player_history:
                    player_history[pname] = []
                player_history[pname].append((d, won, surf, opp))

        # Get top players (Glicko mu > 1900 = rough top-50 proxy)
        top_players = set()
        if self.glicko:
            for name, surfaces in self.glicko.ratings.items():
                r = surfaces.get('all')
                if r and r.mu > 1900 and r.match_count > 30:
                    top_players.add(name)

        # Compute rolling win rates
        for player, history in player_history.items():
            if len(history) < 3:
                continue
            history.sort(key=lambda x: x[0])
            won_flags   = [h[1] for h in history]
            surf_flags  = [h[2] for h in history]
            opp_names   = [h[3] for h in history]

            n = len(won_flags)

            def win_rate(flags, k):
                recent = flags[max(0, n-k):]
                return sum(recent) / len(recent) if recent else 0.5

            form = {
                'form_3':  win_rate(won_flags, 3),
                'form_5':  win_rate(won_flags, 5),
                'form_15': win_rate(won_flags, 15),
                'form_50': win_rate(won_flags, 50),
            }

            # Per-surface form (last 20 matches on that surface)
            for surf in ('hard', 'clay', 'grass'):
                surf_results = [won for (_, won, s, _) in history if s == surf]
                if surf_results:
                    recent_surf = surf_results[max(0, len(surf_results)-20):]
                    form[f'surface_form_{surf}'] = sum(recent_surf) / len(recent_surf)
                else:
                    form[f'surface_form_{surf}'] = 0.5

            # Win rate vs top-50 proxy
            top50_results = [won for (_, won, _, opp) in history if opp in top_players]
            form['win_rate_vs_top50'] = (
                sum(top50_results) / len(top50_results) if top50_results else 0.5
            )

            self.player_form[player] = form

    def _build_match_caches(self):
        """Scan all Sackmann CSVs + supplemental CSV to build win/loss, H2H, and age caches."""
        wl: dict = {}
        h2h: dict = {}
        ages: dict = {}
        use_cols = ['winner_name', 'loser_name', 'surface', 'tourney_date',
                    'winner_age', 'loser_age']

        def _process_match(w, l, surf, tdate, w_age=None, l_age=None):
            """Process a single match into wl, h2h, ages."""
            if w not in wl:
                wl[w] = {'wins': 0, 'losses': 0}
            if l not in wl:
                wl[l] = {'wins': 0, 'losses': 0}
            wl[w]['wins'] += 1
            wl[l]['losses'] += 1

            if w_age is not None:
                ages[w] = float(w_age)
            if l_age is not None:
                ages[l] = float(l_age)

            key = tuple(sorted([w, l]))
            if key not in h2h:
                h2h[key] = {
                    'wins': {key[0]: 0, key[1]: 0},
                    'surface_wins': {},
                    'matches': [],
                }
            entry = h2h[key]
            entry['wins'][w] = entry['wins'].get(w, 0) + 1
            if surf:
                if surf not in entry['surface_wins']:
                    entry['surface_wins'][surf] = {key[0]: 0, key[1]: 0}
                entry['surface_wins'][surf][w] = entry['surface_wins'][surf].get(w, 0) + 1
            entry['matches'].append((tdate, w))

        # Only load 2010+ CSVs — older data is already baked into Glicko ratings
        all_csvs = sorted(SACKMANN_DIR.glob('atp_matches_*.csv'))
        csvs_2010 = [f for f in all_csvs if any(f'matches_{y}' in f.name for y in range(2010, 2030))]
        logger.info(f"  Loading {len(csvs_2010)} match CSVs (2010+, skipping {len(all_csvs) - len(csvs_2010)} older files)")
        for csv_path in csvs_2010:
            try:
                df = pd.read_csv(csv_path, usecols=use_cols, low_memory=False)
            except Exception:
                continue
            df = df.dropna(subset=['winner_name', 'loser_name'])

            for _, row in df.iterrows():
                w = row['winner_name']
                l = row['loser_name']
                surf = str(row.get('surface', '')).lower()
                tdate = int(row['tourney_date']) if pd.notna(row.get('tourney_date')) else 0
                w_age = float(row['winner_age']) if pd.notna(row.get('winner_age')) else None
                l_age = float(row['loser_age']) if pd.notna(row.get('loser_age')) else None
                _process_match(w, l, surf, tdate, w_age, l_age)

        # Load supplemental matches with name mapping
        if SUPPLEMENTAL_CSV.exists():
            suppl_name_map = _build_supplemental_name_map(self.player_names)
            self._supplemental_name_map = suppl_name_map
            try:
                sup_df = pd.read_csv(SUPPLEMENTAL_CSV)
                sup_df = sup_df.dropna(subset=['winner_name', 'loser_name'])
                suppl_count = 0
                for _, row in sup_df.iterrows():
                    w_raw = str(row['winner_name']).strip()
                    l_raw = str(row['loser_name']).strip()
                    w = suppl_name_map.get(w_raw)
                    l = suppl_name_map.get(l_raw)
                    if w is None or l is None:
                        continue
                    surf = str(row.get('surface', '')).lower()
                    tdate = int(row['tourney_date']) if pd.notna(row.get('tourney_date')) else 0
                    _process_match(w, l, surf, tdate)
                    suppl_count += 1
                logger.info(f"  ✓ Supplemental matches loaded ({suppl_count} mapped out of {len(sup_df)})")
            except Exception as e:
                logger.warning(f"  Could not load supplemental matches: {e}")
        else:
            self._supplemental_name_map = {}

        # Sort H2H matches by date for recency/streak computation
        for key, entry in h2h.items():
            entry['matches'].sort()

        self.win_loss = wl
        self.h2h = h2h
        self.player_ages = ages

    def _get_h2h_features(self, p1_name: str, p2_name: str, surface: str) -> dict:
        """Compute H2H features for p1 vs p2."""
        key = tuple(sorted([p1_name, p2_name]))
        entry = self.h2h.get(key)

        if entry is None:
            return {
                'h2h_wins_p1': 0, 'h2h_wins_p2': 0, 'h2h_total': 0,
                'h2h_has_history': 0, 'h2h_win_rate_p1': 0.5,
                'h2h_surface_wins_p1': 0, 'h2h_surface_total': 0,
                'h2h_surface_win_rate_p1': 0.5,
                'h2h_recency_p1': 0.5, 'h2h_streak_p1': 0,
            }

        p1_wins = entry['wins'].get(p1_name, 0)
        p2_wins = entry['wins'].get(p2_name, 0)
        total = p1_wins + p2_wins

        surf_data = entry['surface_wins'].get(surface, {})
        surf_p1 = surf_data.get(p1_name, 0)
        surf_total = surf_data.get(p1_name, 0) + surf_data.get(p2_name, 0)

        # Recency: fraction of last 5 matches won by p1
        recent = entry['matches'][-5:]
        if recent:
            recent_p1_wins = sum(1 for _, w in recent if w == p1_name)
            recency = recent_p1_wins / len(recent)
        else:
            recency = 0.5

        # Streak: count consecutive wins from the end
        streak = 0
        for _, w in reversed(entry['matches']):
            if w == p1_name:
                streak += 1
            elif w == p2_name:
                streak -= 1
                break
            else:
                break
            # Only count from the end
        # Actually, compute proper streak
        streak = 0
        for _, w in reversed(entry['matches']):
            if streak == 0:
                streak = 1 if w == p1_name else -1
            elif (streak > 0 and w == p1_name):
                streak += 1
            elif (streak < 0 and w == p2_name):
                streak -= 1
            else:
                break

        return {
            'h2h_wins_p1': p1_wins,
            'h2h_wins_p2': p2_wins,
            'h2h_total': total,
            'h2h_has_history': 1 if total > 0 else 0,
            'h2h_win_rate_p1': p1_wins / total if total > 0 else 0.5,
            'h2h_surface_wins_p1': surf_p1,
            'h2h_surface_total': surf_total,
            'h2h_surface_win_rate_p1': surf_p1 / surf_total if surf_total > 0 else 0.5,
            'h2h_recency_p1': recency,
            'h2h_streak_p1': streak,
        }

    # ── Player lookup ──────────────────────────────────────────────────────

    def find_player(self, query: str) -> Optional[str]:
        """
        Fuzzy match player name against glicko2 roster.
        Returns canonical full name (e.g. "Jannik Sinner") or None.

        Priority:
          1. Exact match (case-insensitive)
          2. Last name exact match
          3. Contains match (partial name, e.g. "Sinner")
          4. difflib close match (typos)
        """
        q = query.strip()
        q_lower = q.lower()

        def glicko_mu(n):
            r = self.glicko.ratings.get(n, {}).get('all')
            return r.mu if r else 0.0

        # 1. Exact match
        for name in self.player_names:
            if name.lower() == q_lower:
                return name

        # 2. Last name match — return highest-rated player with that surname
        lastname_matches = [
            n for n in self.player_names
            if n.split() and n.split()[-1].lower() == q_lower
        ]
        if lastname_matches:
            lastname_matches.sort(key=glicko_mu, reverse=True)
            return lastname_matches[0]

        # 3. Contains match — return highest-rated active player
        candidates = [n for n in self.player_names if q_lower in n.lower()]
        if candidates:
            candidates.sort(key=glicko_mu, reverse=True)
            return candidates[0]

        # 4. difflib close match (handles typos like "Djokovick")
        close = difflib.get_close_matches(q, self.player_names, n=1, cutoff=0.6)
        if close:
            return close[0]

        # 5. Try close matches on last names
        last_names = {n.split()[-1]: n for n in self.player_names if n.split()}
        close_last = difflib.get_close_matches(q, list(last_names.keys()), n=1, cutoff=0.6)
        if close_last:
            return last_names[close_last[0]]

        return None

    # ── Player Glicko-2 state ─────────────────────────────────────────────

    def _get_glicko_state(self, player_name: str, surface: str) -> dict:
        """Get current Glicko-2 state for a player on the given surface."""
        ratings = self.glicko.ratings.get(player_name, {})
        r_all  = ratings.get('all')
        r_surf = ratings.get(surface)

        if r_all is None:
            # Unknown player — return defaults
            return {
                'mu_all': 1500.0, 'rd_all': 350.0, 'vol_all': 0.06,
                'mu_surf': 1500.0, 'rd_surf': 350.0,
                'match_count': 0,
                'peak_mu': 1500.0, 'peak_date': None,
                'last_match_date': None,
            }

        # Apply inactivity RD growth up to latest data date
        import math as _math
        def aged_rd(r, current=LATEST_DATA_DATE):
            if r is None or r.last_match_date is None:
                return r.rd if r else 350.0
            days = (current - r.last_match_date).days
            if days <= 0:
                return r.rd
            growth = 0.5 * _math.sqrt(days)
            return min(350.0, _math.sqrt(r.rd**2 + growth**2))

        mu_all  = r_all.mu
        rd_all  = aged_rd(r_all)

        if r_surf and r_surf.match_count >= 10:
            mu_surf = r_surf.mu
            rd_surf = aged_rd(r_surf)
        else:
            mu_surf = mu_all
            rd_surf = rd_all

        return {
            'mu_all': mu_all, 'rd_all': rd_all, 'vol_all': r_all.volatility,
            'mu_surf': mu_surf, 'rd_surf': rd_surf,
            'match_count': r_all.match_count,
            'peak_mu': r_all.peak_mu,
            'peak_date': r_all.peak_date,
            'last_match_date': r_all.last_match_date,
        }

    # ── Player serve stats ─────────────────────────────────────────────────

    def _get_serve_stats(self, player_name: str) -> dict:
        """Compute serve/return stats from the all-time accumulator."""
        acc = self.attributes.get(player_name)
        if acc is None:
            return {
                'ace_rate': 0.065, 'df_rate': 0.040,
                'first_serve_pct': 0.620,
                'first_serve_won_pct': 0.720,
                'second_serve_won_pct': 0.520,
                'bp_save_pct': 0.630,
            }

        def safe_div(a, b, default=0.0):
            return a / b if b > 10 else default

        return {
            'ace_rate':             safe_div(acc.total_aces, acc.total_serve_points, 0.065),
            'df_rate':              safe_div(acc.total_dfs, acc.total_serve_points, 0.040),
            'first_serve_pct':      safe_div(acc.total_1st_serves_in, acc.total_1st_serve_attempts, 0.620),
            'first_serve_won_pct':  safe_div(acc.total_1st_serve_won, acc.total_1st_serve_played, 0.720),
            'second_serve_won_pct': safe_div(acc.total_2nd_serve_won, acc.total_2nd_serve_played, 0.520),
            'bp_save_pct':          safe_div(acc.total_bp_saved, acc.total_bp_faced, 0.630),
        }

    # ── Charted stats ─────────────────────────────────────────────────────

    def _get_charted_stats(self, player_name: str) -> dict:
        """Extract charted-data features from the attribute accumulator."""
        acc = self.attributes.get(player_name)
        if acc is None or acc.charted_matches == 0:
            return {
                'charted_match_count': 0,
                'charted_serve_entropy': 1.1,
                'charted_first_strike_rate': 0.60,
                'charted_pressure_divergence': 0.0,
                'charted_aggression_index': 0.50,
                'charted_long_rally_wr': 0.48,
                'charted_rally_win_slope': 0.0,
                'charted_pattern_diversity': 0.55,
                'charted_avg_rally_length': 4.0,
            }

        n_chart = acc.charted_matches
        pressure_div = 0.0
        if acc.pressure_matches > 0:
            bp_save = acc.pressure_bp_save_sum / acc.pressure_matches
            normal  = acc.normal_hold_sum / acc.pressure_matches
            pressure_div = bp_save - normal

        return {
            'charted_match_count': n_chart,
            'charted_serve_entropy':   acc.serve_direction_entropy_sum / max(1, acc.serve_direction_matches),
            'charted_first_strike_rate': acc.total_first_strike_won / max(1, acc.total_first_strike_points),
            'charted_pressure_divergence': pressure_div,
            'charted_aggression_index':    acc.aggression_sum / max(1, n_chart),
            'charted_long_rally_wr':    acc.long_rally_wins / max(1, acc.long_rally_points),
            'charted_rally_win_slope':  0.0,  # trajectory not tracked in accumulator
            'charted_pattern_diversity': acc.pattern_diversity_sum / max(1, n_chart),
            'charted_avg_rally_length': 4.0,  # not tracked in accumulator
        }

    # ── Feature vector ─────────────────────────────────────────────────────

    def build_feature_vector(
        self,
        p1_name: str,
        p2_name: str,
        surface: str = 'hard',
    ) -> pd.DataFrame:
        """
        Construct a single-row DataFrame with 168 features for the matchup.
        NaN for features not computable at inference — XGBoost/LightGBM handle them.
        """
        surface = surface.lower()
        surf_code = SURFACE_CODE.get(surface, 0)
        cpi_val   = SURFACE_CPI.get(surface, 36.0)

        p1g = self._get_glicko_state(p1_name, surface)
        p2g = self._get_glicko_state(p2_name, surface)

        p1s = self._get_serve_stats(p1_name)
        p2s = self._get_serve_stats(p2_name)

        p1c = self._get_charted_stats(p1_name)
        p2c = self._get_charted_stats(p2_name)

        p1f = self.player_form.get(p1_name, {})
        p2f = self.player_form.get(p2_name, {})

        def form_val(form_dict, key, default=0.5):
            return form_dict.get(key, default)

        p1_form3  = form_val(p1f, 'form_3')
        p1_form5  = form_val(p1f, 'form_5')
        p1_form15 = form_val(p1f, 'form_15')
        p1_form50 = form_val(p1f, 'form_50')
        p1_surf_form = form_val(p1f, f'surface_form_{surface}')
        p1_top50  = form_val(p1f, 'win_rate_vs_top50')

        p2_form3  = form_val(p2f, 'form_3')
        p2_form5  = form_val(p2f, 'form_5')
        p2_form15 = form_val(p2f, 'form_15')
        p2_form50 = form_val(p2f, 'form_50')
        p2_surf_form = form_val(p2f, f'surface_form_{surface}')
        p2_top50  = form_val(p2f, 'win_rate_vs_top50')

        # Glicko-2 derived features
        elo_diff        = p1g['mu_all']  - p2g['mu_all']
        elo_surf_diff   = p1g['mu_surf'] - p2g['mu_surf']
        rd_diff         = p1g['rd_all']  - p2g['rd_all']
        conf_gap        = abs(p1g['rd_all'] - p2g['rd_all'])
        vol_diff        = p1g['vol_all'] - p2g['vol_all']

        # Rating band overlap (95% CI)
        p1_lower = p1g['mu_all'] - 2 * p1g['rd_all']
        p1_upper = p1g['mu_all'] + 2 * p1g['rd_all']
        p2_lower = p2g['mu_all'] - 2 * p2g['rd_all']
        p2_upper = p2g['mu_all'] + 2 * p2g['rd_all']
        overlap   = max(0.0, min(p1_upper, p2_upper) - max(p1_lower, p2_lower))
        total_span = max(p1_upper, p2_upper) - min(p1_lower, p2_lower)
        band_overlap = overlap / total_span if total_span > 0 else 0.5

        # Neutral weather defaults (median from training set)
        match_temp  = 20.0
        match_humid = 65.0
        match_wind  = 12.0

        # Charted diffs
        c_entropy_diff  = p1c['charted_serve_entropy']     - p2c['charted_serve_entropy']
        c_fstrike_diff  = p1c['charted_first_strike_rate'] - p2c['charted_first_strike_rate']
        c_press_diff    = p1c['charted_pressure_divergence'] - p2c['charted_pressure_divergence']
        c_agg_diff      = p1c['charted_aggression_index']  - p2c['charted_aggression_index']
        c_lrally_diff   = p1c['charted_long_rally_wr']     - p2c['charted_long_rally_wr']
        c_slope_diff    = p1c['charted_rally_win_slope']   - p2c['charted_rally_win_slope']
        c_pat_diff      = p1c['charted_pattern_diversity'] - p2c['charted_pattern_diversity']
        c_rally_diff    = p1c['charted_avg_rally_length']  - p2c['charted_avg_rally_length']

        # Style clash (aggression difference)
        style_clash = abs(c_agg_diff)

        # Weather interaction features (neutral weather → interaction ≈ 0)
        # heat_x_fatigue: match_temp * acwr (both neutral → ~0)
        heat_x_fatigue = match_temp * 0.47  # 0.47 is median acwr
        heat_x_fatigue_diff = 0.0
        wind_x_serve_dep = match_wind * (p1s['ace_rate'] - p2s['ace_rate'])
        humidity_x_rally = match_humid * abs(c_rally_diff)
        heat_x_endurance_diff = 0.0

        row = {
            'surface_code':        surf_code,
            'tourney_level_code':  2,          # default: Masters/Slam level
            'best_of':             3,
            'ball_type':           0,
            'match_temp':          match_temp,
            'match_humidity':      match_humid,
            'match_wind':          match_wind,
            'match_precip':        0.0,
            'is_indoor':           0,
            'is_extreme_heat':     0,
            'is_high_wind':        0,
            'is_high_humidity':    0,
            'roof_likely_closed':  0,
            'imputed_cpi':         cpi_val,
            'cpi':                 cpi_val,
            'rank_diff':           0,

            # Glicko-2
            'p1_elo_all':          p1g['mu_all'],
            'p2_elo_all':          p2g['mu_all'],
            'elo_diff':            elo_diff,
            'p1_elo_surface':      p1g['mu_surf'],
            'p2_elo_surface':      p2g['mu_surf'],
            'elo_surface_diff':    elo_surf_diff,
            'p1_rd_all':           p1g['rd_all'],
            'p2_rd_all':           p2g['rd_all'],
            'rd_diff':             rd_diff,
            'confidence_gap':      conf_gap,
            'rating_band_overlap': band_overlap,
            'p1_volatility':       p1g['vol_all'],
            'p2_volatility':       p2g['vol_all'],
            'volatility_diff':     vol_diff,
            'p1_match_count':      p1g['match_count'],
            'p2_match_count':      p2g['match_count'],

            # Fatigue — neutral defaults (no live match schedule data)
            'p1_acute_stress_7d':          0.0,
            'p1_acute_minutes_7d':         0.0,
            'p1_acute_match_count_7d':     0.0,
            'p1_chronic_stress_28d':       0.0,
            'p1_chronic_minutes_28d':      0.0,
            'p1_chronic_match_count_28d':  0.0,
            'p1_acwr':                     0.47,  # median
            'p1_stress_14d':               0.0,
            'p1_minutes_14d':              0.0,
            'p1_days_rest':                7.0,   # neutral rest
            'p1_last_match_minutes':       90.0,  # neutral match length
            'p1_last_match_stress':        0.5,
            'p1_last_opponent_grinding':   0.5,
            'p1_last_match_competitiveness': 0.5,
            'p1_consecutive_match_days':   0,
            'p1_consecutive_day_stress':   0.0,
            'p1_tournament_load_30d':      0.0,
            'p1_avg_recent_grinding':      0.5,
            'p1_avg_recent_competitiveness': 0.5,
            'p1_surface_consistency_recent': 0.5,

            'p2_acute_stress_7d':          0.0,
            'p2_acute_minutes_7d':         0.0,
            'p2_acute_match_count_7d':     0.0,
            'p2_chronic_stress_28d':       0.0,
            'p2_chronic_minutes_28d':      0.0,
            'p2_chronic_match_count_28d':  0.0,
            'p2_acwr':                     0.47,
            'p2_stress_14d':               0.0,
            'p2_minutes_14d':              0.0,
            'p2_days_rest':                7.0,
            'p2_last_match_minutes':       90.0,
            'p2_last_match_stress':        0.5,
            'p2_last_opponent_grinding':   0.5,
            'p2_last_match_competitiveness': 0.5,
            'p2_consecutive_match_days':   0,
            'p2_consecutive_day_stress':   0.0,
            'p2_tournament_load_30d':      0.0,
            'p2_avg_recent_grinding':      0.5,
            'p2_avg_recent_competitiveness': 0.5,
            'p2_surface_consistency_recent': 0.5,

            # Fatigue diffs (all 0 when both at neutral)
            'acute_stress_7d_diff':        0.0,
            'chronic_stress_28d_diff':     0.0,
            'acwr_diff':                   0.0,
            'days_rest_diff':              0.0,
            'consecutive_match_days_diff': 0,
            'minutes_14d_diff':            0.0,
            'last_match_stress_diff':      0.0,
            'tournament_load_30d_diff':    0.0,
            'fatigue_asymmetry':           0.0,

            # Charted
            'p1_charted_match_count':        p1c['charted_match_count'],
            'p1_charted_serve_entropy':      p1c['charted_serve_entropy'],
            'p1_charted_first_strike_rate':  p1c['charted_first_strike_rate'],
            'p1_charted_pressure_divergence': p1c['charted_pressure_divergence'],
            'p1_charted_aggression_index':   p1c['charted_aggression_index'],
            'p1_charted_long_rally_wr':      p1c['charted_long_rally_wr'],
            'p1_charted_rally_win_slope':    p1c['charted_rally_win_slope'],
            'p1_charted_pattern_diversity':  p1c['charted_pattern_diversity'],
            'p1_charted_avg_rally_length':   p1c['charted_avg_rally_length'],

            'p2_charted_match_count':        p2c['charted_match_count'],
            'p2_charted_serve_entropy':      p2c['charted_serve_entropy'],
            'p2_charted_first_strike_rate':  p2c['charted_first_strike_rate'],
            'p2_charted_pressure_divergence': p2c['charted_pressure_divergence'],
            'p2_charted_aggression_index':   p2c['charted_aggression_index'],
            'p2_charted_long_rally_wr':      p2c['charted_long_rally_wr'],
            'p2_charted_rally_win_slope':    p2c['charted_rally_win_slope'],
            'p2_charted_pattern_diversity':  p2c['charted_pattern_diversity'],
            'p2_charted_avg_rally_length':   p2c['charted_avg_rally_length'],

            'charted_serve_entropy_diff':       c_entropy_diff,
            'charted_first_strike_rate_diff':   c_fstrike_diff,
            'charted_pressure_divergence_diff': c_press_diff,
            'charted_aggression_index_diff':    c_agg_diff,
            'charted_long_rally_wr_diff':       c_lrally_diff,
            'charted_rally_win_slope_diff':     c_slope_diff,
            'charted_pattern_diversity_diff':   c_pat_diff,
            'charted_avg_rally_length_diff':    c_rally_diff,
            'style_clash':                      style_clash,

            # Rolling form
            'p1_form_3':         p1_form3,
            'p1_form_5':         p1_form5,
            'p1_form_15':        p1_form15,
            'p1_form_50':        p1_form50,
            'p1_surface_form':   p1_surf_form,
            'p1_h2h_pct':        0.5,   # no historical H2H at inference time

            # Serve stats
            'p1_ace_rate':              p1s['ace_rate'],
            'p1_df_rate':               p1s['df_rate'],
            'p1_first_serve_pct':       p1s['first_serve_pct'],
            'p1_first_serve_won_pct':   p1s['first_serve_won_pct'],
            'p1_second_serve_won_pct':  p1s['second_serve_won_pct'],
            'p1_bp_save_pct':           p1s['bp_save_pct'],
            'p1_win_rate_vs_top50':     p1_top50,

            'p2_form_3':         p2_form3,
            'p2_form_5':         p2_form5,
            'p2_form_15':        p2_form15,
            'p2_form_50':        p2_form50,
            'p2_surface_form':   p2_surf_form,
            'p2_h2h_pct':        0.5,

            'p2_ace_rate':              p2s['ace_rate'],
            'p2_df_rate':               p2s['df_rate'],
            'p2_first_serve_pct':       p2s['first_serve_pct'],
            'p2_first_serve_won_pct':   p2s['first_serve_won_pct'],
            'p2_second_serve_won_pct':  p2s['second_serve_won_pct'],
            'p2_bp_save_pct':           p2s['bp_save_pct'],
            'p2_win_rate_vs_top50':     p2_top50,

            # Diff features
            'form_3_diff':              p1_form3   - p2_form3,
            'form_5_diff':              p1_form5   - p2_form5,
            'form_15_diff':             p1_form15  - p2_form15,
            'form_50_diff':             p1_form50  - p2_form50,
            'surface_form_diff':        p1_surf_form - p2_surf_form,
            'h2h_pct_diff':             0.0,
            'ace_rate_diff':            p1s['ace_rate']             - p2s['ace_rate'],
            'df_rate_diff':             p1s['df_rate']              - p2s['df_rate'],
            'first_serve_pct_diff':     p1s['first_serve_pct']      - p2s['first_serve_pct'],
            'first_serve_won_pct_diff': p1s['first_serve_won_pct']  - p2s['first_serve_won_pct'],
            'second_serve_won_pct_diff': p1s['second_serve_won_pct'] - p2s['second_serve_won_pct'],
            'bp_save_pct_diff':         p1s['bp_save_pct']          - p2s['bp_save_pct'],
            'win_rate_vs_top50_diff':   p1_top50                    - p2_top50,

            # Weather interaction features
            'heat_x_fatigue':           heat_x_fatigue,
            'heat_x_fatigue_diff':      heat_x_fatigue_diff,
            'wind_x_serve_dep':         wind_x_serve_dep,
            'humidity_x_rally':         humidity_x_rally,
            'heat_x_endurance_diff':    heat_x_endurance_diff,
        }

        # H2H features
        h2h = self._get_h2h_features(p1_name, p2_name, surface)
        row.update(h2h)

        # Age & career features
        p1_age = self.player_ages.get(p1_name, 27.0)  # median fallback
        p2_age = self.player_ages.get(p2_name, 27.0)
        p1_wl = self.win_loss.get(p1_name, {'wins': 0, 'losses': 0})
        p2_wl = self.win_loss.get(p2_name, {'wins': 0, 'losses': 0})
        p1_career = p1_wl['wins'] + p1_wl['losses']
        p2_career = p2_wl['wins'] + p2_wl['losses']
        row['p1_age'] = p1_age
        row['p2_age'] = p2_age
        row['age_diff'] = p1_age - p2_age
        row['p1_career_matches'] = p1_career
        row['p2_career_matches'] = p2_career
        row['career_match_diff'] = p1_career - p2_career

        return pd.DataFrame([row], columns=self.feature_cols)

    # ── Prediction ─────────────────────────────────────────────────────────

    def predict(
        self,
        player1: str,
        player2: str,
        surface: str = 'hard',
    ) -> dict:
        """
        Predict win probabilities for player1 vs player2 on given surface.

        Returns:
            {
              'player1_name': 'Jannik Sinner',
              'player2_name': 'Carlos Alcaraz',
              'surface': 'hard',
              'player1_win_prob': 0.57,
              'player2_win_prob': 0.43,
              'confidence': 'high',
              'confidence_reason': 'Low RD for both players',
              'model': '2-model stacked (XGB+LGB)',
              'elo_diff': 172.0,
              'rd_p1': 74.0,
              'rd_p2': 72.0,
            }
        """
        # Lookup canonical names
        p1_canonical = self.find_player(player1)
        p2_canonical = self.find_player(player2)

        if p1_canonical is None:
            raise ValueError(f"Player not found: {player1!r}")
        if p2_canonical is None:
            raise ValueError(f"Player not found: {player2!r}")

        surface = surface.lower()
        if surface not in SURFACE_CODE:
            surface = 'hard'

        # Build feature vector
        X = self.build_feature_vector(p1_canonical, p2_canonical, surface)

        # XGBoost prediction
        xgb_prob = self.xgb_model.predict_proba(X)[:, 1][0]

        # LightGBM prediction (needs clean column names)
        X_lgb = X.copy()
        X_lgb.columns = X_lgb.columns.str.replace('[^A-Za-z0-9_]', '_', regex=True)
        lgb_prob = self.lgb_model.predict_proba(X_lgb)[:, 1][0]

        # Stack ensemble
        ens = self.ensemble
        if isinstance(ens, dict) and ens.get('type') == 'stacked':
            model_names = ens['model_names']
            stacker     = ens['stacker']
            base_probs  = [xgb_prob if n == 'xgboost' else lgb_prob
                          for n in model_names]
            meta_X = np.array([base_probs])
            p1_win_prob = float(stacker.predict_proba(meta_X)[:, 1][0])
        elif hasattr(ens, 'predict'):
            # Old StackedEnsemble class format
            preds = {'xgboost': np.array([xgb_prob]),
                     'lightgbm': np.array([lgb_prob])}
            p1_win_prob = float(ens.predict(preds)[0])
        else:
            # Fallback: simple average
            p1_win_prob = (xgb_prob + lgb_prob) / 2

        p2_win_prob = 1.0 - p1_win_prob

        # Confidence based on Glicko-2 RD
        p1g = self._get_glicko_state(p1_canonical, surface)
        p2g = self._get_glicko_state(p2_canonical, surface)
        max_rd = max(p1g['rd_all'], p2g['rd_all'])

        if max_rd < 80:
            confidence = 'high'
            confidence_reason = 'Both players have well-established ratings'
        elif max_rd < 150:
            confidence = 'medium'
            confidence_reason = 'One or both players have some rating uncertainty'
        else:
            confidence = 'low'
            confidence_reason = 'High rating uncertainty — limited match history'

        return {
            'player1_name':     p1_canonical,
            'player2_name':     p2_canonical,
            'surface':          surface,
            'player1_win_prob': float(round(p1_win_prob, 3)),
            'player2_win_prob': float(round(p2_win_prob, 3)),
            'confidence':       confidence,
            'confidence_reason': confidence_reason,
            'model':            'stacked (XGB+LGB)',
            'xgb_prob':         round(float(xgb_prob), 3),
            'lgb_prob':         round(float(lgb_prob), 3),
            'elo_diff':         float(round(p1g['mu_all'] - p2g['mu_all'], 1)),
            'elo_surface_diff': float(round(p1g['mu_surf'] - p2g['mu_surf'], 1)),
            'rd_p1':            float(round(p1g['rd_all'], 1)),
            'rd_p2':            float(round(p2g['rd_all'], 1)),
            'p1_glicko':        float(round(p1g['mu_all'], 0)),
            'p2_glicko':        float(round(p2g['mu_all'], 0)),
        }

    # ── FIFA card (Task 3) ─────────────────────────────────────────────────

    def get_player_card(self, player_name: str, surface: str = 'hard') -> dict:
        """
        Generate full FIFA card data for a player.

        Returns:
            {
              'name': 'Carlos Alcaraz',
              'country': 'ESP',
              'flag': '🇪🇸',
              'overall': 93,
              'tier': 'legendary',
              'form_modifier': +2.5,
              'is_retired': False,
              'peak_year': 2024,
              'glow': False,
              'surfaces': {'hard': 90, 'clay': 94, 'grass': 86},
              'attributes': {'serve': 88, 'groundstroke': 94, ...},
            }
        """
        canonical = self.find_player(player_name)
        if canonical is None:
            return None

        surface = surface.lower()
        p1g = self._get_glicko_state(canonical, surface)

        # Retirement detection: no match in 18+ months from latest data date
        is_retired = False
        if p1g['last_match_date'] is not None:
            days_since = (LATEST_DATA_DATE - p1g['last_match_date']).days
            if days_since > RETIREMENT_THRESHOLD_DAYS and p1g['match_count'] > 20:
                is_retired = True

        # Form modifier from recent form
        form_data = self.player_form.get(canonical, {})
        form_3 = form_data.get('form_3', 0.5)

        # Compute FIFA rating manually to bypass glicko2.get_fifa_rating()'s
        # broken is_retired logic (it checks match_count > 20 instead of date).
        def _compute_card(mu, peak_mu, rd, is_ret, form_3_val):
            elo_for_rating = peak_mu if is_ret else mu
            base = 55.0 + 42.0 / (1.0 + math.exp(-0.004 * (elo_for_rating - 1750.0)))
            form_mod = (form_3_val - 0.5) * 8.0 if not is_ret else 0.0
            display = base + form_mod
            if display >= 91:
                tier = 'legendary'
            elif display >= 80:
                tier = 'gold'
            elif display >= 69:
                tier = 'silver'
            else:
                tier = 'bronze'
            base_tier = ('legendary' if base >= 91 else 'gold' if base >= 80
                         else 'silver' if base >= 69 else 'bronze')
            return {
                'display_rating': round(display, 1),
                'base_rating': round(base, 1),
                'form_modifier': round(form_mod, 1),
                'tier': tier,
                'has_glow': tier != base_tier,
                'glow_direction': 'up' if display > base else 'down' if display < base else 'none',
            }

        r_all = self.glicko.ratings.get(canonical, {}).get('all')
        rating = _compute_card(
            p1g['mu_all'], p1g['peak_mu'], p1g['rd_all'], is_retired, form_3
        ) if r_all else {'display_rating': 50, 'base_rating': 50, 'form_modifier': 0,
                         'tier': 'bronze', 'has_glow': False, 'glow_direction': 'none'}

        # Peak year
        peak_year = p1g['peak_date'].year if p1g['peak_date'] else None

        # Surface ratings
        surfaces_out = {}
        for surf in ('hard', 'clay', 'grass'):
            r = self.glicko.ratings.get(canonical, {}).get(surf)
            if r and r.match_count >= 10:
                sr = _compute_card(r.mu, r.peak_mu, r.rd, is_retired, form_3)
                surfaces_out[surf] = sr['display_rating']
            else:
                surfaces_out[surf] = None

        # FIFA attributes from accumulator
        acc = self.attributes.get(canonical)
        attributes = {}
        if acc:
            from modules.player_attributes import AttributeRanker
            # Use a population-normalized ranker
            raw = acc.compute_raw_attributes()
            # Simple 0-100 mapping (percentile needs full population; use linear map as fallback)
            for attr_name, raw_val in raw.items():
                attributes[attr_name] = int(np.clip(30 + raw_val * 69, 30, 99))
            # Fix defaults: footwork raw=0.0 means no data (all components missing)
            if raw.get('footwork', 0.0) == 0.0:
                attributes['footwork'] = None
            # Fix defaults: volley raw=0.5 is universal default (no charted net data)
            if abs(raw.get('volley', 0.5) - 0.5) < 0.001:
                attributes['volley'] = None
        else:
            for attr in ('serve', 'groundstroke', 'volley', 'footwork',
                         'endurance', 'durability', 'clutch', 'mental'):
                attributes[attr] = 50

        # Apply proxies for footwork/volley from charted data
        proxies = self.attribute_proxies.get(canonical, {})
        if attributes.get('footwork') is None and 'footwork' in proxies:
            attributes['footwork'] = proxies['footwork']
        if attributes.get('volley') is None and 'volley' in proxies:
            attributes['volley'] = proxies['volley']

        # Country code from Sackmann data (best-effort)
        country_code = _get_player_country(canonical)

        return {
            'name':           canonical,
            'country':        country_code,
            'flag':           _country_flag(country_code),
            'overall':        rating['display_rating'],
            'tier':           rating['tier'],
            'base_rating':    rating['base_rating'],
            'form_modifier':  rating['form_modifier'],
            'is_retired':     is_retired,
            'peak_year':      peak_year,
            'glow':           rating['has_glow'],
            'glow_direction': rating['glow_direction'],
            'elo':            round(p1g['mu_all'], 1),
            'rd':             round(p1g['rd_all'], 1),
            'match_count':    int(p1g['match_count']),
            'wins':           int(self.win_loss.get(canonical, {}).get('wins', 0)),
            'losses':         int(self.win_loss.get(canonical, {}).get('losses', 0)),
            'last_match':     str(p1g['last_match_date']) if p1g['last_match_date'] else None,
            'surfaces':       surfaces_out,
            'attributes':     attributes,
            'attribute_averages': self.attribute_averages,
            'form': {
                'form_3':   form_3,
                'form_5':   form_data.get('form_5', 0.5),
                'form_15':  form_data.get('form_15', 0.5),
            },
        }


# ── Country helpers ──────────────────────────────────────────────────────────

_COUNTRY_CACHE: dict = {}

def _get_player_country(player_name: str) -> str:
    """Look up country from Sackmann CSV (cached)."""
    if not _COUNTRY_CACHE:
        _build_country_cache()
    return _COUNTRY_CACHE.get(player_name, 'UNK')


def _build_country_cache():
    """Build player → country mapping from Sackmann CSVs."""
    for year in range(2020, 2025):
        csv_path = SACKMANN_DIR / f'atp_matches_{year}.csv'
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path, usecols=['winner_name', 'winner_ioc', 'loser_name', 'loser_ioc'])
            for _, row in df.iterrows():
                if row['winner_name'] and not pd.isna(row['winner_name']):
                    _COUNTRY_CACHE[row['winner_name']] = str(row.get('winner_ioc', 'UNK') or 'UNK')
                if row['loser_name'] and not pd.isna(row['loser_name']):
                    _COUNTRY_CACHE[row['loser_name']] = str(row.get('loser_ioc', 'UNK') or 'UNK')
        except Exception:
            pass


_IOC_TO_FLAG = {
    'AUS': '🇦🇺', 'AUT': '🇦🇹', 'BEL': '🇧🇪', 'BRA': '🇧🇷', 'BUL': '🇧🇬',
    'CAN': '🇨🇦', 'CHI': '🇨🇱', 'CHN': '🇨🇳', 'CRO': '🇭🇷', 'CZE': '🇨🇿',
    'DEN': '🇩🇰', 'ESP': '🇪🇸', 'FIN': '🇫🇮', 'FRA': '🇫🇷', 'GBR': '🇬🇧',
    'GEO': '🇬🇪', 'GER': '🇩🇪', 'GRE': '🇬🇷', 'HUN': '🇭🇺', 'ITA': '🇮🇹',
    'JPN': '🇯🇵', 'KAZ': '🇰🇿', 'NED': '🇳🇱', 'NOR': '🇳🇴', 'POL': '🇵🇱',
    'POR': '🇵🇹', 'ROU': '🇷🇴', 'RSA': '🇿🇦', 'RUS': '🇷🇺', 'SER': '🇷🇸',
    'SRB': '🇷🇸', 'SUI': '🇨🇭', 'SVK': '🇸🇰', 'SWE': '🇸🇪', 'TUN': '🇹🇳',
    'UKR': '🇺🇦', 'URU': '🇺🇾', 'USA': '🇺🇸', 'UZB': '🇺🇿',
}

def _country_flag(ioc: str) -> str:
    return _IOC_TO_FLAG.get(str(ioc).upper(), '🏳️')
