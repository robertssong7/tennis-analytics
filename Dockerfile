FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
# Reduce memory fragmentation — critical for Railway 512MB limit
ENV MALLOC_ARENA_MAX=2
ENV PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY modules/ modules/

# Copy only the runtime model/data files
COPY models/ensemble/xgb_model.pkl models/ensemble/xgb_model.pkl
COPY models/ensemble/lgb_model.pkl models/ensemble/lgb_model.pkl
COPY models/ensemble/stacked_ensemble.pkl models/ensemble/stacked_ensemble.pkl
COPY models/ensemble/stacked_meta.json models/ensemble/stacked_meta.json
COPY models/ensemble/ensemble_summary.json models/ensemble/ensemble_summary.json
COPY data/processed/glicko2_state.pkl data/processed/glicko2_state.pkl
COPY data/processed/player_attributes_v2.pkl data/processed/player_attributes_v2.pkl
COPY data/processed/matchup_grid.json data/processed/matchup_grid.json
COPY data/processed/supplemental_matches_2025_2026.csv data/processed/supplemental_matches_2025_2026.csv
COPY data/processed/live_tournament.json data/processed/live_tournament.json
COPY data/player_headshots.json data/player_headshots.json
COPY data/court_speed.csv data/court_speed.csv

EXPOSE 8000

# Railway sets PORT env var. --timeout-keep-alive 120 allows slow model loading.
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-keep-alive 120"]
