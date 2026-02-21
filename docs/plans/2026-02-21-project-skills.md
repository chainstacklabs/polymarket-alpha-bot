# Project Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create 6 project-level skills in `.claude/skills/` that contributors can use for feature development, experiments, pipeline management, and semi-automatic trading.

**Architecture:** Each skill is a directory with a `SKILL.md` file following the Claude Code skills spec (YAML frontmatter + markdown body). Skills are committed to the repo so any contributor gets them automatically.

**Tech Stack:** Markdown, Claude Code skills system, FastAPI backend at `localhost:8000`

---

### Task 1: alphapoly-feature

**Files:**
- Create: `.claude/skills/alphapoly-feature/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-feature
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-feature
description: Use when adding a new feature to the alphapoly codebase - guides through exploration and implementation following project conventions
---

# Alphapoly Feature Development

## Key Conventions
- **Backend:** Python + FastAPI, `uv` only (never pip/conda), `polars` (never pandas)
- **Frontend:** Next.js App Router, TypeScript
- **Default LLM:** `xiaomi/mimo-v2-flash:free` via OpenRouter
- **Run Python from:** `backend/` directory

## Workflow

1. **Understand scope** — read CLAUDE.md, explore relevant files
2. **Identify touch points** — see Common Patterns below
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
- Backend: `backend/server/` (follow portfolio_prices.py pattern)
- Frontend hook: `frontend/hooks/`
```

**Step 3: Verify**
```bash
ls .claude/skills/alphapoly-feature/
# Expected: SKILL.md
```

**Step 4: Commit**
```bash
git add .claude/skills/alphapoly-feature/
git commit -m "feat(skills): add alphapoly-feature skill"
```

---

### Task 2: alphapoly-experiment

**Files:**
- Create: `.claude/skills/alphapoly-experiment/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-experiment
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-experiment
description: Use when creating a new standalone experiment script in the alphapoly experiments/ directory
---

# Alphapoly Experiment

Scaffold a new standalone script in `experiments/`.

## Rules
- **Completely standalone** — no imports from `backend/` modules
- **Sequential numbering** — next after existing `experiments/08_*.py`
- **uv inline dependencies** — declare deps in script header

## Template

```python
# /// script
# dependencies = [
#   "httpx",
#   "polars",
# ]
# ///
"""What this experiment explores."""

import httpx
import polars as pl

# ... experiment code
```

## Naming
`experiments/NN_short_description.py` — e.g. `experiments/09_test_llm_model.py`

## Running
```bash
cd backend && uv run ../experiments/NN_script.py
```
```

**Step 3: Commit**
```bash
git add .claude/skills/alphapoly-experiment/
git commit -m "feat(skills): add alphapoly-experiment skill"
```

---

### Task 3: alphapoly-pipeline

**Files:**
- Create: `.claude/skills/alphapoly-pipeline/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-pipeline
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-pipeline
description: Use when running, debugging, or managing the alphapoly ML pipeline
---

# Alphapoly Pipeline

## Quick Run
```bash
make pipeline         # Incremental (new groups only, uses cache)
make pipeline-full    # Full reprocess (resets all state)
```

## With Model Overrides
```bash
cd backend && uv run python -c "
from core.runner import run
run(
    implications_model='openai/gpt-4o-mini',
    validation_model='openai/gpt-4o',
)"
```

## Pipeline Steps
1. Fetch events from Polymarket
2. Build market groups
3. Detect new groups (incremental check)
4. Extract implications (LLM, cached)
5. Expand to market-level pairs
6. Validate pairs (LLM, cached)
7. Build portfolios with tier metrics
8. Export to `data/_live/`

## Seed Data
```bash
make export-seed    # Save current state as seed
make import-seed    # Reset DB and import seed (resets state)
```

## Troubleshooting
- LLM errors → verify `OPENROUTER_API_KEY` in `.env`
- Stale state → `make pipeline-full` to reprocess everything
- Partial outputs → check `data/_live/` for what was exported
```

**Step 3: Commit**
```bash
git add .claude/skills/alphapoly-pipeline/
git commit -m "feat(skills): add alphapoly-pipeline skill"
```

---

### Task 4: alphapoly-portfolios

**Files:**
- Create: `.claude/skills/alphapoly-portfolios/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-portfolios
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-portfolios
description: Use when listing or browsing current alphapoly portfolio opportunities
---

# Alphapoly Portfolios

Fetch and display current portfolio opportunities from the running backend.

## Requires
Backend running at `localhost:8000` — start with `make backend`

## Fetch
```bash
curl -s http://localhost:8000/data/portfolios | python3 -m json.tool
```

Fetch the portfolios and display a formatted table with:
- `pair_id` — identifier for entering/exiting positions
- Tier (1=HIGH ≥95%, 2=GOOD 90-95%, 3=MODERATE 85-90%)
- Target and cover market questions
- Coverage % and total cost (USDC)
- Expected profit

## Tier Reference
| Tier | Label | Coverage | Description |
|------|-------|----------|-------------|
| 1 | HIGH | ≥95% | Near-arbitrage |
| 2 | GOOD | 90-95% | Strong hedge |
| 3 | MODERATE | 85-90% | Decent hedge |
```

