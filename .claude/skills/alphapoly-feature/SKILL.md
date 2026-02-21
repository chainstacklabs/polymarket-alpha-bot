---
name: alphapoly-feature
description: Use when adding a new feature, endpoint, pipeline step, or UI page to the alphapoly codebase
---

# Alphapoly Feature Development

## Key Conventions
- **Backend:** Python + FastAPI, `uv` only (never pip/conda), `polars` (never pandas)
- **Frontend:** Next.js App Router, TypeScript
- **Default LLM:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Run Python from:** `backend/` directory

## Workflow

1. **Understand scope** — read CLAUDE.md, explore relevant files
2. **Identify touch points** — see Common Touch Points below
3. **Implement** — follow patterns in neighboring files
4. **Lint** — `make lint`, fix all issues before committing
5. **Commit** — `type: description` (feat/fix/refactor/chore)

## Common Touch Points

**New API endpoint:**
- Add router: `backend/server/routers/<name>.py`
- Register in: `backend/server/main.py`
- Add TS types: `frontend/types/`

**New pipeline step:**
- Add to: `backend/core/steps/`
- Wire into: `backend/core/runner.py`

**New UI page:**
- Add: `frontend/app/<page>/page.tsx`
- Link from: `frontend/components/Sidebar.tsx`

**WebSocket service:**
- Backend: `backend/server/routers/` (follow `portfolio_prices.py` pattern)
- Frontend hook: `frontend/hooks/`
