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

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENROUTER_API_KEY` | API key for LLM inference (event semantics, causal classification). Free models work fine — we use `xiaomi/mimo-v2-flash:free` | required |
| `POLYMARKET_TAG` | Comma-separated Polymarket tags (OR logic). E.g., `politics` or `politics,sports` | `politics` |

## Usage

### Run Pipeline

**UI (recommended):**
```bash
uv run poly serve        # API server at http://localhost:8000
cd frontend && npm i && npm run dev  # UI at http://localhost:3000
```

Go to Pipeline tab:
- "Run Demo" — quick test with 20 events
- "Sync New Events" — process only new events
- "Reprocess All Events" — full reprocess

**CLI:**
```bash
uv run poly run          # Sync new events only
uv run poly run --full   # Reprocess all events
uv run poly run state    # Check pipeline status
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