**Step 3: Commit**
```bash
git add .claude/skills/alphapoly-portfolios/
git commit -m "feat(skills): add alphapoly-portfolios skill"
```

---

### Task 5: alphapoly-enter-position

**Files:**
- Create: `.claude/skills/alphapoly-enter-position/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-enter-position
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-enter-position
description: Use when entering a new covered pair position in alphapoly - guides through portfolio selection, estimation, and trade execution
---

# Enter Position

Execute a covered pair trade on Polymarket.

## Prerequisites
- Backend running: `make backend`
- Wallet unlocked (via `POST /wallet/unlock` or frontend)

## Flow

1. **List portfolios** — `GET /data/portfolios`, show with pair_ids
2. **User picks** — select portfolio by pair_id
3. **Set amount** — USDC amount per position side
4. **Estimate** — show total cost vs wallet balance
5. **User confirms** — explicit approval required
6. **Execute** — `POST /trading/buy-pair`
7. **Record** — `POST /positions` to track entry

## Key Calls

```bash
# Check wallet balance
curl -s http://localhost:8000/wallet/balances

# Execute trade
curl -s -X POST http://localhost:8000/trading/buy-pair \
  -H "Content-Type: application/json" \
  -d '{
    "pair_id": "<pair_id>",
    "target_market_id": "<id>",
    "target_position": "YES",
    "cover_market_id": "<id>",
    "cover_position": "YES",
    "amount_per_position": 10.0
  }'
```

## Safety
Always verify estimate and wallet balance before executing. Never execute without explicit user confirmation. Check `sufficient_balance` from estimate response.
```

**Step 3: Commit**
```bash
git add .claude/skills/alphapoly-enter-position/
git commit -m "feat(skills): add alphapoly-enter-position skill"
```

---

### Task 6: alphapoly-exit-position

**Files:**
- Create: `.claude/skills/alphapoly-exit-position/SKILL.md`

**Step 1: Create directory**
```bash
mkdir -p .claude/skills/alphapoly-exit-position
```

**Step 2: Write SKILL.md**

```markdown
---
name: alphapoly-exit-position
description: Use when exiting or managing an open position in alphapoly - sell tokens or merge resolved pairs
---

# Exit Position

Sell or merge tokens from an open alphapoly position.

## Prerequisites
- Backend running: `make backend`
- Wallet unlocked

## Flow

1. **List positions** — `GET /positions`, show open positions
2. **User picks** — select by position_id
3. **Choose action:**
   - Sell target side (wanted or unwanted token)
   - Sell cover side (wanted or unwanted token)
   - Merge YES+NO pair (if market resolved, recovers $1 per pair)
4. **User confirms** — show expected recovery amount
5. **Execute**

## Key Calls

```bash
# List open positions
curl -s http://localhost:8000/positions

# Sell a side
curl -s -X POST http://localhost:8000/positions/{position_id}/sell \
  -H "Content-Type: application/json" \
  -d '{"side": "target", "token_type": "wanted"}'

# Merge resolved pair
curl -s -X POST http://localhost:8000/positions/{position_id}/merge \
  -H "Content-Type: application/json" \
  -d '{"side": "target"}'
```

## Token Types
- `wanted` — the position you wanted (YES or NO based on your bet)
- `unwanted` — the other side token received from split
```

**Step 3: Commit**
```bash
git add .claude/skills/alphapoly-exit-position/
git commit -m "feat(skills): add alphapoly-exit-position skill"
```

---

### Task 7: Update CLAUDE.md to reference skills

**Files:**
- Modify: `CLAUDE.md`

Add a Skills section so contributors know these exist:

```markdown
## Skills

Project-level Claude Code skills in `.claude/skills/`:

| Skill | When to use |
|-------|-------------|
| `alphapoly-feature` | Adding a new feature |
| `alphapoly-experiment` | Creating a new experiment script |
| `alphapoly-pipeline` | Running or debugging the ML pipeline |
| `alphapoly-portfolios` | Browsing current portfolio opportunities |
| `alphapoly-enter-position` | Executing a covered pair trade |
| `alphapoly-exit-position` | Selling or merging an open position |
```

**Commit:**
```bash
git add CLAUDE.md
git commit -m "docs: add skills reference to CLAUDE.md"
```
