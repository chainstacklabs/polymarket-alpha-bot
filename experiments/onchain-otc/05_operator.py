"""05: Operator deep-dive — impersonate operator, settle via matchOrders (V2).

WHAT IT DOES
    Impersonates a live Polymarket operator, crafts EIP-712 signed V2 orders,
    self-verifies them against the exchange (`hashOrder` + `validateOrderSignature`),
    and settles a trade via `matchOrders` on the V2 CTF Exchange.

V2 NOTES (verified on-chain 2026-06-22 against the Sourcify ABI)
    - Exchange is V2 `0xE111...`, EIP-712 domain version "2" (confirmed via
      `eip712Domain()`).
    - Dual collateral: `getCtfCollateral() == USDC.e` (positions are USDC.e-backed)
      and `getCollateral() == pUSD` (order maker/taker amounts settle in pUSD).
      So sellers split USDC.e to get tradeable tokens; buyers pay pUSD.
    - V2 order struct DROPS taker/expiration/nonce/feeRateBps and ADDS
      timestamp (ms) / metadata (bytes32) / builder (bytes32). Verified against
      the SDK (`polymarket.actions.orders.typed_data`) and the on-chain ABI.
    - **`fillOrder` was REMOVED in V2** — `matchOrders` is the only settlement
      path. Its signature gained a leading `conditionId` and trailing
      `takerFeeAmount` / `makerFeeAmounts` (fees are operator-supplied, not in
      the order; the per-order `feeRateBps` is gone).
    - Auth is `isOperator(address)` / `isAdmin(address)` (bool), not V1's
      uint256 mappings. The legacy operator `0x768408...` is still an operator,
      so we impersonate it rather than manipulate storage.
    - On-chain order cancel was removed; use operator `pauseUser` / `unpauseUser`
      (+ `isUserPaused`). This scenario does not exercise it.
    - These test orders are signed by plain Anvil EOAs (signatureType 0). The
      production deposit-wallet path uses signatureType 3 (ERC-7739
      TypedDataSign) via the CLOB HTTP API.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    cd backend && uv run ../experiments/onchain-otc/05_operator.py
"""

import json
import logging
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# --- Config ---

ANVIL_RPC = "http://127.0.0.1:8545"
GAMMA_API = "https://gamma-api.polymarket.com"

# Dual collateral (verified): CTF positions are USDC.e-backed; the exchange
# settles order maker/taker amounts in pUSD.
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # getCtfCollateral() — split/merge
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # getCollateral() — order cash leg
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"  # V2

# Legacy operator EOA — still `isOperator() == true` on the V2 exchange.
PM_OPERATOR = "0x768408F252d4Ea905E5d4225F4B29FaaBa651579"

# Anvil pre-funded private keys (deterministic)
ALICE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
BOB_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

EMPTY_BYTES32 = b"\x00" * 32

# Side enum: 0 = BUY, 1 = SELL
SIDE_BUY = 0
SIDE_SELL = 1

# SignatureType enum: 0 = EOA, 1 = POLY_PROXY, 2 = POLY_GNOSIS_SAFE, 3 = DEPOSIT_WALLET
SIG_TYPE_EOA = 0

# EIP-712 domain — confirmed on-chain via eip712Domain(): version "2".
EIP712_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "2",
    "chainId": 137,
    "verifyingContract": CTF_EXCHANGE,
}

# V2 order struct (verified against the SDK and the on-chain ABI).
ORDER_TYPE = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
        {"name": "timestamp", "type": "uint256"},
        {"name": "metadata", "type": "bytes32"},
        {"name": "builder", "type": "bytes32"},
    ]
}

# On-chain Order tuple = the 11 signed fields + the signature bytes.
_ORDER_COMPONENTS = [
    {"name": "salt", "type": "uint256"},
    {"name": "maker", "type": "address"},
    {"name": "signer", "type": "address"},
    {"name": "tokenId", "type": "uint256"},
    {"name": "makerAmount", "type": "uint256"},
    {"name": "takerAmount", "type": "uint256"},
    {"name": "side", "type": "uint8"},
    {"name": "signatureType", "type": "uint8"},
    {"name": "timestamp", "type": "uint256"},
    {"name": "metadata", "type": "bytes32"},
    {"name": "builder", "type": "bytes32"},
    {"name": "signature", "type": "bytes"},
]

