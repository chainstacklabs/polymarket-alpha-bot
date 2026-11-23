# On-chain OTC experiments

Seven scripts that trade Polymarket positions **without the CLOB** on a local Anvil fork of Polygon: split and merge, direct transfers, an atomic escrow, NegRisk conversions, the V2 exchange's operator-only `matchOrders`, and signed intents with ERC-20 wrappers.

**Status:** all seven ran end to end on a fork on 2026-06-22, after Polymarket's April 2026 pUSD upgrade. They cover **standard (v1) CTF markets only** and pick only Gamma markets with `version: "v1"`. Protocol V2 markets (live since 2026-11-23: ExchangeV3, Router, PositionManager) aren't covered.

> **Test keys only.** The fork keeps chain ID 137 and the real contract addresses, so anything signed on it is valid on mainnet. Use Anvil's default accounts, never a real key.

## Running

Prerequisites: [Foundry](https://book.getfoundry.sh/getting-started/installation) (`anvil`, `forge`) and an **archive** Polygon RPC (`CHAINSTACK_NODE` in `.env` or the environment, or `FORK_URL`). Free-tier RPCs fail on `eth_getStorageAt`.

From the repo root:

```bash
(cd experiments/onchain-otc/contracts && forge build)        # escrow, intents, wrapper
./experiments/onchain-otc/anvil.sh                            # terminal 1: fork on 127.0.0.1:8545
uv run --frozen --project backend experiments/onchain-otc/setup_accounts.py   # fund test accounts
uv run --frozen --project backend experiments/onchain-otc/01_baseline.py      # then 02 … 07
```

`setup_accounts.py` funds Alice and Bob with USDC.e from a whale, wraps some into pUSD, and clears EIP-7702 code on the test accounts. Restart Anvil for a clean state.

## Scripts

| # | Script | What it shows | Result |
|---|---|---|---|
| 1 | `01_baseline.py` | Split USDC.e into YES + NO and merge back | Lossless; split ~133–170k gas, merge ~120k |
| 2 | `02_transfers.py` | ERC-1155 transfers, batch transfers, approvals | ~57k gas per transfer; batching saves ~37% |
| 3 | `03_escrow.py` | Atomic YES ↔ USDC.e swap through `OTCEscrow.sol` | Create ~247k, fill ~91–108k; expiry, restricted taker, and double fill enforced |
| 4 | `04_negrisk.py` | NegRisk split, `convertPositions`, merge | Convert ~375–432k on a 4-outcome market; 5M+ on large ones |
| 5 | `05_operator.py` | Sign V2 orders and settle with `matchOrders` as the operator | ~210k gas; non-operators are rejected |
| 6 | `06_negrisk_trading.py` | Get directional NegRisk exposure without the CLOB | Split + convert alone is break-even |
| 7 | `07_intents.py` | Wrap positions as ERC-20s and settle EIP-712 intents | `fillIntent` ~140k; wrap 57k (650k for the first wrap of a token) |

Gas figures are from the 2026-06-22 run.

## Contracts

| Contract | Address | Role |
|---|---|---|
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | CTF collateral: positions are USDC.e-backed |
| pUSD | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | Cash leg of V2 exchange orders |
| CTF | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | ERC-1155 YES/NO tokens |
| CTF Exchange V2 | `0xE111180000d2663C0091e4f400237545B87B996B` | Binary markets, operator-only `matchOrders` |
| NegRisk CTF Exchange V2 | `0xe2222d279d744050d28e00520010520000310F59` | Multi-outcome markets |
| NegRisk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` | NegRisk split, merge, convert |

Ours, in `contracts/` (experiments, not audited): `OTCEscrow.sol`, `IntentSettlement.sol`, `Wrapped1155.sol` (one ERC-20 wrapper per token ID, created with `new`).

## Findings

- **Split and merge are lossless.** The CTF charges no fee; only gas.
- **Positions are still USDC.e-backed after the pUSD upgrade.** `getCtfCollateral()` is USDC.e, `getCollateral()` is pUSD. Splitting with pUSD on the CTF mints token IDs no order book knows.
- **Direct OTC works.** A transfer costs ~$0.003, but it needs trust; the escrow makes the swap atomic for ~$0.02.
- **The NegRisk break-even trap.** Split + convert leaves YES on every outcome, which pays exactly $1 whatever wins. Directional exposure needs selling the unwanted side to someone.
- **`convertPositions` uses the on-chain question order,** which isn't Gamma's `markets[]` order. Split all questions before converting. k NOs converted return (k − 1) × amount in collateral.
- **The V2 exchange is operator-only.** `fillOrder` is gone; `matchOrders` takes a leading `conditionId` and operator-set fees. Domain version "2", 11-field order struct, no on-chain cancel (`pauseUser` instead).
- **Intents need no operator.** Wrapped positions plus EIP-712 intents settle permissionlessly. Finding counterparties is the missing piece.

## Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `safeTransferFrom` to an Anvil account reverts | Real Polygon has EIP-7702 code on Anvil's default addresses | `anvil_setCode(addr, "0x")` (`setup_accounts.py` does this) |
| web3.py rejects blocks | Polygon's PoA `extraData` | `ExtraDataToPOAMiddleware` |
| A reverted tx raises nothing | With explicit `gas`, web3.py skips simulation | Check `receipt["status"]`, or `.call()` first to see the reason |
| Impersonated whale can't send | It has no POL on the fork | `anvil_setBalance` first |
| Wrong token IDs from a split | NegRisk `conditionId` on the plain CTF path | Split NegRisk markets through the adapter |
| Deploys fail with 403 | Non-archive RPC | Use an archive node |
| Gamma fields are strings or arrays | Depends on the endpoint | `json.loads(x) if isinstance(x, str) else x` |
| Large NegRisk converts run out of gas | On-chain question count exceeds the API's (padding) | Use small markets |
