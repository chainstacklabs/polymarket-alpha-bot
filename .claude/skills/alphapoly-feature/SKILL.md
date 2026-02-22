---
name: Adding Alphapoly Features
description: Guides feature development in the alphapoly codebase following stack conventions (uv, polars, FastAPI+Next.js). Use whenever adding, building, or modifying any feature — including new API endpoints, pipeline steps, UI pages, WebSocket services, or any other backend/frontend work. If the user says "add X", "build X", "implement X", "make X work", "create an endpoint", "wire up", "hook up", or "connect X to Y", use this skill. For quick prototypes, consider alphapoly-experiment instead.
---

# Adding Alphapoly Features

## Key Conventions
- **Backend:** Python + FastAPI, `uv` only (never pip/conda), `polars` (never pandas)
- **Frontend:** Next.js App Router, TypeScript
- **Default LLM:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Run Python from:** `backend/` directory

## Workflow

1. **Implement** — follow patterns in neighboring files; see Common Touch Points below
2. **Lint** — `make lint` auto-fixes backend (ruff) and frontend (prettier + eslint)
3. **Typecheck** — `cd frontend && npm run typecheck` (report-only; fix all errors before committing)
4. **Verify** — start `make dev` and manually test the feature works (check API responses, UI renders, WebSocket connects)
5. **Commit** — `type: description` (feat/fix/refactor/chore)

## Common Touch Points

**New API endpoint:**
- Add router: `backend/server/routers/<name>.py`
- Register in: `backend/server/main.py`
- Add TS types: `frontend/types/`
- For complex logic, add a service in `backend/server/` (follow `portfolio_service.py` pattern)

**New pipeline step:**
- Add to: `backend/core/steps/`
- Wire into: `backend/core/runner.py`
- For LLM/ML model access: use singletons in `backend/core/models.py`

**New domain feature (trading, wallet, positions):**
- Add subdirectory: `backend/core/<domain>/` with `manager.py` and/or `service.py`
- Add router: `backend/server/routers/<domain>.py`
- Follow existing pattern in `core/positions/`, `core/wallet/`, `core/trading/`

**New UI page:**
- Add: `frontend/app/<page>/page.tsx`
- Link from: `frontend/components/Sidebar.tsx`
- API/tier config: `frontend/config/` (`api-config.ts`, `tier-config.ts`)

**WebSocket service:**
- Backend: `backend/server/routers/` (follow `portfolio_prices.py` pattern)
- Frontend hook: `frontend/hooks/`
- Note: `frontend/server.js` proxies `/api/*` and `/ws/*` to the backend
