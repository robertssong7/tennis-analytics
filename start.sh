#!/bin/sh
# Only install if packages aren't already present
python3 -c "import uvicorn" 2>/dev/null || pip3 install --user -r requirements.txt
export PATH="$HOME/.local/bin:$PATH"
python3 -m uvicorn src.api.main:app --host 0.0.0.0 --port 8080
