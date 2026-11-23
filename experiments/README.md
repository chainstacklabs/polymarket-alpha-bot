# Experiments

Standalone scripts for learning and prototyping. They don't import from `backend/`; each one copies the production logic it shows. They share the backend's dependencies, so run them from the repo root:

```bash
uv run --frozen --project backend experiments/<script>.py
```

## Pipeline, one step per script

Each step mirrors a stage of `backend/core/runner.py` and writes `data/<step>/<run>/`; the next step reads the latest run (or `--run <timestamp>`).

| Script | Production | What it does |
|---|---|---|
| `01_fetch_events.py` | `core/steps/fetch.py` | Fetch active events for a tag from Gamma |
| `02_build_groups.py` | `core/steps/groups.py` | Group markets by event, with partition type and resolution dates |
| `03_extract_implications.py` | `core/steps/implications.py` | LLM finds logical implications between groups, then derives covers (`--dry-run` prints the prompt) |
| `04_expand_pairs.py` | `core/steps/expand.py` | Expand group implications into market-level target/cover pairs |
| `05_validate_pairs.py` | `core/steps/validate.py`, `hedge_checks.py` | Rule checks (deadlines, both-lose number ladders, cost under $1), then LLM validation (`--dry-run` skips the LLM) |
| `06_find_portfolios.py` | `core/steps/portfolios.py` | Cost, coverage, fees, and tiers for valid pairs |
| `07_websocket_prices.py` | `server/clob_websocket.py` | Stream live order book prices for portfolio tokens |
| `08_new_market_polling.py` | `core/market_poller.py` | Poll Gamma for newly created events |

`03` and `05` call OpenRouter (`OPENROUTER_API_KEY`, `IMPLICATIONS_MODEL`, `VALIDATION_MODEL` in `.env`) and cost money; the other steps are free.

## Other folders

| Folder | What's there |
|---|---|
| [`trading/`](trading/) | Deposit wallet setup, USDC.e ↔ pUSD wrapping, a buy, transfers. They use real funds; the ones that spend preview or ask first |
| [`onchain-otc/`](onchain-otc/) | Trading without the order book on a local Polygon fork (own [README](onchain-otc/README.md)) |

When production changes how it calls Polymarket or what its prompts say, update the matching script here in the same PR.
