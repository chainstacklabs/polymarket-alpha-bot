# Gondor — DeFi Lending on Polymarket Positions

This document is a reference for experimenting with Gondor on a local Polygon fork.
It covers the full on-chain flow, all contract addresses, and the background concepts
needed to understand what each step is actually doing.

---

## Background Concepts

### Polymarket

Polymarket is a prediction market on Polygon where users trade YES/NO outcome shares
for real-world events ("Will X happen by date Y?"). Shares are priced between $0 and $1
based on crowd-estimated probability. The winning side redeems at $1; the losing side at $0.

Polymarket does not run its own smart contracts for settlement — it is built on top of the
**Gnosis Conditional Token Framework**.

### Conditional Token Framework (CTF)

The CTF is a general-purpose ERC1155 contract originally built by Gnosis. It holds USDC
as collateral and issues outcome shares as ERC1155 tokens.

Core mechanic:
- `splitPosition(conditionId, amount)` — locks N USDC, mints N YES tokens + N NO tokens
- `mergePositions(conditionId, amount)` — burns N YES + N NO, returns N USDC
- `redeemPositions(conditionId)` — after resolution: winning token redeems for $1, losing for $0

The invariant is: **YES + NO always = $1**. This is enforced by the contract at all times.

Each outcome share is an ERC1155 token with a unique `tokenId` (called `positionId`).
The `tokenId` is derived from the `conditionId` and index set via elliptic curve math on BN128 —
it is not a simple hash.

### NegRisk Markets

Most active Polymarket markets use the **NegRisk** mechanism. This handles mutually exclusive
outcomes efficiently (e.g. "Who wins the election?" with many candidates).

Key difference from standard CTF:
- Collateral is not raw USDC.e but a **WrappedCollateral (wcol)** ERC20 deployed by the NegRiskAdapter
- `tokenId`s use `address(wcol)` instead of `address(USDC.e)` in their derivation
- Provides a `convertPositions()` mechanic for cross-market arbitrage

For our experiments: most live markets are NegRisk. Standard CTF markets still exist but are fewer.

### ERC1155 vs ERC20

Polymarket outcome shares are **ERC1155** (multi-token standard). This is great for on-chain
settlement but a problem for DeFi — lending protocols like Morpho, Aave, and Compound only
accept **ERC20** as collateral.

This is the gap Gondor fills.

### Morpho Blue

Morpho Blue is a minimalist lending protocol — a single immutable smart contract (singleton)
that lets anyone create isolated lending markets.

A market is defined by exactly five parameters:
```
(loanToken, collateralToken, oracle, IRM, LLTV)
```

Key properties:
- **Isolated risk** — each market is independent; a bad asset in one market cannot drain others
- **Permissionless** — anyone can create a market for any token pair
- **No governance** — the core contract is immutable and non-upgradeable
- **LLTV** (Liquidation Loan-to-Value) — if `borrowValue / collateralValue > LLTV`, anyone can liquidate

Lenders do not deposit into Morpho Blue directly in Gondor's setup. Instead they deposit into
**MetaMorpho vaults** (ERC4626), which allocate USDC across multiple Morpho Blue markets
based on a curator-defined strategy.

### How Gondor Combines All of This

Gondor is the bridge between Polymarket positions and Morpho Blue lending:

1. Takes a Polymarket ERC1155 outcome share (e.g. "Trump wins — YES token")
2. Wraps it into an ERC20 via `Wrapped1155Factory` (1:1, fully backed)
3. Creates a Morpho Blue market: `(USDC, wYES_ERC20, oracle, adaptiveCurveIRM, 62%)`
4. Oracle prices the wYES token using Polymarket's current market probability
5. Borrower deposits wYES → borrows USDC → uses USDC for other trades
6. If YES resolves, wYES = $1 and borrower repays USDC, redeems for $1
7. If NO resolves, wYES = $0 and borrower gets liquidated

The economic rationale: if you hold a high-confidence YES position (e.g. 90¢), it is
capital-inefficient to sit on it. Gondor lets you extract ~55¢ of USDC liquidity from it
while keeping exposure to the upside.

---

## The Full Flow

```
USDC
 │
 ▼  CTF.splitPosition()
YES (ERC1155) + NO (ERC1155)          ← 1 USDC → 1 YES + 1 NO share
 │
 ▼  Wrapped1155Factory.wrap()
wYES (ERC20, symbol: ylopwCTF)        ← ERC1155 tokenId → ERC20 1:1
 │
 ▼  Morpho.supplyCollateral()
Morpho Blue market                    ← deposit wYES as collateral
 │
 ▼  Morpho.borrow()
USDC                                  ← borrow up to ~50% of collateral value
```

---

## Step 1 — Split USDC into YES + NO Shares

