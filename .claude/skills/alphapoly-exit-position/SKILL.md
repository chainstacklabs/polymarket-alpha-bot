---
name: alphapoly-exit-position
description: Use when exiting or managing an open position in alphapoly
---

# Exiting or Managing an Open Position

## Prerequisites

- Backend is running on `http://localhost:8000`
- Wallet is unlocked (all action endpoints return HTTP 401 if not unlocked)
- You know the `position_id` of the position to manage (UUID string)

---

## Step 1 — List Open Positions

Retrieve all positions with live balances, prices, and state:

```
GET /positions
```

Optional query parameter to narrow results:

```
GET /positions?state=active      # or: pending, partial, complete
```

### Response shape (`PositionsListResponse`)

```json
{
  "count": 3,
  "active_count": 2,
  "total_pnl": -1.42,
  "positions": [
    {
      "position_id": "uuid",
      "pair_id": "uuid",
      "entry_time": "2026-02-20T10:00:00Z",
      "entry_amount_per_side": 10.0,
      "entry_total_cost": 20.0,
      "entry_net_cost": 18.50,

      "target_market_id": "0xabc...",
      "target_position": "YES",
      "target_token_id": "0x111...",
      "target_question": "Will X happen?",
      "target_entry_price": 0.72,
      "target_group_slug": "some-group",
      "target_split_tx": "0xtx...",
      "target_clob_order_id": "order_id or null",
      "target_clob_filled": true,

      "cover_market_id": "0xdef...",
      "cover_position": "NO",
      "cover_token_id": "0x222...",
      "cover_question": "Will Y happen?",
      "cover_entry_price": 0.28,
      "cover_group_slug": "some-group",
      "cover_split_tx": "0xtx...",
      "cover_clob_order_id": null,
      "cover_clob_filled": false,

      "notes": null,

      "target_balance": 13.89,
      "cover_balance": 35.71,
      "target_current_price": 0.78,
      "cover_current_price": 0.21,
      "target_unwanted_balance": 0.0,
      "cover_unwanted_balance": 0.0,

      "state": "active",
      "current_value": 18.33,
      "pnl": -0.17,
      "pnl_pct": -0.92,

      "selling_target": false,
      "selling_cover": false
    }
  ]
}
```

Key fields for deciding an action:

| Field | Meaning |
|---|---|
| `state` | `active` (both sides filled), `pending` (unwanted tokens remain), `partial` (one side sold), `complete` (fully exited) |
| `target_balance` / `cover_balance` | Current on-chain balance of the **wanted** token on each side |
| `target_unwanted_balance` / `cover_unwanted_balance` | Balance of the **unwanted** token remaining — non-zero means a pending sell failed |
| `selling_target` / `selling_cover` | `true` while a sell is in flight (persists across page refresh) |

---

## Step 2 — Pick a Position and Choose an Action

Use `position_id` from the list above. There are three actions:

| Situation | Action |
|---|---|
| Exit a live position (sell your held tokens at market) | **Sell — `token_type: "wanted"`** |
| Clear leftover tokens from an incomplete entry | **Sell — `token_type: "unwanted"`** |
| Market is resolved; you hold both YES and NO tokens | **Merge** |
| Position state is `pending`; retry clearing unwanted tokens | **Retry** |

---

## Step 3 — Execute an Action

### Action A: Sell Tokens

Sells tokens from one side of the position via a CLOB IOC (Immediate-or-Cancel) market order at an aggressive price for instant execution.

```
POST /positions/{position_id}/sell
```

Request body (`SellTokenRequest`):

```json
{
  "side": "target",
  "token_type": "wanted"
}
```

Fields:

| Field | Values | Meaning |
|---|---|---|
| `side` | `"target"` or `"cover"` | Which market leg of the position to act on |
| `token_type` | `"wanted"` or `"unwanted"` | Which token to sell (see below) |

**`token_type` explained:**

When you enter a position, the system splits USDC into YES+NO outcome tokens, then sells the side you do not want via CLOB to recover partial cost. The token you keep is the **wanted** token (your actual position). The token being sold off during entry is the **unwanted** token.

- `"wanted"` — sell the token you are holding as your position (normal exit)
- `"unwanted"` — sell the residual token from a failed or partial entry order (cleanup)

Response body (`SellTokenResponse`):

```json
{
  "success": true,
  "token_id": "0x111...",
  "amount": 13.89,
  "order_id": "clob-order-id",
  "filled": true,
  "recovered_value": 10.82,
  "error": null
}
```

---

### Action B: Merge Tokens (resolved markets)

Burns equal amounts of YES and NO tokens on-chain and returns USDC.e collateral. Use this when a market has resolved and you hold both outcome tokens. The merged amount is `min(yes_balance, no_balance)`.

```
POST /positions/{position_id}/merge
```

Request body (`MergeTokensRequest`):

```json
{
  "side": "target"
}
```

Fields:

| Field | Values | Meaning |
|---|---|---|
| `side` | `"target"` or `"cover"` | Which market leg to merge |

Response body (`MergeTokensResponse`):

```json
{
  "success": true,
  "market_id": "0xabc...",
  "merged_amount": 10.0,
  "tx_hash": "0xtx...",
  "error": null
}
```

---

### Action C: Retry Pending Sells

Retries selling unwanted tokens for a position stuck in `pending` state. Checks both `target` and `cover` sides for non-zero unwanted balances and attempts FOK (Fill-or-Kill) CLOB orders. No request body needed.

```
POST /positions/{position_id}/retry
```

Response body (`RetryPendingResponse`):

```json
{
  "success": true,
  "target_result": {
    "success": true,
    "token_id": "0x111...",
    "amount": 5.0,
    "order_id": "order_id",
    "filled": true,
    "recovered_value": 1.40,
    "error": null
  },
  "cover_result": null,
  "message": "Retried pending sells"
}
```

`target_result` and `cover_result` are `null` when that side had no unwanted balance.

---

## Step 4 — Confirm Success

After any action, re-fetch the position to verify state:

```
GET /positions/{position_id}
```

A successful full exit will show `state: "complete"` and both `target_balance` and `cover_balance` near zero.

---

## Error Reference

| HTTP Status | Meaning |
|---|---|
| `401` | Wallet is locked — unlock it first |
| `404` | `position_id` not found |
| `400` | Bad request (e.g. invalid side or token_type) |
| `500` | Unexpected server error — check backend logs |

---

## Typical Exit Flow (Summary)

```
1. GET  /positions                          # find position_id and check state
2. POST /positions/{id}/sell                # body: {"side":"target","token_type":"wanted"}
3. POST /positions/{id}/sell                # body: {"side":"cover","token_type":"wanted"}
4. GET  /positions/{id}                     # confirm state == "complete"
```

If `target_unwanted_balance` or `cover_unwanted_balance` is non-zero after entry:

```
POST /positions/{id}/retry                  # clears both sides automatically
```

If market is resolved and you hold both outcomes:

```
POST /positions/{id}/merge                  # body: {"side":"target"}
POST /positions/{id}/merge                  # body: {"side":"cover"}
```
