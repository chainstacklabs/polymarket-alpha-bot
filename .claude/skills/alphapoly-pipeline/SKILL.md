---
name: alphapoly-pipeline
description: Use when running, debugging, or managing the alphapoly ML pipeline
---

# Alphapoly Pipeline

## Quick Run
```bash
make pipeline         # Incremental (new groups only, uses cache)
make pipeline-full    # Full reprocess (resets all state)
```

## With Model Overrides
```bash
cd backend && uv run python -c "
from core.runner import run
run(
    implications_model='openai/gpt-4o-mini',
    validation_model='openai/gpt-4o',
)"
```

## Pipeline Steps
1. Fetch events from Polymarket
2. Build market groups
3. Detect new groups (incremental check)
4. Extract implications (LLM, cached)
5. Expand to market-level pairs
6. Validate pairs (LLM, cached)
7. Build portfolios with tier metrics
8. Export to `data/_live/`

## Seed Data
```bash
make export-seed    # Save current state as seed
make import-seed    # Reset DB and import seed (resets state)
```

## Troubleshooting
- LLM errors → verify `OPENROUTER_API_KEY` in `.env`
- Stale state → `make pipeline-full` to reprocess everything
- Partial outputs → check `data/_live/` for what was exported
```