Contract: **CTF (Conditional Token Framework)** — ERC1155

```
Address: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
```

Call:
```solidity
CTF.splitPosition(
    collateralToken: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174,  // USDC.e
    parentCollectionId: bytes32(0),
    conditionId: <conditionId>,
    partition: [1, 2],   // index sets for YES and NO
    amount: 1_000_000    // 1 USDC (6 decimals)
)
```

Result: you receive 1,000,000 units of tokenId_YES and 1,000,000 units of tokenId_NO.
Together they are always redeemable for exactly 1 USDC.

### TokenId computation (NegRisk markets — most Polymarket markets)

```
questionId   = negRiskMarketId with low byte = question index
conditionId  = keccak256(abi.encodePacked(negRiskAdapter, questionId, 2))
collectionId = CTHelpers.getCollectionId(bytes32(0), conditionId, 1)  // BN128 curve math
positionId   = keccak256(abi.encodePacked(wcol, collectionId))
```

NegRisk collateral is **wcol** (WrappedCollateral ERC20), not raw USDC.e:
```
NEG_RISK_ADAPTER:  0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
wcol address:      query negRiskAdapter.wcol()
```

---

## Step 2 — Wrap ERC1155 → ERC20

Contract: **Wrapped1155Factory**

```
Address: 0x194B27c5bb294319DE2B2DA40c10bd13484D7349
```

The factory deploys a new ERC20 contract for each (CTF address, tokenId) pair.
Each deployed ERC20 wraps exactly one ERC1155 tokenId at 1:1.

Gondor's production wrappers all share the symbol **`ylopwCTF`** (6 decimals).

Call:
```solidity
Wrapped1155Factory.wrap(
    multiToken: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045,  // CTF
    tokenId: <positionId>,
    amount: 1_000_000
)
// returns: 1_000_000 units of the ERC20 wrapper
```

### Known production wrapper addresses (ylopwCTF)

| Address | Morpho Market Supply |
|---|---|
| `0xB906C022C6F68E4FB14b1426C0760D52e65236b2` | $10 |
| `0x06fbE6eB25F600eF51a64039f46B7Dfddf2A4031` | $3.50 |
| `0xBb1e078990a2Eb958c9da42bfB4113D106C72856` | $0 |
| `0x91A8cbD409ff6D85dA828F2A22D66757eA083084` | $0 |
| `0x9900fF09E8be34cF4F9053007Fb7A610288D28C2` | $0 |
| `0x54875329eCcB92D40cb0A6c30d6C26FAe83E0EfB` | $0 |
| `0xEbAa2620e71565D0b4bFa67d373fE085BD47D743` | $0 |

*(Protocol is early-stage — small TVL)*

---

## Step 3 — Supply Collateral to Morpho Blue

Contract: **Morpho Blue** (singleton)

```
Address: 0x1bF0c2541F820E775182832f06c0B7Fc27A25f67
```

Each Gondor position market has params:
```
loanToken:       USDC  (0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359)
collateralToken: ylopwCTF (per-position ERC20 wrapper)
oracle:          custom oracle per market (prices collateral in USDC)
irm:             0xe675A2161D4a6E2de2eeD70ac98EEBf257FBF0B0  (Adaptive Curve IRM)
lltv:            62%  (liquidation LTV for production markets)
```

Call:
```solidity
// 1. Approve Morpho to spend your wYES tokens
wYES.approve(MORPHO_BLUE, amount)

// 2. Supply collateral
Morpho.supplyCollateral(
    marketParams,
    assets: amount,
    onBehalf: msg.sender,
    data: ""
)
```

---

## Step 4 — Borrow USDC

```solidity
Morpho.borrow(
    marketParams,
    assets: borrowAmount,   // USDC to borrow
    shares: 0,
    onBehalf: msg.sender,
    receiver: msg.sender
)
```

**Max borrow = collateral value × LLTV × oracle_price**

The oracle prices each wYES token as its current Polymarket probability × $1.
- A YES token at 80¢ → oracle price ≈ 0.80
- With 62% LLTV: max borrow = 0.80 × 0.62 = ~$0.496 per share

---

## Step 5 — Leverage Loop (optional)

Contract: **Leverage Manager**
```
Address: 0x05b059870dA1aCF525a7F7B304e724149d31D152
```

The Leverage Manager loops the borrow → buy → wrap → collateralize cycle
in a single transaction using Morpho's flash loan mechanism.

---

## Gondor Protocol Contracts (Polygon)

### Core

