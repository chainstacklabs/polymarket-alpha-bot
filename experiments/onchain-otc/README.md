# On-Chain OTC Experiments

> Explore every on-chain path for trading Polymarket positions **without CLOB**,
> find edge cases, and prototype an atomic OTC swap.

## TL;DR

Trustless P2P trading for Polymarket positions is **fully viable without the CLOB**. We validated 7 scenarios on a local Polygon fork (Anvil), from basic token transfers to intent-based atomic settlement:

- **Split/merge is lossless** — no fees on the CTF contract itself
- **P2P transfers cost ~$0.003** — permissionless but non-atomic (trust required)
- **Atomic OTC escrow works** — a 100-line Solidity contract enables trustless swaps for ~$0.02
- **NegRisk markets are complex but solvable** — `convertPositions` + OTC gives directional exposure
- **Intent-based settlement is the endgame** — EIP-712 signed intents + ERC-20 wrappers create a permissionless CoW-style marketplace, plugging Polymarket into the broader DeFi ecosystem

The bottleneck is not smart contracts — it's **finding OTC counterparties**. A solver/relayer network is the missing piece for production use.

## Prerequisites

- **Foundry** — `anvil`, `cast`, `forge` (install via `foundryup`)
- **Python + web3.py + eth_account** — available via `cd backend && uv sync`
- **Chainstack archive RPC** — set `CHAINSTACK_NODE` in `.env`; must be an archive node (full nodes return 403 on `eth_getStorageAt`)

## Contents

