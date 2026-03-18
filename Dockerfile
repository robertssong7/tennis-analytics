FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY modules/ modules/

# Copy only the runtime model/data files
COPY models/ensemble/xgb_model.pkl models/ensemble/xgb_model.pkl
COPY models/ensemble/lgb_model.pkl models/ensemble/lgb_model.pkl
COPY models/ensemble/stacked_ensemble.pkl models/ensemble/stacked_ensemble.pkl
COPY data/processed/glicko2_state.pkl data/processed/glicko2_state.pkl
COPY data/processed/player_attributes_v2.pkl data/processed/player_attributes_v2.pkl
COPY data/processed/matchup_grid.json data/processed/matchup_grid.json

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
