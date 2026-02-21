---
name: alphapoly-enter-position
description: Use when entering a new covered pair position in alphapoly
---

# Skill: Enter a Covered Pair Position

Enter a hedged pair position (target + cover) from a detected alphapoly portfolio.

---

## Prerequisites

1. **Backend is running** on `http://localhost:8000`
2. **Wallet is unlocked** — verify with `GET /wallet/status` and check `"unlocked": true`
3. **Sufficient USDC.e balance** — `amount_per_position * 2` is the total cost (both legs)

### Unlock wallet if needed

```
POST /wallet/unlock
{
  "password": "<wallet_password>"
}
```

Response: `{ "unlocked": true, "address": "0x..." }`

### Check wallet balance

```
GET /wallet/status
```

Check `balances.usdc_e` in the response.

---

## 7-Step Flow

### Step 1 — List portfolios

```
GET /data/portfolios
```

Each portfolio entry contains pair details. Identify the pair you want to enter:
- `pair_id`
- `target_market_id`, `target_position` (e.g. `"YES"` or `"NO"`)
- `cover_market_id`, `cover_position`
- `target_group_slug`, `cover_group_slug` (optional, used for position display)

### Step 2 — Pick a pair

Select a pair and note all required IDs and position sides from the portfolio response.

### Step 3 — Set amount

Decide `amount_per_position` (in USDC.e). Total cost = `amount_per_position * 2`.

### Step 4 — Estimate (REQUIRED before executing)

```
POST /trading/buy-pair/estimate
{
  "pair_id": "<pair_id>",
  "target_market_id": "<target_market_id>",
  "target_position": "YES",          // or "NO"
  "target_group_slug": "",           // optional
  "cover_market_id": "<cover_market_id>",
  "cover_position": "NO",            // or "YES"
  "cover_group_slug": "",            // optional
  "amount_per_position": 10.0,
  "skip_clob_sell": false
}
```

Response shape:

```json
{
  "pair_id": "...",
  "total_cost": 20.0,
  "target_market": {
    "question": "...",
    "position": "YES",
    "price": 0.72
  },
  "cover_market": {
    "question": "...",
    "position": "NO",
    "price": 0.31
  },
  "wallet_balance": 150.0,
  "sufficient_balance": true
}
```

Show this estimate to the user. **Do not proceed if `sufficient_balance` is `false`.**

### Step 5 — Confirm

Present the estimate to the user and **require explicit confirmation** before executing.
Do not proceed without a clear "yes", "confirm", or equivalent affirmative response.

### Step 6 — Execute buy-pair

```
POST /trading/buy-pair
{
  "pair_id": "<pair_id>",
  "target_market_id": "<target_market_id>",
  "target_position": "YES",
  "target_group_slug": "",
  "cover_market_id": "<cover_market_id>",
  "cover_position": "NO",
  "cover_group_slug": "",
  "amount_per_position": 10.0,
  "skip_clob_sell": false
}
```

Response shape:

```json
{
  "success": true,
  "pair_id": "...",
  "target": {
    "success": true,
    "market_id": "...",
    "position": "YES",
    "amount": 10.0,
    "split_tx": "0x...",
    "clob_order_id": "...",
    "clob_filled": true,
    "error": null
  },
  "cover": {
    "success": true,
    "market_id": "...",
    "position": "NO",
    "amount": 10.0,
    "split_tx": "0x...",
    "clob_order_id": "...",
    "clob_filled": true,
    "error": null
  },
  "total_spent": 20.0,
  "final_balances": { "usdc_e": 130.0, "pol": 0.8 },
  "warnings": []
}
```

Check `success: true` on both the top-level and both legs. Surface any `warnings` to the user — they indicate CLOB sells that failed (user may hold both YES and NO tokens and need to manually sell unwanted sides via the Positions page).

### Step 7 — Record position (automatic)

Position recording happens **automatically** inside `POST /trading/buy-pair` when `success` is `true`. No separate call is needed.

If you need to manually record a position (e.g. import), call:

```
POST /positions
{
  "pair_id": "<pair_id>",
  "entry_amount_per_side": 10.0,
  "target_market_id": "<target_market_id>",
  "target_position": "YES",
  "target_token_id": "<token_id>",
  "target_question": "Will X happen?",
  "target_entry_price": 0.72,
  "target_split_tx": "0x...",
  "target_clob_order_id": null,
  "target_clob_filled": false,
  "cover_market_id": "<cover_market_id>",
  "cover_position": "NO",
  "cover_token_id": "<token_id>",
  "cover_question": "Will Y happen?",
  "cover_entry_price": 0.31,
  "cover_split_tx": "0x...",
  "cover_clob_order_id": null,
  "cover_clob_filled": false
}
```

Response: `{ "position_id": "<uuid>", "success": true }`

---

## Safety Rules

- **Always run `/trading/buy-pair/estimate` before `/trading/buy-pair`** — never skip the estimate step.
- **Never execute without explicit user confirmation** of the estimate.
- **Never proceed if `sufficient_balance` is `false`** — tell the user to fund the wallet first.
- If `warnings` are non-empty after execution, surface them immediately so the user knows unwanted tokens may need manual selling via the Positions page (`GET /positions`).
- `skip_clob_sell: true` only when the user explicitly requests it (skips CLOB sell of unwanted token side).
