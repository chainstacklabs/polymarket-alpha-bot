# alphapoly

Polymarket alpha detection: finds conditional probability arbitrage across related prediction markets.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
- [Node.js](https://nodejs.org/) 18+ — for frontend

## Setup

```bash
uv sync
cp .env.example .env  # Add OPENROUTER_API_KEY
```

## Usage

### Run Pipeline

Two options:

**CLI:**
```bash
uv run poly run          # Sync new events only
uv run poly run --full   # Reprocess all events
uv run poly run state    # Check pipeline status
```

**UI:** Start the frontend (see below), go to Pipeline tab. Use "Reprocess All Events" for first run, "Sync New Events" for subsequent updates.

### Start Services

```bash
uv run poly serve        # API server at http://localhost:8000
cd frontend && npm i && npm run dev  # UI at http://localhost:3000
```

## Experiments

Standalone scripts in `experiments/` for testing ideas before porting to `core/`.

```bash
uv run python experiments/01_fetch_events.py
```

## Output

Results in `data/_live/`:
- `opportunities.json` — detected alpha (price discrepancies between related markets)
- `events.json` — processed market data
- `graph.json` — entity relationship graph
