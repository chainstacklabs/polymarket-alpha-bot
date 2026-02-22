# Enter Position — API Reference

## Wallet

### Check status
```
GET /wallet/status
```
Response: `{ "exists": true, "unlocked": true, "address": "0x...", "balances": { "usdc_e": 150.0, "pol": 0.8 }, "approvals_set": true }`

### Unlock
```
POST /wallet/unlock
{ "password": "<wallet_password>" }
```
Response: `{ "unlocked": true, "address": "0x..." }`

---

## Estimate

```
POST /trading/buy-pair/estimate
```

Request (`BuyPairRequest`):
```json
{
  "pair_id": "<pair_id>",
  "target_market_id": "<id>",
  "target_position": "YES",
  "target_group_slug": "",
  "cover_market_id": "<id>",
  "cover_position": "NO",
  "cover_group_slug": "",
  "amount_per_position": 10.0,
  "skip_clob_sell": false
}
```

Response (`EstimateResponse`):
```json
{
  "pair_id": "...",
  "total_cost": 20.0,
  "target_market": { "question": "...", "position": "YES", "price": 0.72 },
  "cover_market": { "question": "...", "position": "NO", "price": 0.31 },
  "wallet_balance": 150.0,
  "sufficient_balance": true
}
```

---

## Execute

```
POST /trading/buy-pair
```

Same request body as estimate. Response (`BuyPairResponse`):
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
  "cover": { "...same shape..." },
  "total_spent": 20.0,
  "final_balances": { "usdc_e": 130.0, "pol": 0.8 },
  "warnings": []
}
```

---

## Manual Position Recording (imports only)

```
POST /positions
```

```json
{
  "pair_id": "<pair_id>",
  "entry_amount_per_side": 10.0,
  "target_market_id": "<id>",
  "target_position": "YES",
  "target_token_id": "<token_id>",
  "target_question": "Will X happen?",
  "target_entry_price": 0.72,
  "target_split_tx": "0x...",
  "target_clob_order_id": null,
  "target_clob_filled": false,
  "cover_market_id": "<id>",
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
