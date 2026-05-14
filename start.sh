#!/bin/sh
# App Runner reinstalls deps on every cold start (build/runtime containers
# are separate). The skip-if-present check handles warm restarts.
set -e

# Probe multiple modules: earlier versions checked only uvicorn, so a
# partial install (uvicorn present, xgboost missing) would skip pip and
# then fail at engine load.
NEED_INSTALL=0
for mod in uvicorn fastapi xgboost lightgbm pandas pyarrow boto3 psycopg2; do
    python3 -c "import $mod" 2>/dev/null || NEED_INSTALL=1
done

if [ "$NEED_INSTALL" = "1" ]; then
    pip3 install --user --no-cache-dir --prefer-binary --quiet -r requirements.txt
    python3 -m compileall -q -j 0 "$HOME/.local/lib" 2>/dev/null || true
fi

export PATH="$HOME/.local/bin:$PATH"

# exec so App Runner SIGTERM reaches uvicorn directly during scale-down.
exec python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8080
