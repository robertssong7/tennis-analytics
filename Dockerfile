FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

# Reduce memory fragmentation
ENV MALLOC_ARENA_MAX=2

# Copy everything — .dockerignore excludes large/unnecessary files
COPY . .

EXPOSE 8000

# Railway sets PORT env var
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
