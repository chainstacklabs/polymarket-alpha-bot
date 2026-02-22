---
name: Browsing Alphapoly Portfolios
description: Fetches and displays current hedging portfolio opportunities from the backend API. Use when browsing or listing portfolio opportunities to trade. Also use when the user says "show me opportunities", "what's available", "what can I trade", "show pairs", "list portfolios", "any good trades", or asks about current market opportunities.
---

# Browsing Alphapoly Portfolios

Fetch and display current portfolio opportunities from the running backend.

## Requires
Backend running at `localhost:8000` — start with `make backend`

## Fetch
```bash
curl -s http://localhost:8000/data/portfolios | python3 -m json.tool
```

Display as a formatted table with:
- `pair_id` — use this to enter positions
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

To enter a position from the list, use the `alphapoly-enter-position` skill.
