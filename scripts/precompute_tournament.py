#!/usr/bin/env python3
"""Precompute tournament predictions for the most recent Grand Slam."""
import sys
import json
import pickle
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# Fix pickle deserialization: StackedEnsemble was saved as __main__.StackedEnsemble
import importlib
_et = importlib.import_module('scripts.ensemble_trainer')
import __main__
__main__.StackedEnsemble = _et.StackedEnsemble

from src.api.predict_engine import PredictEngine

engine = PredictEngine.get()
engine.load()

SACKMANN = BASE / 'data' / 'sackmann' / 'tennis_atp'

# Try to find the most recent Grand Slam (US Open 2024 or Australian Open 2024)
# Grand Slams have tourney_level = 'G'
target_tournament = None
target_year = None

for year in [2024, 2023]:
    for csv_file in sorted((SACKMANN).glob(f'atp_matches_{year}.csv')):
        df = pd.read_csv(csv_file, low_memory=False)
        slams = df[df['tourney_level'] == 'G']['tourney_name'].unique()
        if len(slams) > 0:
            # Use last Grand Slam of the year
            slam_dates = []
            for slam_name in slams:
                slam_rows = df[df['tourney_name'] == slam_name]
                max_date = slam_rows['tourney_date'].max()
                slam_dates.append((slam_name, max_date))
            slam_dates.sort(key=lambda x: x[1], reverse=True)
            target_tournament = slam_dates[0][0]
            target_year = year
            print(f"Using: {target_tournament} {target_year}")
            break
    if target_tournament:
        break

if not target_tournament:
    print("No Grand Slam found, using US Open 2023")
    target_tournament = "US Open"
    target_year = 2023

# Load tournament data
csv_path = SACKMANN / f'atp_matches_{target_year}.csv'
df = pd.read_csv(csv_path, low_memory=False)
tourn_df = df[df['tourney_name'] == target_tournament].copy()

print(f"Matches found: {len(tourn_df)}")

# Get all players in this tournament
all_players = sorted(set(tourn_df['winner_name'].dropna()) | set(tourn_df['loser_name'].dropna()))
print(f"Players in tournament: {len(all_players)}")

# Map each player to their seed
seed_map = {}
for _, row in tourn_df.iterrows():
    if pd.notna(row.get('winner_name')) and pd.notna(row.get('winner_seed')):
        try:
            seed_map[row['winner_name']] = int(row['winner_seed'])
        except:
            pass
    if pd.notna(row.get('loser_name')) and pd.notna(row.get('loser_seed')):
        try:
            seed_map[row['loser_name']] = int(row['loser_seed'])
        except:
            pass

# Determine surface
surface = tourn_df['surface'].mode()[0].lower() if not tourn_df.empty else 'hard'
print(f"Surface: {surface}")

# Round order for Grand Slams
ROUNDS = ['R128', 'R64', 'R32', 'R16', 'QF', 'SF', 'F']

# For each player, compute Glicko-2 rating for draw difficulty
def get_glicko(name):
    canonical = engine.find_player(name)
    if not canonical:
        return 1500.0
    r = engine.glicko.ratings.get(canonical, {}).get('all')
    return r.mu if r else 1500.0

# Precompute all pairwise win probs for players in tournament
print("Computing pairwise predictions...")
players_canonical = {}
for p in all_players:
    can = engine.find_player(p)
    if can:
        players_canonical[p] = can

n = len(players_canonical)
print(f"Found {n} canonical players")

# Simulate tournament using actual draw structure (match history)
# Build round-by-round results
round_results = {}
for _, row in tourn_df.iterrows():
    rnd = str(row.get('round', ''))
    if rnd not in round_results:
        round_results[rnd] = []
    round_results[rnd].append({
        'winner': row.get('winner_name'),
        'loser': row.get('loser_name'),
    })

# For prediction purposes: simulate who would win each round
# using ML predictions. Start with R128 bracket.

# Build bracket from actual matches
# Strategy: use the actual round structure from the CSV
# For each player, track cumulative win probability per round

