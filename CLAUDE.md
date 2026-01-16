# CLAUDE.md

> Polymarket alpha detection platform: cross-market arbitrage and conditional probability mispricings via LLM pipeline, REST API, and web dashboard.

## Project Structure

```
alphapoly/
├── backend/                 # Python backend (FastAPI + ML pipeline)
│   ├── core/                # Pipeline logic
│   │   ├── runner.py        # Main orchestrator
│   │   ├── state.py         # SQLite state, _live/ exports
│   │   ├── paths.py         # Shared path constants
│   │   └── steps/           # Pipeline steps
│   ├── server/              # FastAPI app
│   │   ├── main.py          # App entrypoint
│   │   └── routers/         # API routes
│   ├── pyproject.toml
│   └── uv.lock
│
├── frontend/                # Next.js dashboard
│   ├── app/                 # Pages (App Router)
│   ├── components/
│   ├── hooks/
│   └── package.json
│
├── experiments/             # Standalone scripts, tests, scratch work
├── data/                    # Pipeline outputs (gitignored)
├── Makefile                 # Common commands
└── .env                     # Environment variables
```

## Quick Start

```bash
make install    # Install backend + frontend deps
make dev        # Start both servers (backend :8000, frontend :3000)

# Or run separately
make backend    # API only
make frontend   # UI only
make check-node # Verify Node.js is detected
```

> **Note**: Makefile auto-detects Node.js from fnm, nvm, volta, or system paths.

## Commands

```bash
make install        # Install all dependencies
make dev            # Start backend + frontend
make backend        # Backend only (localhost:8000)
make frontend       # Frontend only (localhost:3000)
make pipeline       # Run ML pipeline (incremental)
make pipeline-full  # Run ML pipeline (full)
make lint           # Lint + format all code
make check-node     # Verify Node.js is available
make clean          # Remove build artifacts
```

## Critical Rules

- **Use `uv` exclusively** — never pip, never conda
- **Use `polars`** — never pandas
- **Default LLM:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Experiments are independent** — no shared modules
- **Run Python commands from `backend/`**

## Alpha Detection

**Goal**: Find position combinations across DIFFERENT questions where total cost < $1.00 guarantees $1.00 payout.

**Ignore**: Intra-event sibling arbitrage — Polymarket handles this.

## API Endpoints

### Data
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/data/portfolios` | GET | Covering portfolios (alpha) with live prices |

### Pipeline
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/pipeline/status` | GET | Pipeline state & run history |
| `/pipeline/run/production` | POST | Trigger pipeline (full/incremental/demo) |
| `/pipeline/reset` | POST | Clear pipeline state |

### Prices
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/prices/current` | GET | Cached prices (REST fallback) |
| `/prices/ws` | WS | Live price updates |

### Portfolios
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/portfolios/ws` | WS | Real-time portfolio updates with filtering |

### System
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |

## Code Style

**DO**: Type hints, `pathlib.Path`, f-strings, `httpx`, `loguru`

**DON'T**: Bare `except:`, hardcoded values, long functions, over-engineering

## Environment

```bash
# .env (at project root, gitignored)
OPENROUTER_API_KEY=sk-...
```

## Git

- Commit format: `<type>: <description>`
- Types: `feat`, `fix`, `docs`, `refactor`, `chore`
- Never commit: API keys, `/data` contents
