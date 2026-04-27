<img width="1200" alt="Labs" src="https://user-images.githubusercontent.com/99700157/213291931-5a822628-5b8a-4768-980d-65f324985d32.png">

<p>
 <h3 align="center">Chainstack is the leading suite of services connecting developers with Web3 infrastructure</h3>
</p>

<p align="center">
  • <a target="_blank" href="https://chainstack.com/">Homepage</a> •
  <a target="_blank" href="https://chainstack.com/protocols/">Supported protocols</a> •
  <a target="_blank" href="https://chainstack.com/blog/">Chainstack blog</a> •
  <a target="_blank" href="https://docs.chainstack.com/quickstart/">Blockchain API reference</a> • <br> 
  • <a target="_blank" href="https://console.chainstack.com/user/account/create">Start for free</a> •
</p>


# Alphapoly - Polymarket alpha detection platform

> ⚠️ **Polymarket V2 cutover: 2026-04-28 11:00 UTC.**
> After cutover, set `POLYMARKET_V2_ENABLED=true` in your `.env` and re-run `WalletManager.set_approvals()` once (approvals are mode-gated).
> If you hold USDC.e, wrap it to pUSD with [`experiments/trading/02_wrap_to_pusd.py`](experiments/trading/02_wrap_to_pusd.py) before trading.
> V1 code paths will be removed in **v2.0** (~2026-05-05). To pin to the legacy V1 stack: `git checkout v1-final`.

Find covering portfolios across correlated prediction markets using predefined rules and LLM decisions. The system detects relationships between markets, classifies them to identify hedging pairs, and tracks their prices. The platform offers a smooth UI for entering detected pairs when profit opportunities exist and tracking your positions.

For a good experience, you'll need to add an LLM from OpenRouter and an RPC node (see `.env.example`).



![Dashboard Screenshot](assets/dashboard-screenshot.png)

## How It Works

1. **Groups** - Fetches markets from Polymarket (e.g., "Presidential Election Winner")
2. **Implications** *(LLM)* - Extracts logical relationships between groups
3. **Validation** *(LLM)* - Validates implications at the individual market level
4. **Portfolios** - Computes cost and expected profit for validated pairs using live market prices
5. **Positions** - Tracks your purchased position pairs

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python automatically)
- **Node.js 18+** via [fnm](https://github.com/Schniz/fnm), nvm, or brew

## Quick Start

```bash
cp .env.example .env

# With make
make install && make dev

# Without make
cd backend && uv sync
cd frontend && npm install
cd backend && uv run python -m uvicorn server.main:app --port 8000 &
cd frontend && npm run dev
```

Dashboard: http://localhost:3000 · API: http://localhost:8000/docs

## Commands

**With make** (auto-detects fnm/nvm/volta):
```bash
make install    # Install deps
make dev        # Start both servers
make pipeline   # Run ML pipeline (incremental, also available in UI)
make lint       # Auto-fix: ruff + prettier + eslint
```

**Without make**:
```bash
# Backend
cd backend && uv sync
cd backend && uv run python -m uvicorn server.main:app --reload --port 8000

# Frontend
cd frontend && npm install
cd frontend && npm run dev
```

## Agentic Coding

This repo is configured for AI coding agents via the `.claude/` directory:

- **`CLAUDE.md`** — project context, commands, conventions, and API routes
- **`hooks/`** — auto-lint on edit, guard against writing secrets *(Claude Code only)*
- **`skills/`** — workflows for pipeline management, trading, and feature development

### Skills

The `.claude/skills/` directory contains [Agent Skills](https://agentskills.io/home) — an open standard for extending AI coding agents with reusable, modular capabilities. Each skill is a directory with a `SKILL.md` file (YAML frontmatter + natural-language instructions) that teaches an agent how to perform a domain-specific workflow.

| Skill | Purpose |
|-------|---------|
| `alphapoly-pipeline` | Run, debug, and manage the ML pipeline |
| `alphapoly-portfolios` | Fetch and display portfolio opportunities |
| `alphapoly-enter-position` | Execute a covered pair trade |
| `alphapoly-exit-position` | Exit or manage an open position |
| `alphapoly-feature` | Add features following stack conventions |
| `alphapoly-experiment` | Scaffold standalone experiment scripts |

To use the skills in this repo with a different agent, point it at `.claude/skills/` or copy the skill directories into the agent's expected location (e.g., `~/.codex/skills/` for Codex CLI).

### Instructions file

`CLAUDE.md` is read natively by [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and by [GitHub Copilot in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-instructions#_use-a-claudemd-file) (opt-in via `chat.useClaudeMdFile`). For broader cross-agent compatibility, [`AGENTS.md`](https://agents.md/) is also provided as a symlink to `CLAUDE.md` — an open format supported by Codex, Cursor, Copilot, and others.

## Experiments

Standalone research scripts (no imports from `backend/`). Three groups:

| Folder | Description |
|--------|-------------|
| [`experiments/`](experiments/) | Pipeline-step learning examples — fetch events, build groups, extract implications, validate, score portfolios, stream prices. Mirrors what `backend/core/runner.py` orchestrates, one stage per file. |
| [`experiments/trading/`](experiments/trading/) | Wallet + funding + position helpers. Generate a wallet, swap native USDC → USDC.e (legacy), wrap USDC.e → pUSD (Polymarket V2 collateral), buy a position, transfer tokens. Flag-aware: scripts read `POLYMARKET_V2_ENABLED` to mirror the backend's V1/V2 routing. |
| [`experiments/onchain-otc/`](experiments/onchain-otc/) | On-chain OTC trading without the CLOB — split/merge, P2P transfers, atomic escrow, NegRisk conversions, and intent-based settlement on an Anvil fork of Polygon. Forked-chain research; uses USDC.e collateral (the fork's frozen state predates Polymarket V2 / pUSD). |

---

**Disclaimer:** This software is provided as-is for educational and research purposes only. It is not financial advice. Trading prediction markets involves risk—you may lose money. Use at your own discretion.
