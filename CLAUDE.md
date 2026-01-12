# CLAUDE.md

> Polymarket alpha detection platform: cross-market arbitrage and conditional probability mispricings via ML/NLP pipeline, REST API, and web dashboard.

## Project Structure

```
alphapoly-v1/
├── experiments/     # R&D scripts - standalone, exploratory (NN_name.py format)
├── core/            # Production pipeline - reusable steps, state management
│   ├── runner.py    # Main pipeline orchestrator
│   ├── state.py     # SQLite state management, _live/ exports
│   ├── models.py    # Singleton model loaders (GLiNER, embedder, LLM)
│   └── steps/       # Pipeline steps (fetch, entities, relations, alpha, arbitrage, etc.)
├── server/          # FastAPI backend - REST API, WebSocket prices
├── cli/             # Minimal Typer CLI - automation only (run, reset, serve)
├── frontend/        # Next.js dashboard - primary UI
└── data/            # Pipeline outputs (gitignored)
    └── _live/       # Production state (events.json, graph.json, opportunities.json)
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

**Incremental mode** (default): Only processes new events, merges into existing graph.
**Full mode** (`--full`): Resets state, reprocesses everything.

## Critical Rules

- **Use `uv` exclusively** — never pip, never conda
- **Use `polars`** — never pandas
- **Default LLM model:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Experiments are independent** — no shared modules between experiment scripts
- **Core uses singletons** — models loaded once via `core/models.py`

## Commands

```bash
# CLI (minimal - for automation/cron)
poly run              # Incremental pipeline
poly run --full       # Full reprocess
poly reset            # Clear state
poly serve            # API server (localhost:8000)

# Development
uv run python experiments/NN_name.py   # Run experiment script
uv add package                         # Add dependency
uvx ruff check . && uvx ruff format .  # Lint & format
```

## Experiment Script Structure

### Naming: `NN_name.py` → `data/NN_name/<timestamp>/`

```
experiments/01_fetch_events.py  →  data/01_fetch_events/20251229_151059/
experiments/02_prepare_nlp.py   →  data/02_prepare_nlp/20251229_160000/
```

### Template

```python
"""One-line description of what this script does."""

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = DATA_DIR / "01_script_name"
API_ENDPOINT = "https://..."
TIMEOUT_SECONDS = 30
# All tunables here, UPPER_CASE

# =============================================================================
# MAIN LOGIC
# =============================================================================
```

### Output Requirements

- Write to `data/NN_name/<timestamp>/`
- Save as formatted JSON
- Include `summary.json` with run stats and config snapshot

## Code Style

### DO

- Type hints on all functions
- `pathlib.Path` for file paths
- f-strings for formatting
- `httpx` for HTTP (async preferred)
- Specific exceptions with context
- `loguru` for logging (production), `logging` for experiments
- Fail fast on bad inputs

### DON'T

- Bare `except:` clauses
- Hardcoded values in logic
- Long functions (split them)
- Over-engineer — KISS, YAGNI

## API Endpoints

### Data
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/data/opportunities` | GET | Alpha opportunities (`?type=arbitrage\|conditional`) |
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

## Environment

```bash
# .env (gitignored)
OPENROUTER_API_KEY=sk-...
```

## Git

- Commit format: `<type>: <description>`
- Types: `feat`, `fix`, `docs`, `refactor`, `chore`
- No Claude signatures in commits
- Never commit: API keys, `/data` contents, large files
- **Temporary documentation:** Use `.local.md` extension for temporary docs and do not commit them
