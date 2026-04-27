# CLAUDE.md

> Polymarket alpha detection platform. LLM pipeline groups related markets, extracts logical implications, and builds covering portfolios (hedged positions via contrapositive logic). Next.js dashboard with real-time price tracking and position management.

## Polymarket V2 cutover (2026-04-28 11:00 UTC)

The bot supports both V1 (USDC.e collateral) and V2 (pUSD collateral) trading paths, gated by `POLYMARKET_V2_ENABLED`. Single source of truth: `core/feature_flags.v2_enabled()`.

**Phase 2 PR #49** (`feat/v2-flip-default`) flips the default unset → true at cutover. **DO NOT MERGE BEFORE 2026-04-28 11:00 UTC** — V2 books are empty pre-cutover.

Operator post-flip: re-run `WalletManager.set_approvals()` once (approvals are mode-gated). USDC.e holders wrap to pUSD via `experiments/trading/02_wrap_to_pusd.py`. V1 retirement (#37) is queued for ~2026-05-05 after a 1-week soak; the `v1-final` git tag at `94e89ce` is the permanent legacy reference.

## Commands

```bash
# Development
make install        # Install all deps (uv sync + npm install)
make dev            # Start backend (:8000) + frontend (:3000)
make backend        # Backend only
make frontend       # Frontend only
make stop           # Kill dev servers

# Pipeline
make pipeline       # Run ML pipeline (incremental)
make pipeline-full  # Full reprocess from scratch

# Seed Data
make export-seed    # Export current state as seed
make import-seed    # Import seed data (resets DB)

# Quality
make lint           # Auto-fix: ruff (backend) + prettier + eslint (frontend)
cd frontend && npm run typecheck  # TypeScript check (not in Makefile)
```

## Critical Rules

- **Use `uv` exclusively** — never pip, never conda
- **Use `polars`** — never pandas
- **Experiments are independent** — no imports from `backend/`
- **Run backend Python from `backend/`** — `cd backend && uv run ...`
- **`load_dotenv()` before imports** in `server/main.py` (E402 ignored by ruff for this file)

## Project Structure

```
backend/
├── core/           # Pipeline: runner.py, state.py, steps/, positions/, trading/, wallet/
└── server/         # FastAPI: main.py, routers/, WebSocket services
frontend/
├── app/            # Pages: /, /portfolios, /positions, /terminal
├── components/     # React components (positions/, terminal/)
├── hooks/          # Custom hooks (prices, keyboard, wallet, favorites)
├── server.js       # Custom Next.js server with WebSocket proxy
└── config/         # API URL resolution, tier display settings
experiments/        # Standalone scripts (no shared modules)
data/               # Pipeline outputs (gitignored)
```

## API Routes

| Route | Description |
|-------|-------------|
| `GET /data/portfolios` | Covering portfolios with live prices |
| `WS /portfolios/ws` | Real-time portfolio price updates |
| `GET /pipeline/status` | Pipeline state & run history |
| `POST /pipeline/run/production` | Trigger pipeline run |
| `POST /pipeline/reset` | Clear pipeline state |
| `GET,POST /positions` | Position CRUD |
| `POST /positions/{id}/sell` | Sell position tokens via CLOB FAK |
| `POST /positions/{id}/merge` | Merge complementary tokens (CTF mergePositions) |
| `POST /positions/{id}/exit` | Orchestrated sell + merge fallback |
| `POST /trading/buy-pair` | Execute covered pair trade |
| `POST /trading/buy-pair/estimate` | Estimate trade cost |
| `GET /wallet/status` | Wallet state (balances, approvals, V2-aware) |
| `POST /wallet/generate` | Generate new wallet (encrypted with passphrase) |
| `POST /wallet/import` | Import private key (encrypted with passphrase) |
| `POST /wallet/unlock` / `lock` | Decrypt key into memory / clear |
| `POST /wallet/approve-contracts` | Set all Polymarket approvals (V2-aware) |
| `GET /health` | Health check |

> Debug: `GET /prices/current`, `WS /prices/ws`

## Hooks

Claude hooks auto-run on every Write/Edit:

- **PreToolUse guard** — blocks edits to `.env`, `data/`, `*.key`, `*.pem`, `*.secret`
- **PostToolUse lint** — ruff format+check (Python), prettier+eslint (TypeScript/CSS). Unfixable errors surface as blocking messages.

## Skills

| Skill | When to use |
|-------|-------------|
| `alphapoly-feature` | Adding a feature, endpoint, pipeline step, or UI page |
| `alphapoly-experiment` | Creating a standalone script in `experiments/` |
| `alphapoly-pipeline` | Running, debugging, or managing the ML pipeline |
| `alphapoly-portfolios` | Listing current portfolio opportunities |
| `alphapoly-enter-position` | Executing a covered pair trade |
| `alphapoly-exit-position` | Exiting or managing an open position |

## Environment

```bash
# .env (copy from .env.example, gitignored)
OPENROUTER_API_KEY=sk-...          # Required: LLM access
IMPLICATIONS_MODEL=...             # Required: model for implication extraction
VALIDATION_MODEL=...               # Required: model for validation
POLYMARKET_TAG=politics             # Optional: market filter
MARKET_POLLING_ENABLED=false        # Optional: background polling
CHAINSTACK_NODE=https://...         # Optional: Polygon RPC for on-chain trades
POLYMARKET_V2_ENABLED=true         # Optional: V1/V2 path selector. Pre-#49 default false; post-#49 default true. Set =false explicitly to opt back to V1 (until V1 is removed in #37).
```

## Git

- Format: `<type>: <description>` (feat, fix, docs, refactor, chore)
- Never commit: API keys, `/data` contents, `.env`
