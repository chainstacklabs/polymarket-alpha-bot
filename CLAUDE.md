# CLAUDE.md

> Polymarket alpha detection platform. LLM pipeline groups related markets, extracts logical implications between groups, and builds covering portfolios (hedged positions via contrapositive logic). Web dashboard displays portfolios with real-time price tracking.

## Project Structure

```
alphapoly/
├── backend/           # FastAPI + ML pipeline (Python/uv)
│   ├── core/          # Pipeline: runner.py, state.py, steps/
│   └── server/        # API: main.py, routers/, WebSocket services
├── frontend/          # Next.js dashboard (App Router)
│   ├── app/           # Pages: /, /pipeline, /portfolios, /terminal
│   ├── components/    # React components
│   ├── hooks/         # Custom hooks
│   ├── types/         # TypeScript definitions
│   └── config/        # API and UI configuration
├── experiments/       # Standalone scripts (no shared modules)
├── data/              # Pipeline outputs (gitignored)
└── Makefile           # Dev commands
```

## Commands

```bash
# Development
make install        # Install all dependencies
make dev            # Start backend (:8000) + frontend (:3000)
make backend        # Backend only
make frontend       # Frontend only

# Pipeline
make pipeline       # Run ML pipeline (incremental)
make pipeline-full  # Run ML pipeline (full reprocess)

# Seed Data
make export-seed    # Export current state as seed
make import-seed    # Import seed data (resets DB)

# Quality
make lint           # Lint + format all code
make clean          # Remove build artifacts
```

## Critical Rules

- **Use `uv` exclusively** — never pip, never conda
- **Use `polars`** — never pandas
- **Default LLM:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Experiments are independent** — no shared modules
- **Run Python commands from `backend/`**

## API Overview

| Route | Description |
|-------|-------------|
| `GET /data/portfolios` | Covering portfolios with live prices |
| `GET /pipeline/status` | Pipeline state & run history |
| `POST /pipeline/run/production` | Trigger pipeline run |
| `POST /pipeline/reset` | Clear pipeline state |
| `WS /portfolios/ws` | Real-time portfolio updates (primary) |
| `GET /health` | Health check |

> Internal debug endpoints: `/prices/current`, `/prices/ws`

## Skills

Project-level Claude Code skills in `.claude/skills/`:

| Skill | When to use |
|-------|-------------|
| `alphapoly-feature` | Adding a new feature, endpoint, pipeline step, or UI page |
| `alphapoly-experiment` | Creating a new standalone script in `experiments/` |
| `alphapoly-pipeline` | Running, debugging, or managing the ML pipeline |
| `alphapoly-portfolios` | Listing current portfolio opportunities |
| `alphapoly-enter-position` | Executing a covered pair trade |
| `alphapoly-exit-position` | Exiting or managing an open position |

## Environment

```bash
# .env (project root, gitignored)
OPENROUTER_API_KEY=sk-...
```

## Git

- Format: `<type>: <description>` (feat, fix, docs, refactor, chore)
- Never commit: API keys, `/data` contents
