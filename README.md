# alphapoly

Polymarket alpha detection: finds conditional probability arbitrage across related prediction markets.

## Setup

```bash
uv sync
cp .env.example .env  # Add OPENROUTER_API_KEY
```

## Usage

```bash
# Run pipeline (fetches markets, extracts entities, builds graph, detects alpha)
uv run poly run

# Start API server
uv run poly serve
# API docs at http://localhost:8000/docs

# Commands
uv run poly run          # Incremental (new events only)
uv run poly run --full   # Full reprocess
uv run poly run state    # Check pipeline status
```

## Experiments

The `experiments/` folder contains standalone scripts extracted from the pipeline. Each script is self-contained and independent — feel free to debug, test, and modify them to experiment with different approaches. Once validated, changes can be ported to `core/`.

```bash
uv run python experiments/01_fetch_events.py
```

## UI

```bash
cd frontend && npm run dev
# Open http://localhost:3000
```

## Output

Results in `data/_live/`:
- `opportunities.json` — detected alpha (price discrepancies between related markets)
- `events.json` — processed market data
- `graph.json` — entity relationship graph