player_probs = {p: {'R128': 1.0, 'R64': 0.0, 'R32': 0.0, 'R16': 0.0, 'QF': 0.0, 'SF': 0.0, 'F': 0.0, 'W': 0.0} for p in all_players}

# Process each round
for rnd in ROUNDS:
    if rnd not in round_results:
        continue
    for match in round_results[rnd]:
        w = match['winner']
        l = match['loser']
        if w in player_probs and l in player_probs:
            # Get prediction
            w_can = players_canonical.get(w)
            l_can = players_canonical.get(l)

            if w_can and l_can:
                try:
                    pred = engine.predict(w_can, l_can, surface)
                    w_prob = pred['player1_win_prob']
                    l_prob = pred['player2_win_prob']
                except:
                    w_prob = 0.6  # default slight favor to actual winner
                    l_prob = 0.4
            else:
                w_prob = 0.6
                l_prob = 0.4

            # Advance probability to next round
            next_rnd_idx = ROUNDS.index(rnd) + 1
            if next_rnd_idx < len(ROUNDS):
                next_rnd = ROUNDS[next_rnd_idx]
                if next_rnd == 'F':
                    # SF winner goes to F
                    player_probs[w][next_rnd] = player_probs[w].get(rnd, 1.0) * w_prob
                    player_probs[l][next_rnd] = player_probs[l].get(rnd, 1.0) * l_prob
                else:
                    player_probs[w][next_rnd] = player_probs[w].get(rnd, 1.0) * w_prob
                    player_probs[l][next_rnd] = max(player_probs[l].get(next_rnd, 0.0), player_probs[l].get(rnd, 1.0) * l_prob)

            # Winner probability
            if rnd == 'F':
                player_probs[w]['W'] = player_probs[w].get('F', 1.0) * w_prob
                player_probs[l]['W'] = player_probs[l].get('F', 1.0) * l_prob

# Compute draw difficulty
glicko_ratings = {p: get_glicko(players_canonical.get(p, p)) for p in all_players}
all_glickos = list(glicko_ratings.values())
min_g, max_g = min(all_glickos), max(all_glickos)

def draw_difficulty(player_name):
    """Average Glicko of opponents in player's half, normalized to 0-10."""
    opponents = []
    for _, row in tourn_df.iterrows():
        if row.get('winner_name') == player_name:
            opponents.append(row.get('loser_name'))
        elif row.get('loser_name') == player_name:
            opponents.append(row.get('winner_name'))

    if not opponents:
        return 5.0

    opp_ratings = [glicko_ratings.get(o, 1500) for o in opponents if o]
    if not opp_ratings:
        return 5.0

    avg_rating = np.mean(opp_ratings)
    # Normalize: 1500 = 0, 2100 = 10
    normalized = np.clip((avg_rating - 1500) / 60.0, 0, 10)
    return round(float(normalized), 1)

# Build results sorted by win probability
results = []
for player in all_players:
    probs = player_probs.get(player, {})
    dd = draw_difficulty(player)
    results.append({
        'player': player,
        'seed': seed_map.get(player),
        'draw_difficulty': dd,
        'probs': {
            'R64': round(probs.get('R64', 0.0), 3),
            'R32': round(probs.get('R32', 0.0), 3),
            'R16': round(probs.get('R16', 0.0), 3),
            'QF': round(probs.get('QF', 0.0), 3),
            'SF': round(probs.get('SF', 0.0), 3),
            'F': round(probs.get('F', 0.0), 3),
            'W': round(probs.get('W', 0.0), 3),
        }
    })

results.sort(key=lambda x: x['probs']['W'], reverse=True)

output = {
    'tournament': target_tournament,
    'year': target_year,
    'surface': surface,
    'players': results[:32]
}

out_path = BASE / 'data' / 'processed' / 'tournament_predictions.json'
with open(out_path, 'w') as f:
    json.dump(output, f, indent=2)

print(f"\nSaved tournament predictions to {out_path}")
print(f"Top 5 predicted winners:")
for r in results[:5]:
    print(f"  {r['player']} (seed {r['seed']}): {r['probs']['W']:.1%} to win")