| Section | Description |
|---------|-------------|
| [Scenario 1 — Baseline](#scenario-1--baseline-confirm-fork-works) | Validate Anvil fork, split/merge round-trip on a real market |
| [Scenario 2 — Direct transfers](#scenario-2--direct-transfers-otc-primitives) | P2P token transfers, batch transfers, edge cases, gas costs |
| [Scenario 3 — Atomic OTC escrow](#scenario-3--atomic-otc-escrow) | Custom Solidity escrow contract for trustless ERC-1155 ↔ ERC-20 swaps |
| [Scenario 4 — NegRisk conversions](#scenario-4--negrisk-conversions) | Multi-outcome `convertPositions`, initial exploration (corrected in Scenario 6) |
| [Scenario 5 — Operator deep-dive](#scenario-5--operator-deep-dive) | EIP-712 order signing, `fillOrder`, `matchOrders` via impersonated operator |
| [Scenario 6 — NegRisk trading](#scenario-6--negrisk-trading-scenarios-can-you-avoid-clob-entirely) | 6 comprehensive scenarios proving NegRisk trades work without CLOB |
| [Scenario 7 — Intent-based trading](#scenario-7--intent-based-trading-for-ctf-tokens) | ERC-20 wrappers + EIP-712 intents = permissionless atomic settlement |
| [Summary](#summary-on-chain-trading-without-clob) | Gas comparison tables, practical trading paths, universal gotchas |
| [Tooling](#tooling) | Tools used (Anvil, Forge, web3.py, Chainstack) |
| [Running](#running) | How to start the fork and run experiments |

## Actors

| Actor | Source | Purpose |
|-------|--------|---------|
| **Alice** | Anvil account #0 | Primary trader, position holder |
| **Bob** | Anvil account #1 | OTC counterparty |
| **USDC Whale** | Impersonate real whale on Polygon | Fund Alice & Bob with USDC.e |
| **PM Operator** | Impersonate operator EOA | Test operator-gated functions |
| **Escrow Contract** | Deployed by us | Atomic ERC-1155 <> ERC-20 swap |

## Contract Addresses (Polygon Mainnet — available on fork)

| Contract | Address | Role |
|----------|---------|------|
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | Collateral token (bridged USDC, 6 decimals) |
| CTF | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | ERC-1155 conditional tokens (YES/NO shares) |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | Operator-gated order matching for binary markets |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` | Operator-gated order matching for multi-outcome markets |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` | Split/merge/convert for multi-outcome (NegRisk) markets |

---

## Scenario 1 — Baseline (confirm fork works)

### Background

Polymarket positions are ERC-1155 tokens managed by the **Conditional Token Framework (CTF)**. Each binary market has a `conditionId` and two token IDs: YES and NO. Splitting $1 USDC.e gives you 1 YES + 1 NO token. Merging burns 1 YES + 1 NO and returns $1 USDC.e. This is the fundamental primitive — everything else builds on it.

We use **Anvil** (Foundry's local chain) to fork Polygon mainnet. The fork copies real on-chain state (contract code, storage, balances) on first access, then caches locally. All transactions are free and instant.

### Checklist

- [x] Fork Polygon at latest block via Anvil
- [x] Fund Alice & Bob with USDC.e (impersonate whale)
- [x] Alice splits USDC.e → YES + NO tokens on a real active market
- [x] Verify balances via `balanceOf`
- [x] Merge round-trip: YES + NO → USDC.e

### Results

| Operation | Gas | Cost (@ 30 gwei) |
|-----------|-----|-------------------|
| `splitPosition` (USDC → YES+NO) | ~133–170k | ~$0.008–0.01 |
| `mergePositions` (YES+NO → USDC) | ~120k | ~$0.007 |

**Status:** PASS

### Findings

- **Split/merge is lossless.** Split $100 → 100M YES + 100M NO. Merge 100M YES + 100M NO → $100. No fees on the CTF contract itself.
- **USDC.e whale:** Aave lending pool at `0x625E7708f30cA75bfd92586e17077590C60eb4cD` holds ~$1M+ USDC.e. Reliable impersonation source.
- **Market discovery:** Use `GET https://gamma-api.polymarket.com/markets?active=true&closed=false` to find live markets with `conditionId` and `clobTokenIds` (YES/NO token IDs). **Important:** Filter for non-NegRisk markets when using `CTF.splitPosition` directly (see gotchas).

### Gotchas

- **EIP-7702 delegation on Anvil accounts.** Anvil's default accounts (`0xf39F...`, `0x7099...`) have contract code on the Polygon fork because real Polygon uses EIP-7702 delegation for these well-known addresses. This breaks ERC-1155 `safeTransferFrom` because the callback `onERC1155Received` hits the delegated code which doesn't return the expected magic value. **Fix:** `anvil_setCode(addr, "0x")` to clear code and make them pure EOAs.
- **Polygon PoA middleware.** Polygon blocks have extra `extraData` that web3.py rejects by default. **Fix:** `w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)`.
- **Whale impersonation needs gas.** Impersonated accounts start with 0 MATIC. **Fix:** `anvil_setBalance(whale, hex(10**18))` before transacting.
- **NegRisk markets produce wrong token IDs via direct CTF split.** Most top-volume markets (80%+, as of Feb 2026) are NegRisk. If you call `CTF.splitPosition` with a NegRisk `conditionId`, the minted token IDs will NOT match the CLOB token IDs from the API — the tokens are position IDs computed via the NegRiskAdapter's path, not the direct CTF path. **Fix:** Always filter `negRisk=false` when fetching markets for scripts that use `CTF.splitPosition` directly. NegRisk markets must be split via the `NegRiskAdapter` (see Scenario 4).

### Code Pattern

```python
# Impersonate whale, fund accounts
w3.provider.make_request("anvil_impersonateAccount", [whale])
w3.provider.make_request("anvil_setBalance", [whale, hex(10**18)])
usdc.functions.transfer(alice, 10_000 * 10**6).transact({"from": whale})

# Clear EIP-7702 code
w3.provider.make_request("anvil_setCode", [alice, "0x"])

# Split: USDC → YES + NO
ctf.functions.splitPosition(
    USDC_E, EMPTY_BYTES32, condition_id_bytes, [1, 2], amount
).transact({"from": alice, "gas": 500_000})
```

---

## Scenario 2 — Direct transfers (OTC primitives)

### Background

ERC-1155 tokens support `safeTransferFrom` — any token holder can transfer their positions to any address. This is the simplest form of OTC: Alice sends YES tokens to Bob, Bob sends USDC.e to Alice in a separate transaction. No intermediary, no CLOB, no operator permission needed.

The trade-off is **non-atomicity**: one side can default after receiving their tokens. This scenario tests the gas costs and edge cases of direct transfers.

### Checklist

- [x] Alice transfers YES tokens to Bob via `safeTransferFrom`
- [x] Bob transfers USDC.e to Alice — non-atomic, trust-based OTC
- [x] Test `safeBatchTransferFrom` — multi-market positions in one tx
- [x] Edge cases: transfer to self, 0 amount, exceeds balance, to contract address
- [x] Test `setApprovalForAll` delegation

### Results

| Operation | Gas | Notes |
|-----------|-----|-------|
| `safeTransferFrom` (single token) | ~57k | Cheapest on-chain token move |
| `safeBatchTransferFrom` (2 tokens) | ~72k | ~15k marginal per extra token |
| Transfer to self | ~29k | Allowed, no-op |
| Transfer 0 amount | ~34k | Allowed, no-op |
| `setApprovalForAll` | ~46k | One-time per operator |

**Status:** PASS

### Findings

- **P2P OTC is extremely cheap.** At ~57k gas per transfer (~$0.003 on Polygon), direct token transfers are 3-4x cheaper than exchange-based settlement.
- **Batch transfers save gas.** Moving positions across multiple markets in one tx costs ~72k for 2 tokens vs ~114k for 2 separate transfers (37% savings).
- **Approval delegation works.** `setApprovalForAll(operator, true)` lets a third party move all your ERC-1155 tokens. This is the ERC-1155 equivalent of ERC-20 `approve` — useful for building automated trading systems.
- **Transfer to contract addresses** succeeds only if the contract implements `onERC1155Received` (ERC-1155 safety check). EOAs always succeed.

### Gotchas

- **web3.py silent reverts.** When you pass an explicit `gas` parameter to `transact()`, web3.py submits the tx without simulating first. If the tx reverts on-chain, **no Python exception is raised** — you get a valid receipt with `status=0`. **Always check `receipt["status"]` in production code.** Without explicit gas, web3.py estimates gas first and raises `ContractLogicError` on revert.
- **Unauthorized transfers** (calling `safeTransferFrom` for someone else's tokens without approval) revert on-chain but follow the same silent pattern with explicit gas.

### Key Insight

> **P2P OTC is gas-cheap (~57k per transfer), but non-atomic.** For trustless swaps between strangers, you need either an escrow contract (Scenario 3) or a trusted intermediary (the CLOB operator). Between known counterparties (e.g., your own wallets, institutional desks), direct transfer is the cheapest option.

---

## Scenario 3 — Atomic OTC escrow

### Background

To make OTC trustless, we need **atomicity**: either both sides of the trade execute, or neither does. We built a minimal Solidity escrow contract that holds ERC-1155 tokens until the counterparty pays ERC-20, then swaps atomically in one transaction.

This is the on-chain equivalent of a DEX limit order — but permissionless, with no operator, no fees, and no dependency on Polymarket's infrastructure.

### Checklist

- [x] Write minimal Solidity escrow: Alice deposits ERC-1155, Bob deposits ERC-20, swap or refund
- [x] Deploy to local fork, test happy path
- [x] Test failure modes: timeout, partial fill, wrong token ID, reentrancy
- [x] Test restricted taker (only Bob can fill)
- [x] Test double-fill prevention

### Results

| Operation | Gas | Cost (@ 30 gwei) |
|-----------|-----|-------------------|
| Deploy contract | ~787k | ~$0.05 |
| `createOffer` (deposit tokens) | ~247k | ~$0.015 |
| `fillOffer` (atomic swap) | ~91–108k | ~$0.005–0.007 |
| `cancelOffer` (reclaim tokens) | ~44k | ~$0.003 |

**Status:** PASS

### Findings

- **Atomic swap works.** Alice deposits YES tokens into escrow, Bob calls `fillOffer` with USDC payment, contract transfers both sides atomically. Total settlement cost: 247k + 108k = 355k gas (~$0.02).
- **Cancel returns tokens correctly.** Maker can reclaim deposited tokens at any time before fill.
- **Expiry enforcement works.** After deadline, taker's fill tx reverts on-chain (silent — check `receipt.status`). Maker can then cancel to reclaim.
- **Restricted taker works.** Setting `taker` to a specific address prevents anyone else from filling. Unauthorized fill reverts.
- **Double-fill prevention.** Second fill attempt on same offer reverts on-chain.

### Contract Design (`OTCEscrow.sol`)

```text
createOffer(ctf, tokenId, amount, wantToken, wantAmount, deadline, taker)
  → Maker deposits ERC-1155 tokens into escrow
  → Returns offerId

fillOffer(offerId)
  → Taker pays ERC-20, receives ERC-1155 tokens
  → Maker receives ERC-20 payment
  → All in one atomic tx

cancelOffer(offerId)
  → Maker reclaims deposited tokens (before fill or after expiry)
```

### Gotchas

- **RPC node must support archive state queries.** When deploying new contracts, Anvil needs to fetch storage for accounts that don't exist yet. Free-tier RPCs (Chainstack free, Ankr without API key) return 403 for `eth_getStorageAt`. **Fix:** Use a Chainstack **archive** node. Alternatively, pass explicit `gas` to all `transact()` calls to skip gas estimation (which triggers state queries).
- **Anvil auto-mine vs block-time mode.** Using `--block-time 2` causes transactions to queue and can stall when combined with `evm_increaseTime`. **Fix:** Use default auto-mine mode (no `--block-time` flag) for testing.

### Key Insight

> **A ~50-line Solidity contract enables trustless atomic OTC** for Polymarket positions. Deploy cost is negligible ($0.05), and each swap costs ~$0.02. This is a viable alternative to CLOB for large block trades, institutional OTC, or markets where the CLOB has poor liquidity.

---

## Scenario 4 — NegRisk conversions

### Background

**NegRisk markets** are Polymarket's multi-outcome markets (e.g., "Who wins the election?" with 4+ candidates). Each outcome is a separate binary question, but they're linked by the **NegRisk Adapter** which enforces that exactly one outcome resolves YES.

The key operation is `convertPositions(marketId, indexSet, amount)`:
- `indexSet` is a **bitmask** — each bit represents a question
- The adapter **burns NO tokens** for all questions whose bits ARE set in the indexSet
- And **mints YES tokens** for all questions whose bits are NOT set
- Plus returns collateral if the indexSet has multiple bits: `amount × (bits_set - 1)`

This is permissionless — anyone can convert without touching the CLOB. It's essentially saying: "I don't think questions X, Y, Z win → give me YES exposure to the others."

### Checklist

- [x] Find a real multi-outcome market on the fork
- [x] Split into YES/NO across multiple outcomes
- [x] Call `convertPositions` — convert NO tokens → YES tokens for other outcomes
- [x] Calculate PnL: is conversion profitable given real prices?
- [ ] Edge cases: insufficient NO tokens, convert after resolution (deferred)

### Results

| Operation | Gas | Notes |
|-----------|-----|-------|
| `splitPosition` via NegRiskAdapter | ~191–284k per question | Varies by storage layout; only need to split indexSet questions |
| `convertPositions` (4-outcome market, 1-bit indexSet) | ~432k | Burns NO, mints YES |
| `convertPositions` (4-outcome market, 2-bit indexSet) | ~375k | Burns 2 NOs + returns collateral |
| `convertPositions` (27+ outcome market) | ~5M+ | Scales with question count |

**Status:** PASS

### Findings

- **Used Fed Decision market** (4 outcomes) for testing. Smaller markets are more practical for conversion.
- **~~Must split ALL questions before converting~~ CORRECTED (Scenario 6).** You only need NO tokens for the question(s) whose bits are SET in the indexSet. Scenario 6 testing confirmed: splitting only question 0 then calling `convertPositions(indexSet=1)` succeeds — the adapter only burns NO[0] and mints YES[1,2,3] without touching other questions' NO tokens.
- **Single-question indexSet (e.g., `indexSet=1`):** Burns NO[0] and mints YES[1,2,3]. Collateral = $0 (only one bit set).
- **Multi-question indexSet (e.g., `indexSet=3`, binary 0011):** Burns NO[0]+NO[1], mints YES[2]+YES[3]. Collateral returned = `amount × (bits_set - 1)`. Confirmed in Scenario 6: indexSet=3 on a 4-outcome market returned $100 collateral for $100 amount.
- **Merge after conversion:** Works for questions NOT involved in the convert (still have matched YES+NO). Fails for the converted question (NO was burned, balance = 0).
- **Convert is always NO→YES.** There is no reverse operation (YES→NO). To get pure NO, split + OTC sell the YES side.

### How `convertPositions` Works (Step by Step)

```text
Market: "Who wins?" — 4 outcomes [A, B, C, D]
Alice splits question A: now holds 100 YES[A] + 100 NO[A]

convertPositions(marketId, indexSet=1, amount=100)
  indexSet=1 → binary 0001 → bit 0 (question A) is set

  Burns:  100 NO[A]   (bit IS set → NO gets burned)
  Mints:  100 YES[B]  (bit NOT set → YES gets minted)
          100 YES[C]
          100 YES[D]
  Collateral returned: $0 (only 1 bit set, formula: amount × (bits-1) = 100×0)

After: Alice has 100 YES[A], 0 NO[A], 100 YES[B], 100 YES[C], 100 YES[D]
       = YES on ALL outcomes = exactly $100 at resolution = break-even

Multi-bit example: convertPositions(marketId, indexSet=3, amount=100)
  indexSet=3 → binary 0011 → bits 0,1 set

  Burns:  100 NO[A] + 100 NO[B]
  Mints:  100 YES[C] + 100 YES[D]
  Collateral returned: $100 (= 100 × (2-1))
```

### PnL Analysis

If the most expensive outcome (highest YES price) is outcome X:
- Buy NO on X at price `(1 - YES_price_X)` — this is cheap if X is the favorite
- Convert: gives you YES on all other outcomes
- Those YES tokens have market value = sum of their YES prices
- Profit if: `sum(other_YES_prices) > cost_of_NO_X`

This is essentially **on-chain arbitrage** of the price constraint `sum(all YES prices) ≈ 1.0` in multi-outcome markets.

### Gotchas

- **On-chain question count ≠ API count.** The Gamma API may show 27 outcomes, but on-chain `getQuestionCount()` returns 40. Large NegRisk markets have padding/reserved questions. Conversion on these uses 5M+ gas and may hit block gas limits.
- **Archive RPC required.** Anvil's fork needs an RPC that supports `eth_getStorageAt` for state queries. Use a Chainstack **archive** node. Chainstack free tier and `ankr` (without API key) do not support archive state.
- **Gamma API parsing.** `outcomePrices` and `clobTokenIds` may be JSON strings or arrays depending on the endpoint. Always handle both: `json.loads(x) if isinstance(x, str) else x`.

### Key Insight

> **`convertPositions` enables permissionless on-chain arbitrage** in multi-outcome markets. No CLOB, no operator, no fees. If sum(YES prices) deviates from 1.0, you can profit by splitting + converting. The main limitations are: gas cost for large markets (27+ outcomes), the one-way nature (NO→YES only), and the fact that convert alone cannot create directional exposure — it always produces a break-even portfolio (see Scenario 6 analysis).

---

## Scenario 5 — Operator deep-dive

### Background

Polymarket's CLOB order book is **off-chain** — users sign EIP-712 typed data orders, and a trusted **operator** submits matches on-chain. The operator is an EOA with a special role in the exchange contract's `Auth` system:

- `admins` mapping — can add/remove operators and other admins
- `operators` mapping — can call `fillOrder`, `fillOrders`, `matchOrders`
- Everyone else — cannot interact with the order-matching functions

This scenario explores the operator mechanics: EIP-712 signing, order struct, `fillOrder` vs `matchOrders`, and access control.

### Checklist

- [x] Make Alice an operator via storage manipulation
- [x] Understand EIP-712 order struct: fields, signatures
- [x] Test `fillOrder` — operator fills a signed order as counterparty
- [x] Test `matchOrders` — operator matches Alice SELL + Bob BUY
- [x] Verify non-operator access denied
- [ ] Understand fee calculation on-chain (deferred)

### Results

| Operation | Gas | Notes |
|-----------|-----|-------|
| `matchOrders` (1 maker + 1 taker) | ~201k | Neutral: operator just submits |
| `fillOrder` (operator fills) | ~132k | Operator is the counterparty |
| Non-operator `fillOrder` | REVERT | Access correctly denied |

**Status:** PASS

### EIP-712 Order Struct

```text
EIP712Domain {
    name: "Polymarket CTF Exchange"
    version: "1"
    chainId: 137
    verifyingContract: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
}

Order {
    salt:          uint256    // Random nonce to make orders unique
    maker:         address    // Order creator
    signer:        address    // Who signed (usually == maker)
    taker:         address    // Restricted counterparty (0x0 = anyone)
    tokenId:       uint256    // CTF token ID (YES or NO)
    makerAmount:   uint256    // Amount maker provides
    takerAmount:   uint256    // Amount maker wants
    expiration:    uint256    // Unix timestamp (0 = no expiry)
    nonce:         uint256    // For order cancellation
    feeRateBps:    uint256    // Fee in basis points
    side:          uint8      // 0 = BUY, 1 = SELL
    signatureType: uint8      // 0 = EOA, 1 = POLY_PROXY, 2 = GNOSIS_SAFE
}
```

**SELL order:** `makerAmount` = conditional tokens to sell, `takerAmount` = USDC wanted.
**BUY order:** `makerAmount` = USDC to spend, `takerAmount` = conditional tokens wanted.

### `fillOrder` vs `matchOrders`

**`fillOrder(order, fillAmount)`**
- `msg.sender` (the operator) becomes the **counterparty**
- Exchange transfers assets between maker and msg.sender
- Use case: operator fills an order directly (acts as market maker)

**`matchOrders(takerOrder, makerOrders[], takerFillAmount, makerFillAmounts[])`**
- Operator is **neutral** — just submits the match
- Exchange verifies both signatures, transfers assets between the two makers
- Use case: matching two users' orders (the normal CLOB flow)

### Findings

- **EIP-712 signature verification works end-to-end.** Python `Account.sign_typed_data()` → on-chain `ecrecover` → signature matches maker address. The exchange correctly rejects tampered orders.
- **`matchOrders` is the production mechanism.** PM's backend collects signed orders from both sides and calls `matchOrders` as operator. The operator never holds user funds.
- **`fillOrder` makes the operator a participant.** The operator acts as counterparty, so they need token/USDC balances and approvals. In production, this is used less frequently.

### Storage Layout Discovery

The CTF Exchange inherits from multiple contracts. The actual storage layout is:

| Slot | Type | Variable |
|------|------|----------|
| 0 | `uint256` | Scalar (likely `_initialized` or similar from parent) |
| 1 | `mapping(address => uint256)` | `admins` |
| 2 | `mapping(address => uint256)` | `operators` |
| 6 | `address` | Unknown (proxy-related?) |
| 7 | `address` | Unknown (proxy-related?) |

**How we found this:** Brute-force — wrote 1 to `keccak256(abi.encode(Alice, slot))` for slots 0-10, checked which slot made `operators(Alice)` and `admins(Alice)` return 1.

### Approval Requirements

The CTF Exchange needs **three separate approvals** to operate:

| From | Token | Approved For | Purpose |
|------|-------|-------------|---------|
| Maker/Taker | CTF (ERC-1155) | `setApprovalForAll(Exchange, true)` | Exchange moves conditional tokens |
| BUY-side | USDC.e (ERC-20) | `approve(Exchange, amount)` | Exchange moves USDC from buyer |
| SELL-side | USDC.e (ERC-20) | `approve(Exchange, amount)` | Exchange moves USDC to seller (needed for fee deduction) |

`setup_accounts.py` only approves USDC.e for CTF + NegRiskAdapter (for splitting). **Exchange USDC approval must be set separately.**

### Gotchas

- **Storage slots are shifted by parent contracts.** Auth.sol declares `admins` at slot 0 and `operators` at slot 1, but the CTF Exchange's inheritance chain pushes them to slots 1 and 2. Never assume slot indices from a single contract's source — always verify empirically.
- **`anvil_setStorageAt` requires `0x` prefix.** Python's `HexBytes.hex()` returns raw hex without prefix (e.g., `a3c127...`). Anvil silently accepts it but writes to the wrong slot. **Fix:** Always use `"0x" + slot.hex()`.
- **Known PM operator `0x768408...` shows `operators=0`** on the CTF Exchange at current block. This address may only be registered on the NegRisk CTF Exchange, or the operator rotates. Don't hard-code operator addresses.
- **`fillOrder` self-trade trap.** If Alice is both the order maker AND the `msg.sender` calling `fillOrder`, the exchange tries to transfer tokens from Alice to Alice and USDC from Alice to Alice. The USDC `transferFrom` fails unless Alice has approved the exchange. This is a pointless self-trade — always use different addresses for maker and operator/filler.

### Code Pattern

```python
# Sign order with EIP-712
from eth_account import Account

signed = Account.sign_typed_data(
    private_key,
    domain_data={"name": "Polymarket CTF Exchange", "version": "1",
                 "chainId": 137, "verifyingContract": CTF_EXCHANGE},
    message_types={"Order": [{"name": "salt", "type": "uint256"}, ...]},
    message_data=order_dict,
)
signature = signed.signature

# Grant operator role via storage manipulation
alice_padded = alice.lower().replace("0x", "").zfill(64)
op_slot = Web3.solidity_keccak(["bytes"], [bytes.fromhex(alice_padded + "0" * 63 + "2")])
w3.provider.make_request("anvil_setStorageAt", [EXCHANGE, "0x" + op_slot.hex(), ONE])
```

### Key Insight

> **All CLOB settlement is operator-gated.** You cannot match orders on-chain without operator permission. However, the operator role is just an access control check — the actual settlement logic (signature verification, token transfers) is transparent and auditable. For self-custody trading without CLOB dependency, use direct transfers (Scenario 2), escrow (Scenario 3), or NegRisk conversion (Scenario 4).

---

## Scenario 6 — NegRisk trading scenarios (can you avoid CLOB entirely?)

### Background

Scenarios 1-5 established the individual primitives (split, merge, convert, transfer, exchange orders). This scenario answers the practical question: **can you fully trade a NegRisk market without ever touching the CLOB?**

We test every trading scenario — getting pure YES exposure, pure NO exposure, and the break-even trap of convert — using Anvil snapshots to run each scenario from a clean state.

### Checklist

- [x] Scenario 1: Split one question → YES+NO pair
- [x] Scenario 2: Convert without splitting all questions (corrects Scenario 4)
- [x] Scenario 3: Split all + convert + merge → prove break-even
- [x] Scenario 4: Pure YES via split + convert + OTC sell waste
- [x] Scenario 5: Pure NO via split + OTC sell YES
- [x] Scenario 6: Multi-bit indexSet conversion with collateral return

### Results

| Scenario | Operations | Total Gas | Status |
|----------|-----------|-----------|--------|
| Split one question | 1 split | ~220k | PASS |
| Convert without splitting all | 1 split + 1 convert | ~692k | PASS (corrects Scenario 4) |
| Break-even proof | 4 splits + convert + 3 merges | ~2.5M | PASS — confirmed break-even |
| Pure YES[0] | 1 split + 1 convert + 3 transfers | ~848k | PASS |
| Pure NO[0] | 1 split + 1 transfer | ~271k | PASS |
| Multi-bit convert (indexSet=3) | 4 splits + 1 convert | ~1.3M | PASS — $100 collateral returned |

**Status:** PASS

### The Break-Even Trap

The most important finding: **split + convert produces YES on ALL outcomes, which is worth exactly $1 regardless of who wins.** This is break-even, not a directional bet.

```text
Split $100 on question A → 100 YES[A] + 100 NO[A]
Convert NO[A]             → +100 YES[B] + 100 YES[C] + 100 YES[D]

Result: 100 YES on every outcome
At resolution: exactly ONE outcome wins → pays $100
You paid $100, you get $100. Zero profit. Zero exposure.
```

To get actual directional exposure, you **must sell the unwanted tokens** to a counterparty (OTC or CLOB).

### Trading Scenarios (Practical Guide)

**"I'm bullish on A" → Pure YES[A]:**
```text
1. Split $100 on A          → 100 YES[A] + 100 NO[A]     (220k gas)
2. Convert NO[A]            → +100 YES[B,C,D]             (473k gas)
3. OTC sell YES[B,C,D]      → receive ~$(1-price_A) USDC  (52k × 3 gas)
   Net cost: ~$price_A per token (same as CLOB)
   Total: ~848k gas
```

**"I'm bullish on A" (simpler, no convert):**
```text
1. Split $100 on A          → 100 YES[A] + 100 NO[A]     (220k gas)
2. OTC sell NO[A]           → receive ~$(1-price_A) USDC  (52k gas)
   Net cost: ~$price_A per token
   Total: ~272k gas (3x cheaper than convert path!)
```

**"I'm bearish on A" → Pure NO[A]:**
```text
1. Split $100 on A          → 100 YES[A] + 100 NO[A]     (220k gas)
2. OTC sell YES[A]          → receive ~$price_A USDC      (52k gas)
   Net cost: ~$(1-price_A) per token
   Total: ~272k gas
```

### Why Convert Exists (When It's Useful)

Convert doesn't help with directional exposure, but it IS useful for:

1. **Rearranging waste into more liquid forms.** Instead of selling one expensive NO token, sell multiple cheap YES tokens — more potential buyers.
2. **Cross-outcome arbitrage.** If `sum(YES prices) ≠ 1.0` across outcomes, split + convert + sell can capture the deviation.
3. **Portfolio rebalancing.** Convert NO on one outcome to YES on others without going through USDC.

### Correction to Scenario 4

Scenario 4 stated: "Must split ALL questions before converting." **This is wrong.**

Scenario 6 proved: you only need NO tokens for the question(s) in the indexSet. With `indexSet=1`, only NO[0] is burned — the adapter doesn't touch NO[1,2,3]. Splitting only question 0 before calling `convertPositions(indexSet=1)` works.

The Scenario 4 failure was likely caused by the large market (40 on-chain questions vs 27 API-visible), where the adapter's internal operations hit edge cases with hidden questions.

### Gotchas

- **`evm_snapshot`/`evm_revert`** is essential for testing multiple scenarios on the same fork without interference. Each scenario starts from a clean state.
- **Convert is always NO→YES.** There is no `unconvertPositions` or reverse direction. To go YES→NO, split + sell.
- **The "simple path" (split + OTC) is almost always cheaper** than the convert path. Convert adds ~473k gas for the rearrangement step. Only worth it when the rearranged tokens are significantly more liquid.

### Key Insight

> **NegRisk markets CAN be traded without CLOB, but you always need an OTC counterparty for the unwanted side.** The cheapest path is: 1 split + 1 OTC transfer (~272k gas, ~$0.02). `convertPositions` doesn't eliminate the OTC requirement — it rearranges your waste into different tokens. The real constraint isn't smart contracts, it's **liquidity**: finding someone to take the other side.

---

## Scenario 7 — Intent-based trading for CTF tokens

### Background

**What are intents?** An intent is a signed, declarative message where a user specifies a desired outcome rather than an explicit execution path. Instead of constructing a specific transaction ("split on CTF, sell on CLOB"), the user signs: "I want to sell 100 YES tokens and receive at least 45 USDC.e." Specialized actors called **solvers** compete to fulfill it optimally.

```text
Traditional tx:  User → "call swap(tokenA, tokenB, amount) on router 0xABC"
Intent:          User → "I want ≥45 USDC.e for my 100 YES tokens, figure it out"
```

Key properties:
- **Off-chain signature** — user signs EIP-712 typed data, pays no gas
- **Solver competition** — solvers compete to fill at best price (batch auction, Dutch auction)
- **MEV protection** — orders never enter public mempool, preventing front-running/sandwiching
- **On-chain settlement** — settlement contract atomically verifies the user's intent was satisfied

### The ERC-1155 Problem

**No major intent protocol supports ERC-1155 natively.** This is the fundamental blocker.

| Protocol | Polygon Status | ERC-1155? | Notes |
|----------|---------------|-----------|-------|
| CoW Protocol | Live (`0x9008D19f58AAbD9eD0D60971565AA8510560ab41`) | No | Batch auction, whitelisted solvers, gasless for users |
| 1inch Fusion | Live (Aggregation Router V5) | No | Dutch auction via staked resolvers |
| UniswapX | Not confirmed on Polygon | No | Permissionless fillers, Dutch auction |
| Across Protocol | Live (bridge) | No | Cross-chain intent bridge, sub-2s fills |
| ERC-7683 standard | Spec only | No | Uses Permit2 (ERC-20 only) |

All intent protocols are built around ERC-20 `approve()` + `transferFrom()`. ERC-1155 uses `setApprovalForAll` — fundamentally different pattern. (Note: Seaport supports ERC-1155 natively but is order-based, not intent-based — no solver competition.)

### Prior Art: Gnosis `1155-to-20` Wrapper

The bridge between ERC-1155 and intent protocols is the **Gnosis wrapper** — converts CTF tokens to ERC-20:

| Field | Details |
|-------|---------|
| Repo | `github.com/gnosis/1155-to-20` |
| Team | Gnosis — same org that built the CTF that Polymarket is built on, and Gnosis Safe (now Safe{Wallet}) used for all Polymarket proxy wallets |
| Stats | 5 contributors, 48 stars, 22 forks, 56 commits |
| License | LGPL-3.0 |
| Deployment | EIP-2470 SingletonFactory for deterministic cross-chain deployment (same address on every chain) |
| Status | Mature but low-activity — the contracts are simple and "done" |

**How it works:**

```text
User's Polymarket YES token (ERC-1155, tokenId=12345)
    │
    │  safeTransferFrom(user, factory, tokenId, amount, "")
    ▼
Wrapped1155Factory
    │
    │  1. onERC1155Received callback fires
    │  2. Deploys minimal proxy ERC-20 (one per tokenId, via EIP-1167)
    │  3. Mints equivalent ERC-20 to user
    ▼
Wrapped YES token (standard ERC-20)
    → tradeable on Uniswap / CoW Protocol / 1inch
    → usable as collateral in DeFi
    → compatible with any ERC-20 protocol
```

**Key design choices:**
- **Wrapping = just a transfer** — no separate `approve()` + `wrap()`. Send ERC-1155 to the factory, get ERC-20 back in the same tx via `onERC1155Received` callback
- **One proxy per tokenId** — each CTF token ID gets a unique ERC-20 address, deployed via EIP-1167 minimal proxy (cheap)
- **Deterministic addresses** — anyone can compute the wrapped token address for a given tokenId without deploying it first

### Approach: Wrapper + CoW Protocol

```text
CTF ERC-1155 → wrap → wYES (ERC-20) → CoW Protocol intent → solver fills → USDC.e
```

The flow:
1. User wraps CTF tokens via Gnosis `Wrapped1155Factory` → gets standard ERC-20
2. User approves CoW Protocol's `GPv2VaultRelayer` on the wrapped ERC-20
3. User signs EIP-712 intent: "sell X wYES for ≥Y USDC.e"
4. CoW solvers compete to fill (batch auction every ~30s)
5. Settlement: `GPv2Settlement.settle()` executes atomically
6. Buyer unwraps ERC-20 → gets native CTF ERC-1155 tokens

**Reality check:** No solver currently has inventory for wrapped CTF tokens, and no DEX pools exist. Each market×outcome needs a separate wrapped token = thousands of ultra-thin markets. This scenario tests whether the **mechanics** work, not whether there's liquidity.

### Checklist

- [x] Deploy Wrapped1155Factory on fork (our minimal impl, follows Gnosis pattern)
- [x] Test wrap: `safeTransferFrom(user, factory, tokenId, amount, "")` → receive ERC-20
- [x] Test unwrap: burn ERC-20 → receive CTF ERC-1155 back (lossless round-trip)
- [x] Verify wrapper reuse: second wrap for same tokenId skips deployment
- [x] Simulate intent: Alice signs EIP-712 sell intent, Bob (solver) verifies + fills
- [x] Full round-trip: CTF → wrap → intent trade → unwrap → CTF
- [x] Deploy IntentSettlement contract (on-chain EIP-712 ecrecover + atomic swap)
- [x] Permissionless fill: Bob calls `fillIntent()` — on-chain sig verification + atomic settlement
- [x] Security: replay rejected (nonce), tampered sig rejected (ecrecover), expired rejected (deadline)
- [ ] Approve wrapped ERC-20 for CoW `GPv2VaultRelayer` (deferred: needs live CoW)

### Results

| Operation | Gas | Cost (~$0.06/gwei) | Notes |
|-----------|-----|-----|-------|
| Deploy Wrapped1155Factory | (in wrap tx) | — | One-time |
| Deploy IntentSettlement | 552,100 | ~$0.033 | One-time, permissionless fills |
| **Wrap (first, deploys ERC-20)** | **649,567** | **~$0.039** | Deploys new Wrapped1155 contract |
| **Wrap (subsequent)** | **56,836** | **~$0.003** | Reuses existing wrapper, 11x cheaper |
| **Unwrap** | **48–63k** | **~$0.003** | Burns ERC-20, returns CTF (higher for first-time recipient) |
| **`fillIntent` (on-chain atomic)** | **139,559** | **~$0.008** | ecrecover + 2× transferFrom, permissionless |
| **Full chain (wrap+fill+unwrap)** | **~245k** | **~$0.015** | Excluding first-time wrapper deploy |

### Findings

1. **Wrapping works perfectly.** `safeTransferFrom` to factory triggers `onERC1155Received` which deploys an ERC-20 proxy and mints 1:1. Round-trip is lossless.
2. **First wrap is expensive (650k gas)** because it deploys the Wrapped1155 contract inside the callback. Subsequent wraps for the same tokenId cost only 57k. On Polygon this is still cheap (~$0.04 first time, ~$0.003 after).
3. **Wrapper reuses existing proxies** — each (multiToken, tokenId) pair gets one ERC-20 address. Multiple wraps/unwraps use the same contract.
4. **On-chain intent settlement works.** `IntentSettlement.fillIntent()` verifies Alice's EIP-712 signature via `ecrecover` and executes an atomic wYES↔USDC swap in ONE transaction. Gas: 140k — comparable to Polymarket's `fillOrder` (132k) but **permissionless**.
5. **Security is enforced on-chain.** Replay attacks (nonce increment), tampered signatures (ecrecover mismatch), and expired intents (deadline check) are all rejected by the contract.
6. **The wrapped token is a standard ERC-20** — `approve`, `transferFrom`, `balanceOf`, `totalSupply` all work. Compatible with any DeFi protocol.
7. **This is the permissionless equivalent of Polymarket's CLOB.** `fillIntent()` requires no operator whitelist — anyone (human, bot, agent) can be a solver. The contract is the trust layer.
8. **Unwrap gas varies by recipient.** First-time CTF token receipt for an address costs ~63k gas (storage slot initialization for new ERC-1155 balance). Subsequent unwraps to the same address cost ~48k.

### Gotchas

| Gotcha | Impact | Fix |
|--------|--------|-----|
| First wrap needs >500k gas | Reverts with OOG inside callback | Use 1M gas for first wrap per tokenId |
| `eth_account` API changes | `messageHash` → `message_hash`, no `recover_message_from_typed_data` | Use `Account._recover_hash()` + `signed.message_hash` |
| `clobTokenIds` is JSON string | `int(m["clobTokenIds"][0])` fails — it's `"[\"123\",\"456\"]"` | `json.loads()` before indexing |
| Condition ID has `0x` prefix | `bytes.fromhex()` chokes on `0x` | `.removeprefix("0x")` |

### Code Pattern: Wrap via Transfer

```python
# Wrap: just transfer ERC-1155 to the factory — no approve() needed
tx = ctf.functions.safeTransferFrom(
    alice, factory_addr, yes_token_id, amount, b""
).transact({"from": alice, "gas": 1_000_000})  # extra gas for first-time deploy

# Unwrap: call factory.unwrap()
tx = factory.functions.unwrap(
    CTF, yes_token_id, amount, recipient
).transact({"from": holder, "gas": 300_000})
```

### Code Pattern: On-Chain Intent Settlement

```python
# 1. Seller signs EIP-712 intent (off-chain, gasless)
signed = Account.sign_typed_data(
    seller_key,
    domain_data={"name": "CTF Intent Exchange", "version": "1",
                 "chainId": 137, "verifyingContract": settlement_addr},
    message_types=INTENT_TYPES,
    message_data={"seller": alice, "sellToken": wyes_addr, "sellAmount": amount,
                  "buyToken": USDC_E, "minBuyAmount": price, "deadline": ts, "nonce": 0},
)

# 2. Seller approves settlement contract (one-time per token)
wyes.functions.approve(settlement_addr, amount).transact({"from": alice})

# 3. Any solver fills — on-chain sig verification + atomic swap
settlement.functions.fillIntent(
    (alice, wyes_addr, amount, USDC_E, price, deadline, nonce),  # struct
    price,                # buyAmount (>= minBuyAmount)
    signed.v, r, s,       # signature
).transact({"from": solver})  # ANYONE can call this
```

### Contract Design (`IntentSettlement.sol`)

```text
fillIntent(SellIntent intent, uint256 buyAmount, uint8 v, bytes32 r, bytes32 s)
  → Verifies EIP-712 signature via ecrecover
  → Checks: deadline, nonce, minBuyAmount
  → Pulls sellToken from seller → solver (transferFrom)
  → Pulls buyToken from solver → seller (transferFrom)
  → All atomic — both succeed or both revert
  → PERMISSIONLESS — no operator role, anyone can call

cancelAll()
  → Seller increments nonce, invalidating all pending intents
```

**Key difference from Polymarket CLOB:**

| | Polymarket CLOB | IntentSettlement |
|---|---|---|
| Who can settle? | Whitelisted operator only | **Anyone** |
| Sig verification | On-chain (same) | On-chain (same) |
| Atomic? | Yes | Yes |
| Gas | ~132-201k | ~140k |
| Trust | Trust the operator | Trust the **contract** |

### Architecture

```text
              wrap                sign intent        fillIntent()
CTF ERC-1155  ────►  wYES  ──────────────────►  IntentSettlement  ──►  USDC.e
(YES token)   ◄────  wYES  ◄── unwrap ◄─────   (on-chain swap)

Wrapped1155Factory:
  wrap    → safeTransferFrom(user, factory, tokenId, amt, "")
  unwrap  → burn ERC-20, factory returns CTF tokens 1:1

IntentSettlement (our contract):
  1. Seller signs EIP-712 intent off-chain (gasless)
  2. Seller approves contract to spend wYES
  3. ANY solver calls fillIntent(intent, sig)
  4. Contract: ecrecover → verify seller → atomic swap
  5. No operator. No whitelist. The contract IS the trust layer.
```

**The permissionless marketplace:**

```text
Alice signs intent:                            Bob (agent/solver):
"sell 50 wYES for ≥$45 USDC"                  sees intent, evaluates price
      │                                               │
      ▼                                               ▼
IntentSettlement.fillIntent()  ◄───────────── Bob calls fillIntent()
  1. ecrecover verifies Alice's sig            pays $45 USDC, receives 50 wYES
  2. Pulls wYES from Alice → Bob               │
  3. Pulls USDC from Bob → Alice               ▼
  4. All in ONE atomic tx                      Bob unwraps → 50 CTF YES tokens
```

Compared to Polymarket CLOB: Alice signs an EIP-712 order, a **single whitelisted operator** matches it. Here: Alice signs an EIP-712 intent, **any agent** can fill it. Same sig verification, same atomicity, zero gatekeeping.

### Why This Matters

| Approach | Needs counterparty? | Price discovery? | MEV protected? | Gas (user) |
|----------|-------------------|-----------------|----------------|------------|
| CLOB (Polymarket) | Yes (+ operator) | Yes (order book) | No | ~$0.01 |
| P2P transfer (Scenario 2) | Yes (trusted) | No | N/A | ~$0.003 |
| OTC escrow (Scenario 3) | Yes (trustless) | No | N/A | ~$0.007 |
| **Intent + solver (Scenario 7)** | **Yes (solver)** | **Yes (competition)** | **Yes** | **$0 (solver pays)** |

Intents don't eliminate the counterparty — they replace a **single trusted operator** with **competing solvers**. The user experience improves (gasless, MEV-protected), but the fundamental economics are the same: someone needs to take the other side.

### Key Insight

> Polymarket's CLOB is already an intent system in disguise. Users sign EIP-712 orders (intents), an operator (solver) matches them, and settlement is on-chain. The difference: Polymarket has ONE operator. A true intent protocol would have MANY competing solvers, reducing trust and improving price discovery. Scenario 7 explores whether we can build that permissionless layer on top of existing infrastructure.

### Open Questions

- **Solver bootstrapping:** Who are the first solvers? Need at least one to bridge CLOB ↔ intent liquidity via the unwrap→CLOB sell loop.
- **Latency:** CoW batch auctions run every ~30s. Polymarket CLOB matches in <1s. Is 30s acceptable for prediction market trading?
- **Wrapped token liquidity:** Each market×outcome = separate ERC-20 address. Thousands of ultra-thin tokens with no DEX pools. Solvers must route through CLOB, not DEXes.
- **Wrapper deployment:** Is Gnosis `Wrapped1155Factory` already deployed on Polygon mainnet? If not, we deploy via EIP-2470 SingletonFactory.
- **CoW solver whitelist:** CoW uses whitelisted solvers. Can we register a custom solver that understands CTF→CLOB routing, or must we rely on existing solvers?

**Status:** PASS — wrapper, intent signing, and on-chain permissionless settlement all verified. CoW Protocol integration deferred (no live solver inventory).

---

## Summary: On-Chain Trading Without CLOB

### Permissionless Operations (anyone can call)

| Operation | Gas | Cost | Use Case |
|-----------|-----|------|----------|
| `safeTransferFrom` | ~57k | ~$0.003 | P2P OTC (trusted counterparty) |
| `safeBatchTransferFrom` | ~72k (2 tokens) | ~$0.004 | Multi-position P2P transfer |
| `splitPosition` | ~133–220k | ~$0.008–0.013 | USDC → YES+NO tokens (varies by market) |
| `mergePositions` | ~170k | ~$0.01 | YES+NO → USDC (lossless) |
| `convertPositions` | ~375–432k | ~$0.023–0.026 | NO→YES rearrangement (NegRisk only) |
| OTC Escrow `fillOffer` | ~91–108k | ~$0.005–0.007 | Atomic trustless swap |
| Wrap ERC-1155→ERC-20 (first) | ~650k | ~$0.039 | Deploys wrapper + mints (one-time) |
| Wrap ERC-1155→ERC-20 (subsequent) | ~57k | ~$0.003 | Mints wrapped ERC-20 |
| Unwrap ERC-20→ERC-1155 | ~48–63k | ~$0.003–0.004 | Burns ERC-20, returns CTF tokens (higher for first-time recipient) |
| Intent `fillIntent` | ~140k | ~$0.008 | On-chain sig verification + atomic swap (permissionless) |

### Operator-Gated Operations (CLOB only)

| Operation | Gas | Cost | Use Case |
|-----------|-----|------|----------|
| `matchOrders` | ~201k | ~$0.012 | Match two signed orders |
| `fillOrder` | ~132k | ~$0.008 | Operator fills order as counterparty |

### Practical Trading Paths (No CLOB)

**For directional exposure (YES or NO on a specific outcome):**

| Path | Operations | Gas | When to use |
|------|-----------|-----|-------------|
| Split + OTC sell unwanted | 1 split + 1 transfer | ~243–272k | **Best default.** Cheapest path. Need OTC counterparty for one token. |
| Split + convert + OTC sell waste | 1 split + 1 convert + (N-1) transfers | ~780–848k | When waste YES tokens are more liquid than the NO you'd otherwise sell. |
| Split + escrow | 1 split + 1 createOffer + 1 fillOffer | ~530–575k | When you need **trustless** atomic settlement with a stranger. |

**Other permissionless operations:**

| Path | Operations | Gas | Notes |
|------|-----------|-----|-------|
| Direct P2P transfer | 1 `safeTransferFrom` | ~52k | Cheapest. Non-atomic, trust-based. |
| Batch transfer | 1 `safeBatchTransferFrom` | ~72k (2 tokens) | Multi-position in one tx. |
| NegRisk arbitrage | split + convert + sell | varies | Only profitable when sum(YES prices) ≠ 1.0 |
| Intent OTC (wrap+fill+unwrap) | 1 wrap + 1 fillIntent + 1 unwrap | ~245k | **Permissionless atomic swap.** Seller signs off-chain, any solver fills. |

**The fundamental constraint:** split always costs $1 per YES+NO pair regardless of market price. To match CLOB economics (buying YES at $0.30 costs $0.30), you must sell the unwanted side. **The bottleneck is finding OTC counterparties, not smart contract limitations.**

### Universal Gotchas

| Gotcha | Impact | Fix |
|--------|--------|-----|
| EIP-7702 on Anvil accounts | ERC-1155 transfers fail silently | `anvil_setCode(addr, "0x")` |
| web3.py silent reverts | No exception with explicit `gas` | Always check `receipt["status"]` |
| Polygon PoA extraData | web3.py connection fails | `ExtraDataToPOAMiddleware` |
| RPC must be **archive** node | Fork hangs on state queries, 403 on `eth_getStorageAt` | Use Chainstack archive node |
| NegRisk via direct CTF split | Minted token IDs ≠ CLOB token IDs | Filter `negRisk=false` for binary scripts; use NegRiskAdapter for NegRisk |
| USDC.e approval scope | Exchange transfers fail | Approve USDC for each contract separately |
| NegRisk question count mismatch | Conversion OOG or overflow | Check `getQuestionCount()` on-chain, not API |
| Convert = break-even trap | NO→YES gives all-outcome exposure = $1 | Must OTC sell waste for directional exposure |
| Convert is one-way | NO→YES only, no reverse | For YES→NO: split + sell YES side |
| First wrap is expensive (650k gas) | OOG inside `onERC1155Received` callback | Use 1M gas limit; subsequent wraps are ~57k |
| Unwrap gas varies by recipient | First-time CTF recipient costs ~63k vs ~48k | Budget extra gas for new recipients (storage init) |
| No intent protocol supports ERC-1155 | Can't use CoW/1inch/UniswapX directly | Wrap to ERC-20 first via Wrapped1155Factory |
| Most top-volume markets are NegRisk | Binary market scripts pick wrong market type | Always check `negRisk` flag from API (80%+ are NegRisk as of Feb 2026) |

---

## Tooling

| Tool | Role |
|------|------|
| Anvil (`~/.foundry/bin/anvil`) | Local forked Polygon chain |
| cast (`~/.foundry/bin/cast`) | Quick contract reads/writes, tx tracing |
| forge (`~/.foundry/bin/forge`) | Compile & deploy Solidity contracts |
| Python + web3.py | Complex multi-step scenarios |
| eth_account | EIP-712 typed data signing |
| Chainstack archive RPC | Fork source (**must support archive state** for `eth_getStorageAt`) |

## File Layout

```text
experiments/onchain-otc/
├── README.md             # This file — findings, gotchas, educational notes
├── anvil.sh              # Start forked chain
├── setup_accounts.py     # Fund actors, set approvals
├── contracts/
│   ├── OTCEscrow.sol     # Minimal ERC-1155 <> ERC-20 swap
│   └── foundry.toml      # Forge compilation config
├── 01_baseline.py        # Scenario 1: fork, split, verify balances
├── 02_transfers.py       # Scenario 2: direct token transfers, edge cases
├── 03_escrow.py          # Scenario 3: deploy & test atomic escrow
├── 04_negrisk.py         # Scenario 4: convertPositions, PnL analysis
├── 05_operator.py        # Scenario 5: EIP-712, fillOrder, matchOrders
├── 06_negrisk_trading.py # Scenario 6: NegRisk trading scenarios, break-even proof
└── 07_intents.py         # Scenario 7: Gnosis wrapper + intent-based trading
```

## Running

**Prerequisite:** The fork RPC (`CHAINSTACK_NODE` in `.env`) must be an **archive node**. Free-tier / full nodes return 403 on `eth_getStorageAt`, which breaks contract deployment, gas estimation, and storage manipulation.

```bash
# Terminal 1: Start forked chain (uses CHAINSTACK_NODE from .env)
./experiments/onchain-otc/anvil.sh

# Terminal 2: Fund accounts (run once per fresh fork)
cd backend && uv run ../experiments/onchain-otc/setup_accounts.py

# Terminal 2: Run any experiment
cd backend && uv run ../experiments/onchain-otc/01_baseline.py
cd backend && uv run ../experiments/onchain-otc/05_operator.py
```
