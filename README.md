# TennisIQ — 538-Style Tennis Analytics

## Quick Start (existing Node.js prototype)

Install Bun: `curl -fsSL https://bun.sh/install | bash`

Run the server: `npm run run-server` → Express server on port 3001

Configure `.env` from `.env.example` with your database credentials.

---

## TennisIQ v2 Architecture

The new platform (being built on this branch) is:
- **Python FastAPI** backend (`src/api/main.py`)
- **Next.js** frontend (`frontend/`)
- **PostgreSQL** database (`schema.sql`)
- **Elo engine** (`src/elo/elo_engine.py`)
- **Feature engine** (`feature_engine.py`) — the only agent-modifiable file
- **Evaluation harness** (`evaluate.py`) — locked

### Python Setup

```bash
pip install -r requirements.txt
```

Required `.env` keys:
```
DATABASE_URL=postgresql://...
AWS_ACCESS_KEY_ID=...          # AWS IAM credentials for Bedrock
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-2
ANTHROPIC_API_KEY=...           # Fallback if Bedrock unavailable
```

See `BLOCKERS.md` for credential setup instructions.

### Build Order

Run phases in sequence:

```bash
# Phase 0 — Environment (done)
python3 src/bedrock_client.py          # Test AI connection

# Phase 1 — Data Pipeline
python3 scripts/data_pipeline.py --phase init

# Phase 2 — Elo Engine
python3 src/elo/elo_engine.py --validate

# Phase 3 — Feature Engine
python3 feature_engine.py --surface hard --validate

# Phase 4 — Evaluation Harness (requires Phase 1-3)
python3 scripts/data_pipeline.py --phase splits
python3 evaluate.py --surface hard

# Phase 5 — Models
# (train scripts in models/ directory)

# Phase 6 — FastAPI Server
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Phase 7 — Frontend
cd frontend && npm install && npm run dev
```

---

## Overnight Runs

**Prerequisites:**
1. Plug laptop into charger before starting
2. Run: `bash scripts/overnight_setup.sh`
3. Place laptop on hard flat surface — not bed or couch
4. Close all apps except terminal

**Run:**
```bash
claude --continue < program.md
```

**Duration:** 4-6 hours, 12-18 experiments at ~20-30 min each

**Morning:**
```bash
python3 experiments/morning_report.py   # Read summary
bash scripts/overnight_teardown.sh      # Restore normal sleep
```

**Cost:** ~$1-3 per overnight session via AWS Bedrock (Haiku 4.5)

Note: M2 Pro thermal management is automatic. The chip will throttle
before damage. Airflow from a hard surface is the only manual step required.

---

## Data Sources
- [Jeff Sackmann Match Charting Project](https://github.com/JeffSackmann/tennis_MatchChartingProject)
- [Jeff Sackmann ATP Results](https://github.com/JeffSackmann/tennis_atp)
- Weather: Open-Meteo archive API (no key required)
- Existing `/db/` and `/docs/data/` pre-computed analytics
