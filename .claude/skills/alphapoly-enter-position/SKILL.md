---
name: Entering an Alphapoly Position
description: Executes a covered pair trade (target + cover) on Polymarket with estimate, confirmation, and position recording. Use when entering a new position from a detected portfolio opportunity. Also use when the user says "buy", "trade", "open a position", "place an order", "enter", or wants to act on any portfolio opportunity.
---

# Entering an Alphapoly Position

Enter a hedged pair position (target + cover) from a detected alphapoly portfolio.

## Prerequisites

1. **Backend running** on `http://localhost:8000`
2. **Wallet unlocked** — check `GET /wallet/status` for `"unlocked": true`; unlock via `POST /wallet/unlock` with `{"password": "<password>"}`
3. **Sufficient USDC.e balance** — total cost is `amount_per_position * 2`

## 7-Step Flow

1. **List portfolios** — `GET /data/portfolios`; note `pair_id`, market IDs, and position sides
2. **Pick a pair** — select by `pair_id`
3. **Set amount** — choose `amount_per_position` in USDC.e
4. **Estimate** (required) — `POST /trading/buy-pair/estimate`; show result to user; stop if `sufficient_balance: false`
5. **Confirm** — require explicit user approval before proceeding
6. **Execute** — `POST /trading/buy-pair`; check `success: true` on both legs; surface any `warnings`
7. **Record** — position is recorded automatically on success; manual `POST /positions` only needed for imports

For full request/response schemas, see [api-reference.md](api-reference.md).

## Safety Rules

These trades involve real USDC on Polygon — transactions are irreversible once submitted on-chain. The estimate step is the only chance to catch problems before money moves.

- Always run `/trading/buy-pair/estimate` before `/trading/buy-pair` — the estimate reveals actual costs, slippage, and balance issues before committing funds
- Never execute without explicit user confirmation — the user needs to see the estimate and agree to the cost
- Never proceed if `sufficient_balance: false` — the transaction will fail on-chain and may leave partial state
- If `warnings` are non-empty after execution, surface them — the user may hold unwanted tokens that need selling via `GET /positions`
- `skip_clob_sell: true` only when the user explicitly requests it
