# Exit Position — API Reference

## List Positions

```
GET /positions
GET /positions?state=active   # filter: active | pending | partial | complete
```

Key fields in each position:

| Field | Meaning |
|---|---|
| `state` | `active` (both filled), `pending` (unwanted tokens remain), `partial` (one side sold), `complete` (fully exited) |
| `target_balance` / `cover_balance` | Current balance of the wanted token on each side |
| `target_unwanted_balance` / `cover_unwanted_balance` | Residual unwanted token — non-zero means a pending sell failed |
| `current_value` | Live estimated value of the position |
| `pnl` / `pnl_pct` | Unrealized profit/loss |

---

## Sell Tokens

```
POST /positions/{position_id}/sell
```

Request (`SellTokenRequest`):
```json
{ "side": "target", "token_type": "wanted" }
```

Fields: `side` = `"target"` or `"cover"` | `token_type` = `"wanted"` or `"unwanted"`

Response (`SellTokenResponse`):
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

## Merge Tokens

Burns equal amounts of YES+NO tokens and returns pUSD collateral. Use when a market has resolved. Merged amount = `min(yes_balance, no_balance)`.

```
POST /positions/{position_id}/merge
```

Request (`MergeTokensRequest`):
```json
{ "side": "target" }
```

Response (`MergeTokensResponse`):
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

## Exit Position (orchestrated)

Exits the **entire** position in one call — merges complementary tokens where possible, sells the rest via CLOB. Preferred over manual per-side sell/merge.

```
POST /positions/{position_id}/exit
```

Optional body (`ExitRequest`): `{ "slippage": 10 }` (default `10`, range `10`–`50`). Response (`ExitResponse`):
```json
{
  "success": true,
  "target": { "merged": 0.0, "merge_tx": null, "sold_wanted": 10.0, "sold_unwanted": 0.0, "recovered": 7.2, "error": null },
  "cover":  { "merged": 0.0, "merge_tx": null, "sold_wanted": 10.0, "sold_unwanted": 0.0, "recovered": 3.1, "error": null },
  "total_recovered": 10.3,
  "message": "Exited position"
}
```

---

## Retry Pending

Retries FAK CLOB orders to clear unwanted tokens on both sides for a `pending` position.

```
POST /positions/{position_id}/retry
```

Optional body (`RetryPendingRequest`): `{ "slippage": 10 }` (default `10`, range `10`–`50`). Response (`RetryPendingResponse`):
```json
{
  "success": true,
  "target_result": { "success": true, "token_id": "...", "amount": 5.0, "filled": true, "recovered_value": 1.40, "error": null },
  "cover_result": null,
  "message": "Retried pending sells"
}
```

`target_result` / `cover_result` are `null` when that side had no unwanted balance.

---

## Error Reference

| HTTP Status | Meaning |
|---|---|
| `401` | Wallet locked — unlock first |
| `404` | `position_id` not found |
| `400` | Invalid `side` or `token_type` |
| `500` | Server error — check backend logs |
