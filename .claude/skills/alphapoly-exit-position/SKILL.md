---
name: Exiting an Alphapoly Position
description: Sells or merges tokens from an open alphapoly position via CLOB or on-chain merge. Use when exiting, cleaning up, or managing an open position. Also use when the user says "sell", "close position", "cash out", "redeem", "exit", "clear tokens", or wants to get out of a trade.
---

# Exiting an Alphapoly Position

## Prerequisites

- Backend running on `http://localhost:8000`
- Wallet unlocked (action endpoints return HTTP 401 if not)

## Flow

1. **List positions** — `GET /positions` (or `?state=active` to filter)
2. **Pick a position** — note the `position_id`
3. **Choose action** based on state:

| Situation | Action |
|---|---|
| Exit a live position | Sell — `token_type: "wanted"` |
| Clear failed entry leftovers | Sell — `token_type: "unwanted"` |
| Market resolved, hold both outcomes | Merge |
| State is `pending` | Retry |

4. **Confirm** — require explicit user approval before executing any sell or merge
5. **Execute** — see quick reference below
6. **Verify** — re-fetch position; full exit shows `state: "complete"`

## Quick Reference

```
# Sell a side
POST /positions/{id}/sell
{"side": "target", "token_type": "wanted"}

# Merge resolved pair
POST /positions/{id}/merge
{"side": "target"}

# Retry pending
POST /positions/{id}/retry

# Check result
GET /positions/{id}
```

## token_type Explained

When entering, the system splits USDC into YES+NO tokens and sells the side you don't want via CLOB to recover partial cost.

- `"wanted"` — the token you hold as your position (normal exit)
- `"unwanted"` — residual from a failed or partial entry sell (cleanup)

## Typical Exit Flow

```
GET  /positions                          # find position_id
POST /positions/{id}/sell                # {"side":"target","token_type":"wanted"}
POST /positions/{id}/sell                # {"side":"cover","token_type":"wanted"}
GET  /positions/{id}                     # confirm state == "complete"
```

If unwanted balances remain after entry: `POST /positions/{id}/retry`

If market resolved and holding both outcomes:
```
POST /positions/{id}/merge   # {"side":"target"}
POST /positions/{id}/merge   # {"side":"cover"}
```

For full response schemas and error codes, see [api-reference.md](api-reference.md).

## Safety Rules

These operations involve real tokens on Polygon — sells are irreversible once the CLOB order fills, and merges burn tokens permanently.

- Always list positions (`GET /positions`) before acting — never guess a position_id
- Never execute a sell or merge without explicit user confirmation
- After any sell/merge, re-fetch the position to verify the new state
- If a sell returns `filled: false`, surface it — the user still holds the tokens
