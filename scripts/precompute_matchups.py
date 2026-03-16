#!/usr/bin/env python3
"""
Precompute matchup win probabilities for top 100 players.
Saves to data/processed/matchup_grid.json
"""
import sys
import json
import pickle
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# Fix pickle deserialization: StackedEnsemble was saved as __main__.StackedEnsemble
# We need to make it available under __main__ before PredictEngine.load() is called
import importlib
_et = importlib.import_module('scripts.ensemble_trainer')
# Register StackedEnsemble in __main__ namespace so pickle can find it
import __main__
__main__.StackedEnsemble = _et.StackedEnsemble

from src.api.predict_engine import PredictEngine

engine = PredictEngine.get()
engine.load()

# Get top 100 by Glicko-2 overall rating
all_ratings = []
for name, surfaces in engine.glicko.ratings.items():
    r = surfaces.get('all')
    if r and r.match_count >= 30:
        all_ratings.append((name, r.mu))

all_ratings.sort(key=lambda x: x[1], reverse=True)
top100 = [name for name, _ in all_ratings[:100]]

print(f"Top 100 players determined. Top 5: {top100[:5]}")

surfaces = ['hard', 'clay', 'grass']
grid = {}  # player -> {surface -> {toughest: [...], easiest: [...]}}

total = len(top100) * len(surfaces)
done = 0

for i, player in enumerate(top100):
    grid[player] = {}
    for surface in surfaces:
        matchups = []
        for opponent in top100:
            if opponent == player:
                continue
            try:
                result = engine.predict(player, opponent, surface)
                matchups.append({
                    "opponent": opponent,
                    "player_win_prob": round(result['player1_win_prob'], 3)
                })
            except Exception as e:
                pass

        matchups.sort(key=lambda x: x['player_win_prob'])
        grid[player][surface] = {
            "toughest": matchups[:5],   # lowest win prob
            "easiest": matchups[-5:][::-1]   # highest win prob
        }
        done += len(top100) - 1

    if (i + 1) % 10 == 0:
        print(f"Progress: {i+1}/{len(top100)} players done ({done} predictions)")

out = BASE / 'data' / 'processed' / 'matchup_grid.json'
with open(out, 'w') as f:
    json.dump({"top100": top100, "grid": grid}, f)

print(f"\nSaved matchup grid to {out}")
print(f"Total top100: {len(top100)}")

# Print Sinner's toughest on hard
if 'Jannik Sinner' in grid:
    print("\nSinner toughest on hard:")
    for m in grid['Jannik Sinner']['hard']['toughest'][:3]:
        print(f"  {m['opponent']}: {m['player_win_prob']:.1%} win prob")
