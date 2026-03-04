# Predictive Matchups Module (Tennis IQ)

Self-contained matchup prediction system. Safe to delete this entire folder to remove the feature.

## Structure

```
matchups/
├── README.md            ← this file
├── pipeline/
│   └── matchup_engine.js ← offline scoring, exports JSON
└── ui/
    └── MatchupExplorer.js ← frontend component
```

## Output
Writes to `docs/data/matchups/{player_id}.json`

## How to run
```bash
node matchups/pipeline/matchup_engine.js
```

## How to remove
1. Delete this `matchups/` folder
2. Delete `docs/data/matchups/` directory
3. Remove matchup imports from `docs/js/home.js` and `docs/index.html`
