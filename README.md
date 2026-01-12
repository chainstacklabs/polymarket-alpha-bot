# alphapoly

Polymarket alpha detection platform: finds cross-market arbitrage and conditional probability mispricings across prediction markets.

![Dashboard Screenshot](assets/dashboard-screenshot.png)

## How It Works

The platform detects two types of alpha opportunities:

**Cross-Market Arbitrage** (Primary)
- Finds exhaustive outcome sets spanning DIFFERENT markets (same-market outcomes are skipped since Polymarket architecture doesn't allow same-market outcome probabilities to diverge from 100%)
- When positions covering all possible outcomes cost less than $1.00, that's a hedged profit opportunity
- Example: Market A "Will SpaceX launch Starship before July?" at 65% YES, Market B "Will Starship launch be delayed past July?" at 30% YES — these are logically opposite events from different markets, so buying YES on both (65% + 30% = 95%) covers all outcomes for 5% profit

**Conditional Dependencies**
- Builds a knowledge graph of causally related markets
- Computes implied conditional probabilities P(B|A) from the graph
- Detects when market prices diverge from model predictions
- Example: "Trump impeached" (12%) has a REQUIRES relation to "Trump resigns" (6%) — if impeachment is a prerequisite for resignation, but resignation is priced lower than impeachment, the model flags this as underpriced

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
uv run poly serve               # API server at http://localhost:8000
cd frontend && npm i && npm run dev  # UI at http://localhost:3000
```

Go to Pipeline tab:
- "Run Demo" — quick test with 50 events
- "Sync New Events" — process only new events
- "Reprocess All Events" — full reprocess

The dashboard (`http://localhost:3000`) shows:
- **Cross-Market Arbitrage** — Primary section for arbitrage opportunities
- **Event Dependencies** — Conditional probability mispricings
- **Live Prices** — Real-time WebSocket price updates
- **Pipeline Status** — Step-by-step progress monitoring

Navigate to Opportunities tab for detailed views with filtering by type.

**CLI:**
```bash
uv run poly run          # Sync new events only
uv run poly run --full   # Reprocess all events
uv run poly reset        # Clear pipeline state
```

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     core/runner.py (14 steps)                   │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch Events        → Polymarket API                        │
│  2. Identify New Events → Compare with existing state           │
│  3. Load ML Models      → GLiNER, embedder, LLM client          │
│  4. Prepare NLP Data    → Clean text, extract markets           │
│  5. Extract Entities    → GLiNER2 NER                           │
│  6. Extract Semantics   → LLM event parsing                     │
│  7. Generate Embeddings → Sentence transformers                 │
│  8. Enrich Quality      → Negation detection, flags             │
│  9. Find Candidate Pairs→ FAISS similarity + entity blocking    │
│ 10. Classify Structural → Rule-based relation classification    │
│ 11. Classify Causal     → LLM causal inference                  │
│ 12. Build Graph         → NetworkX knowledge graph              │
│ 13. Detect Alpha        → Conditional probability opportunities │
│ 14. Detect Arbitrage    → Cross-market exhaustive sets          │
└─────────────────────────────────────────────────────────────────┘
```

## Experiments

Standalone scripts in `experiments/` for testing ideas before porting to `core/`.

```bash
uv run python experiments/01_fetch_events.py
```

## Cheatsheet

| Acronym | Full Name | Why We Need It |
|---------|-----------|----------------|
| **NER** | Named Entity Recognition | Extract people, organizations, dates from text to find related markets |
| **GLiNER** | Generalist and Lightweight NER | Fast, accurate entity extraction without heavy GPU requirements |
| **FAISS** | Facebook AI Similarity Search | Efficiently find similar events among thousands using vector embeddings |
| **P(B\|A)** | Conditional Probability | Probability of B given A — core metric for detecting mispricings |


## API Endpoints

### Data
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/data/opportunities` | GET | Alpha opportunities (supports `?type=arbitrage\|conditional`) |
| `/data/graph` | GET | Knowledge graph (nodes + edges) |
| `/data/events` | GET | All processed events |
| `/data/entities` | GET | Extracted entities |
| `/data/relations` | GET | Event relations |
| `/data/runs` | GET | Pipeline run history |

### Pipeline
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/pipeline/status` | GET | Pipeline state overview |
| `/pipeline/steps` | GET | Current step progress |
| `/pipeline/run` | POST | Trigger pipeline run |
| `/pipeline/run/production` | POST | Production run with options |
| `/pipeline/reset` | POST | Clear pipeline state |

### Prices
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/prices/current` | GET | Current cached prices |
| `/prices/ws` | WS | Live price updates |