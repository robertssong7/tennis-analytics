# Stage 1: build wheels
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# Stage 2: runtime image
FROM python:3.12-slim

WORKDIR /app

ENV MALLOC_ARENA_MAX=2
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Application source
COPY src/ src/
COPY modules/ modules/

# Runtime model and data assets
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

# Pre-bake parsed_points.parquet from the public assets bucket so cold-start
# does not depend on S3 at runtime. Retry once with curl, fail the build on
# non-2xx so a broken parquet never silently ships.
RUN mkdir -p data/processed && \
    curl --fail --location --silent --show-error --retry 3 --retry-delay 5 \
        --max-time 120 \
        -o data/processed/parsed_points.parquet \
        https://tennisiq-data-assets.s3.us-east-1.amazonaws.com/parsed_points.parquet \
    && ls -lh data/processed/parsed_points.parquet

EXPOSE 8000

CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-keep-alive 120 --workers 1"]
