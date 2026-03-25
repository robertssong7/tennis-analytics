FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt

# Reduce memory fragmentation (important for Railway's 8GB limit)
ENV MALLOC_ARENA_MAX=2

# Copy application code
COPY src/ src/
COPY modules/ modules/

# Copy model files
COPY models/ensemble/xgb_model.pkl models/ensemble/xgb_model.pkl
COPY models/ensemble/lgb_model.pkl models/ensemble/lgb_model.pkl
COPY models/ensemble/stacked_ensemble.pkl models/ensemble/stacked_ensemble.pkl
COPY models/ensemble/stacked_meta.json models/ensemble/stacked_meta.json
COPY models/ensemble/ensemble_summary.json models/ensemble/ensemble_summary.json

# Copy processed data
COPY data/processed/glicko2_state.pkl data/processed/glicko2_state.pkl
COPY data/processed/player_attributes_v2.pkl data/processed/player_attributes_v2.pkl
COPY data/processed/matchup_grid.json data/processed/matchup_grid.json
COPY data/processed/parsed_points.parquet data/processed/parsed_points.parquet
COPY data/processed/supplemental_matches_2025_2026.csv data/processed/supplemental_matches_2025_2026.csv
COPY data/processed/live_tournament.json data/processed/live_tournament.json

# Copy reference data
COPY data/player_headshots.json data/player_headshots.json
COPY data/court_speed.csv data/court_speed.csv
# Copy Sackmann match CSVs (needed for conditions, H2H, win/loss caches)
COPY data/sackmann/tennis_atp/ data/sackmann/tennis_atp/

# Copy cached headshots
COPY data/processed/headshots/ data/processed/headshots/

# Copy frontend (for serving static files if needed)
COPY frontend/ frontend/

EXPOSE 8000

# Railway sets PORT env var — must use it
CMD ["sh", "-c", "python -m uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