# V2 CTF Exchange ABI (function signatures verified against the Sourcify ABI).
EXCHANGE_ABI = [
    {"inputs": [{"name": "_usr", "type": "address"}], "name": "isOperator", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "_usr", "type": "address"}], "name": "isAdmin", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getCollateral", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getCtfCollateral", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"components": _ORDER_COMPONENTS, "name": "order", "type": "tuple"}], "name": "hashOrder", "outputs": [{"name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"components": _ORDER_COMPONENTS, "name": "order", "type": "tuple"}], "name": "validateOrder", "outputs": [], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "orderHash", "type": "bytes32"}, {"components": _ORDER_COMPONENTS, "name": "order", "type": "tuple"}], "name": "validateOrderSignature", "outputs": [], "stateMutability": "view", "type": "function"},
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"components": _ORDER_COMPONENTS, "name": "takerOrder", "type": "tuple"},
            {"components": _ORDER_COMPONENTS, "name": "makerOrders", "type": "tuple[]"},
            {"name": "takerFillAmount", "type": "uint256"},
            {"name": "makerFillAmounts", "type": "uint256[]"},
            {"name": "takerFeeAmount", "type": "uint256"},
            {"name": "makerFeeAmounts", "type": "uint256[]"},
        ],
        "name": "matchOrders",
        "outputs": [],
        "type": "function",
    },
    {"inputs": [], "name": "paused", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
]

CTF_ABI = json.loads("""[
    {"inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"partition","type":"uint256[]"},{"name":"amount","type":"uint256"}],"name":"splitPosition","outputs":[],"type":"function"}
]""")

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")


def sign_order(order_data: dict, private_key: str) -> bytes:
    """Sign a V2 order using EIP-712 typed data."""
    signed = Account.sign_typed_data(
        private_key,
        domain_data=EIP712_DOMAIN,
        message_types=ORDER_TYPE,
        message_data=order_data,
    )
    return signed.signature


def make_order(maker, signer, token_id, maker_amount, taker_amount, side, private_key, timestamp, salt=1):
    """Create and sign a V2 order."""
    order_data = {
        "salt": salt,
        "maker": maker,
        "signer": signer,
        "tokenId": token_id,
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "side": side,
        "signatureType": SIG_TYPE_EOA,
        "timestamp": timestamp,
        "metadata": EMPTY_BYTES32,
        "builder": EMPTY_BYTES32,
    }
    sig = sign_order(order_data, private_key)
    return {**order_data, "signature": sig}


def order_tuple(o):
    return (
        o["salt"], o["maker"], o["signer"], o["tokenId"],
        o["makerAmount"], o["takerAmount"], o["side"], o["signatureType"],
        o["timestamp"], o["metadata"], o["builder"], o["signature"],
    )