| Contract | Address |
|---|---|
| Morpho Blue | `0x1bF0c2541F820E775182832f06c0B7Fc27A25f67` |
| Wrapped1155Factory | `0x194B27c5bb294319DE2B2DA40c10bd13484D7349` |
| Leverage Manager | `0x05b059870dA1aCF525a7F7B304e724149d31D152` |
| Swapper Proxy | `0x30C0be3E113944005234E25345251ec86C5e3345` |
| JIT Reallocator | `0x4D80B61e78B0Eb397D5223496511C258D02EcdAA` |
| Dealer | `0x177c34e49Ef78Ba1F2456175DDC5Bc48d8ACad33` |
| Adaptive Curve IRM | `0xe675A2161D4a6E2de2eeD70ac98EEBf257FBF0B0` |
| Gondor Token | `0xF32dCaE6B0538B1f54a27d5e6421759ca16583FA` |

### Lending Vaults (USDC deposits earn yield)

| Vault | Address |
|---|---|
| Conservative V1 (MetaMorpho) | `0x724010b99cBDDD3421974716B32A9d902E1F0e70` |
| Moderate V1 (MetaMorpho) | `0xed5bD9AaD216b36991944f67439ECCd9253010B5` |
| Growth V1 (MetaMorpho) | `0x968fa99710D6031AA4569BF4C624332284321802` |
| Conservative V2 | `0x5a72c4624B846DBa03C772bD5219a2939455A9C3` |
| Moderate V2 | `0x8cF19873a6dfEe4721b860bC662bB7b563Bc2Aef` |
| Growth V2 | `0xCc1d87FBEF8eE3045671d121F95fB7d68D6b6C5E` |

### V2 Adapters

| Adapter | Address |
|---|---|
| Conservative Adapter V2 | `0xC90A2a0A438b124530d3a4c5e8838da2FcF9DbEe` |
| Moderate Adapter V2 | `0xb534D4EDd62aB24AF89d0E7c795BA979da6F7AA4` |
| Growth Adapter V2 | `0xcF0dD93944d6eA03e42c23e6877c3608A6459D08` |

### Polymarket Contracts (Polygon)

| Contract | Address |
|---|---|
| CTF (ERC1155 positions) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| NegRisk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| USDC (native) | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` |

---

## What's Possible On-Chain

| Action | Contracts involved |
|---|---|
| Split USDC → YES+NO | CTF.splitPosition |
| Merge YES+NO → USDC | CTF.mergePositions |
| Wrap YES → wYES (ERC20) | Wrapped1155Factory.wrap |
| Unwrap wYES → YES | Wrapped1155Factory.unwrap |
| Supply wYES as collateral | Morpho.supplyCollateral |
| Borrow USDC | Morpho.borrow |
| Repay USDC | Morpho.repay |
| Withdraw collateral | Morpho.withdrawCollateral |
| Leverage loop | LeverageManager |
| Supply USDC to vault | Vault.deposit (ERC4626) |
| Check oracle price | oracle.price() → uint256 (scaled 1e36) |

---

## Gondor Supabase Backend

Gondor runs a Supabase backend that stores their full pool registry with human-readable metadata.

```
URL: https://wdlpdsyzrtawgragahmt.supabase.co
Table: pools
```

This is the **only place** that maps a contract address to a readable market title:

| Field | Example |
|---|---|
| pool_name | "Russia x Ukraine ceasefire by March 31, 2026?" |
| outcome | "Yes" / "No" |
| fund_type | "conservative" / "moderate" / "growth" |
| token_address | `0xE065f32D94640FE3d491fcCC70BD08A5022e82CF` |
| morpho_market_id | `0x3aeb310afe0e86...` |
| oracle_address | `0x969c33D81dc8DBEA...` |

Everything else (market IDs, token addresses, oracle addresses) is derivable purely on-chain
via Morpho API + Wrapped1155Factory events. But without Supabase you get raw addresses with
no idea which Polymarket question they represent.

**To use it:** the anon key is embedded in Gondor's frontend JS bundle at `https://app.gondor.fi`.
It is intentionally public (Supabase's security model relies on Row Level Security, not key secrecy).
Fetch the bundle, extract the JWT starting with `eyJ`, and query:

```
GET https://wdlpdsyzrtawgragahmt.supabase.co/rest/v1/pools
    ?select=*&enabled=eq.true&resolved=eq.false
Headers:
    apikey: <anon_key>
    Authorization: Bearer <anon_key>
```

---

## Open Questions

- [ ] What Polymarket markets do current ylopwCTF wrappers correspond to?
      → Either scan Wrapped1155Factory TokenWrap events (needs Chainstack node)
         or fetch pool names from Gondor Supabase (fastest path)
- [ ] What is the oracle pricing mechanism exactly?
      → oracle.marketId() returns sequential int (1,2,3) — Gondor internal ID
- [ ] Is the Leverage Manager audited / what are the risks?
- [ ] V2 vaults vs V1 — what's different in VaultV2 vs MetaMorpho?
