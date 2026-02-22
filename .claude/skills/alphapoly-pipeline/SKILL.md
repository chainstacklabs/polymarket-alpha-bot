---
name: Running the Alphapoly Pipeline
description: Runs, debugs, and manages the alphapoly ML pipeline with make commands and model overrides. Use when running, resetting, or troubleshooting the pipeline. Also use when the user says "refresh data", "update portfolios", "fetch markets", "reprocess", "run the pipeline", or wants to regenerate portfolio opportunities from scratch.
---

# Running the Alphapoly Pipeline

The pipeline fetches Polymarket events, groups related markets, extracts logical implications via LLM, and builds hedged portfolios. Output lands in `data/_live/`.

## Quick Run
```bash
make pipeline         # Incremental (new groups only, uses cache)
make pipeline-full    # Full reprocess (resets all state)
```

## With Model Overrides
Run from the `backend/` directory:
```bash
uv run python -c "
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

Price updates happen separately via the WebSocket service when the backend is running.

## Seed Data
```bash
make export-seed    # Save current state as seed
make import-seed    # Reset DB and import seed (resets state)
```

## Verify

After running, confirm the pipeline produced output:
- Check `data/_live/` for output files
- Or start the backend (`make backend`) and hit `GET /data/portfolios` to see generated portfolios

## Troubleshooting
- Crashes on startup → verify `.env` has all three required vars: `OPENROUTER_API_KEY`, `IMPLICATIONS_MODEL`, `VALIDATION_MODEL`
- LLM errors → check `OPENROUTER_API_KEY` is valid; try a different model via override
- Stale state → `make pipeline-full` to reprocess everything
- Partial outputs → check `data/_live/` then re-run `make pipeline` to resume incrementally
