# CLAUDE.md

> Polymarket alpha detection platform: ML/NLP pipeline, REST API, and web dashboard.

## Project Structure

```
alphapoly-v1/
├── experiments/     # R&D scripts - standalone, exploratory (NN_name.py format)
├── core/            # Production pipeline - reusable steps, state management
│   ├── runner.py    # Main pipeline orchestrator
│   ├── state.py     # SQLite state management, _live/ exports
│   ├── models.py    # Singleton model loaders (GLiNER, embedder, LLM)
│   └── steps/       # Pipeline steps (fetch, prepare, entities, etc.)
├── server/          # FastAPI backend - REST API, WebSocket prices
├── cli/             # Minimal Typer CLI - automation only (run, reset, serve)
├── frontend/        # Next.js dashboard - primary UI
└── data/            # Pipeline outputs (gitignored)
    └── _live/       # Production state (events.json, graph.json, opportunities.json)
```

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     core/runner.py                              │
├─────────────────────────────────────────────────────────────────┤
│  1. fetch_events()      → Polymarket API                        │
│  2. prepare_nlp_data()  → Clean text, extract markets           │
│  3. extract_entities()  → GLiNER2 NER                           │
│  4. extract_semantics() → LLM event parsing                     │
│  5. embed_events()      → Sentence transformers                 │
│  6. enrich_quality()    → Negation detection, flags             │
│  7. block_candidates()  → FAISS similarity blocking             │
│  8. classify_struct()   → Rule-based relation classification    │
│  9. classify_causal()   → LLM causal inference                  │
│ 10. build_graph()       → NetworkX knowledge graph              │
│ 11. detect_alpha()      → Conditional probability arbitrage     │
│ 12. export_live()       → Write to data/_live/                  │
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

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/data/opportunities` | GET | Alpha opportunities |
| `/data/graph` | GET | Knowledge graph |
| `/data/events` | GET | All events |
| `/data/entities` | GET | Extracted entities |
| `/pipeline/status` | GET | Pipeline state |
| `/pipeline/run/production` | POST | Trigger pipeline run |
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