def fetch_active_market() -> dict:
    resp = httpx.get(
        f"{GAMMA_API}/markets",
        params={"closed": "false", "active": "true", "limit": 50, "order": "volume24hr", "ascending": "false"},
        timeout=15.0,
    )
    for m in resp.json():
        cid = m.get("conditionId", "")
        tokens = m.get("clobTokenIds")
        neg_risk = m.get("negRisk", False)
        if cid and tokens and not neg_risk:
            t = json.loads(tokens) if isinstance(tokens, str) else tokens
            if len(t) >= 2:
                return {"question": m.get("question", "?"), "condition_id": cid, "yes_id": int(t[0]), "no_id": int(t[1])}
    raise RuntimeError("No non-NegRisk market found")


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil")
        return

    alice = w3.eth.accounts[0]
    bob = w3.eth.accounts[1]

    exchange = w3.eth.contract(address=Web3.to_checksum_address(CTF_EXCHANGE), abi=EXCHANGE_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)

    market = fetch_active_market()
    yes_id = market["yes_id"]
    condition_bytes = bytes.fromhex(
        market["condition_id"][2:] if market["condition_id"].startswith("0x") else market["condition_id"]
    )
    log.info("Market: %s", market["question"])
    log.info("YES ID: %s", yes_id)

    now_ms = int(time.time() * 1000)  # V2 orders carry a millisecond timestamp

    # =========================================================
    # Step 1: Operator access + collateral model
    # =========================================================
    log.info("\n=== Step 1: Operator Access & Collateral ===")
    log.info("getCtfCollateral = %s (USDC.e expected)", exchange.functions.getCtfCollateral().call())
    log.info("getCollateral    = %s (pUSD expected)", exchange.functions.getCollateral().call())

    operator = Web3.to_checksum_address(PM_OPERATOR)
    log.info("isOperator(%s) = %s", PM_OPERATOR, exchange.functions.isOperator(operator).call())
    w3.provider.make_request("anvil_impersonateAccount", [operator])
    w3.provider.make_request("anvil_setBalance", [operator, hex(10**18)])

    # =========================================================
    # Step 2: Token setup — Alice (seller) splits USDC.e → YES + NO
    # =========================================================
    log.info("\n=== Step 2: Token Setup ===")
    if ctf.functions.balanceOf(alice, yes_id).call() < 50 * 10**6:
        tx = ctf.functions.splitPosition(
            Web3.to_checksum_address(USDC_E), EMPTY_BYTES32, condition_bytes, [1, 2], 100 * 10**6
        ).transact({"from": alice, "gas": 500_000})
        w3.eth.wait_for_transaction_receipt(tx)

    log.info("Alice: %d YES, $%.2f pUSD", ctf.functions.balanceOf(alice, yes_id).call(), pusd.functions.balanceOf(alice).call() / 1e6)
    log.info("Bob:   %d YES, $%.2f pUSD", ctf.functions.balanceOf(bob, yes_id).call(), pusd.functions.balanceOf(bob).call() / 1e6)

    # =========================================================
    # Step 3: Build + self-verify orders, then matchOrders
    # =========================================================
    log.info("\n=== Step 3: matchOrders (Alice sells YES, Bob buys YES @ 0.50) ===")

    # Alice (maker): SELL 30 YES for 15 pUSD
    alice_sell = make_order(
        maker=alice, signer=alice, token_id=yes_id,
        maker_amount=30 * 10**6, taker_amount=15 * 10**6,
        side=SIDE_SELL, private_key=ALICE_KEY, timestamp=now_ms, salt=100,
    )
    # Bob (taker): BUY 30 YES with 15 pUSD
    bob_buy = make_order(
        maker=bob, signer=bob, token_id=yes_id,
        maker_amount=15 * 10**6, taker_amount=30 * 10**6,
        side=SIDE_BUY, private_key=BOB_KEY, timestamp=now_ms, salt=200,
    )

    # Self-verify both orders against the exchange before submitting.
    for label, o in [("Alice SELL", alice_sell), ("Bob BUY", bob_buy)]:
        order_hash = exchange.functions.hashOrder(order_tuple(o)).call()
        try:
            exchange.functions.validateOrderSignature(order_hash, order_tuple(o)).call()
            log.info("  %s: signature valid (hash %s)", label, order_hash.hex()[:18])
        except (ContractLogicError, Web3RPCError) as e:
            log.error("  %s: signature INVALID — %s", label, e)
            return

    try:
        tx = exchange.functions.matchOrders(
            condition_bytes,
            order_tuple(bob_buy),        # taker order (BUY)
            [order_tuple(alice_sell)],   # maker orders (SELL)
            15 * 10**6,                  # taker fill amount (Bob's pUSD)
            [30 * 10**6],               # maker fill amounts (Alice's YES)
            0,                           # taker fee amount
            [0],                         # maker fee amounts
        ).transact({"from": operator, "gas": 1_500_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 1:
            log.info("matchOrders SUCCESS (gas: %d)", receipt["gasUsed"])
            for name, addr in [("Alice", alice), ("Bob", bob)]:
                y = ctf.functions.balanceOf(addr, yes_id).call()
                u = pusd.functions.balanceOf(addr).call()
                log.info("  %s: %d YES, $%.2f pUSD", name, y, u / 1e6)
        else:
            log.info("matchOrders REVERTED (tx: %s)", receipt["transactionHash"].hex())
    except (ContractLogicError, Web3RPCError) as e:
        log.error("matchOrders failed: %s", e)

    w3.provider.make_request("anvil_stopImpersonatingAccount", [operator])

    # =========================================================
    # Step 4: Verify non-operator can't call matchOrders
    # =========================================================
    log.info("\n=== Step 4: Non-operator access denied ===")
    charlie = w3.eth.accounts[2]
    w3.provider.make_request("anvil_setCode", [charlie, "0x"])
    log.info("isOperator(charlie) = %s", exchange.functions.isOperator(charlie).call())

    alice_sell2 = make_order(
        maker=alice, signer=alice, token_id=yes_id,
        maker_amount=10 * 10**6, taker_amount=5 * 10**6,
        side=SIDE_SELL, private_key=ALICE_KEY, timestamp=now_ms, salt=300,
    )
    bob_buy2 = make_order(
        maker=bob, signer=bob, token_id=yes_id,
        maker_amount=5 * 10**6, taker_amount=10 * 10**6,
        side=SIDE_BUY, private_key=BOB_KEY, timestamp=now_ms, salt=400,
    )
    try:
        tx = exchange.functions.matchOrders(
            condition_bytes, order_tuple(bob_buy2), [order_tuple(alice_sell2)],
            5 * 10**6, [10 * 10**6], 0, [0],
        ).transact({"from": charlie, "gas": 500_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.info("Non-operator matchOrders: REJECTED (reverted on-chain)")
        else:
            log.info("Non-operator matchOrders: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("Non-operator matchOrders: REJECTED (%s)", type(e).__name__)

    log.info("\n=== Phase 5 Operator: COMPLETE ===")


if __name__ == "__main__":
    main()
