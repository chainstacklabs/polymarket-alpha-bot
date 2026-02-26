"""05: Operator deep-dive — impersonate operator, call fillOrder/matchOrders.

WHAT IT DOES
    Impersonates the Polymarket operator EOA, crafts EIP-712 signed orders,
    and tests fillOrder and matchOrders directly. Explores the operator-gated
    exchange functions that normally only PM's backend can call.

WHY WE NEED THIS
    Understanding operator mechanics reveals what's possible and what's
    gated. If we can replicate order matching, we understand the full
    on-chain settlement layer.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    uv run experiments/onchain-otc/05_operator.py
"""

import json
import logging
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

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Known Polymarket operator (found from live chain tx analysis)
PM_OPERATOR = "0x768408F252d4Ea905E5d4225F4B29FaaBa651579"

# Anvil pre-funded private keys (deterministic)
ALICE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
BOB_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

EMPTY_BYTES32 = b"\x00" * 32

# Side enum: 0 = BUY, 1 = SELL
SIDE_BUY = 0
SIDE_SELL = 1

# SignatureType enum: 0 = EOA, 1 = POLY_PROXY, 2 = POLY_GNOSIS_SAFE
SIG_TYPE_EOA = 0

# EIP-712 domain for CTF Exchange
EIP712_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": 137,
    "verifyingContract": CTF_EXCHANGE,
}

ORDER_TYPE = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}

# Minimal Exchange ABI
EXCHANGE_ABI = json.loads("""[
    {"inputs":[{"name":"usr","type":"address"}],"name":"operators","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"usr","type":"address"}],"name":"admins","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"admin_","type":"address"}],"name":"addAdmin","outputs":[],"type":"function"},
    {"inputs":[{"name":"operator_","type":"address"}],"name":"addOperator","outputs":[],"type":"function"},
    {"inputs":[
        {"components":[
            {"name":"salt","type":"uint256"},
            {"name":"maker","type":"address"},
            {"name":"signer","type":"address"},
            {"name":"taker","type":"address"},
            {"name":"tokenId","type":"uint256"},
            {"name":"makerAmount","type":"uint256"},
            {"name":"takerAmount","type":"uint256"},
            {"name":"expiration","type":"uint256"},
            {"name":"nonce","type":"uint256"},
            {"name":"feeRateBps","type":"uint256"},
            {"name":"side","type":"uint8"},
            {"name":"signatureType","type":"uint8"},
            {"name":"signature","type":"bytes"}
        ],"name":"order","type":"tuple"},
        {"name":"fillAmount","type":"uint256"}
    ],"name":"fillOrder","outputs":[],"type":"function"},
    {"inputs":[
        {"components":[
            {"name":"salt","type":"uint256"},
            {"name":"maker","type":"address"},
            {"name":"signer","type":"address"},
            {"name":"taker","type":"address"},
            {"name":"tokenId","type":"uint256"},
            {"name":"makerAmount","type":"uint256"},
            {"name":"takerAmount","type":"uint256"},
            {"name":"expiration","type":"uint256"},
            {"name":"nonce","type":"uint256"},
            {"name":"feeRateBps","type":"uint256"},
            {"name":"side","type":"uint8"},
            {"name":"signatureType","type":"uint8"},
            {"name":"signature","type":"bytes"}
        ],"name":"takerOrder","type":"tuple"},
        {"components":[
            {"name":"salt","type":"uint256"},
            {"name":"maker","type":"address"},
            {"name":"signer","type":"address"},
            {"name":"taker","type":"address"},
            {"name":"tokenId","type":"uint256"},
            {"name":"makerAmount","type":"uint256"},
            {"name":"takerAmount","type":"uint256"},
            {"name":"expiration","type":"uint256"},
            {"name":"nonce","type":"uint256"},
            {"name":"feeRateBps","type":"uint256"},
            {"name":"side","type":"uint8"},
            {"name":"signatureType","type":"uint8"},
            {"name":"signature","type":"bytes"}
        ],"name":"makerOrders","type":"tuple[]"},
        {"name":"takerFillAmount","type":"uint256"},
        {"name":"makerFillAmounts","type":"uint256[]"}
    ],"name":"matchOrders","outputs":[],"type":"function"},
    {"inputs":[],"name":"paused","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"partition","type":"uint256[]"},{"name":"amount","type":"uint256"}],"name":"splitPosition","outputs":[],"type":"function"}
]""")

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")


def sign_order(order_data: dict, private_key: str) -> bytes:
    """Sign an order using EIP-712 typed data."""
    signed = Account.sign_typed_data(
        private_key,
        domain_data=EIP712_DOMAIN,
        message_types=ORDER_TYPE,
        message_data=order_data,
    )
    return signed.signature


def make_order(maker, signer, token_id, maker_amount, taker_amount, side, private_key, salt=1, taker="0x0000000000000000000000000000000000000000"):
    """Create and sign an order."""
    order_data = {
        "salt": salt,
        "maker": maker,
        "signer": signer,
        "taker": taker,
        "tokenId": token_id,
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": 0,  # no expiration
        "nonce": 0,
        "feeRateBps": 0,  # no fees for testing
        "side": side,
        "signatureType": SIG_TYPE_EOA,
    }
    sig = sign_order(order_data, private_key)
    return {**order_data, "signature": sig}


def fetch_active_market() -> dict:
    resp = httpx.get(
        f"{GAMMA_API}/markets",
        params={"closed": "false", "active": "true", "limit": 10, "order": "volume24hr", "ascending": "false"},
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
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    market = fetch_active_market()
    yes_id = market["yes_id"]
    no_id = market["no_id"]
    log.info("Market: %s", market["question"])
    log.info("YES ID: %s", yes_id)

    # =========================================================
    # Step 1: Check operator status and make ourselves operator
    # =========================================================
    log.info("\n=== Step 1: Operator Access ===")

    operator = Web3.to_checksum_address(PM_OPERATOR)
    w3.provider.make_request("anvil_impersonateAccount", [operator])
    w3.provider.make_request("anvil_setBalance", [operator, hex(10**18)])

    is_op = exchange.functions.operators(operator).call()
    log.info("PM operator %s: operators=%d", PM_OPERATOR, is_op)

    # Check if there's an admin we can use
    is_admin = exchange.functions.admins(operator).call()
    log.info("PM operator is admin: %d", is_admin)

    # Make Alice an operator via storage manipulation
    # Storage layout (found by brute-force): slot 0 = scalar, slot 1 = admins, slot 2 = operators
    # mapping(address => uint256) storage key = keccak256(abi.encode(address, slot_index))
    alice_padded = alice.lower().replace("0x", "").zfill(64)
    one_value = "0x" + "0" * 63 + "1"

    # Set Alice as admin (mapping at slot 1)
    admin_slot = Web3.solidity_keccak(["bytes"], [bytes.fromhex(alice_padded + "0" * 63 + "1")])
    w3.provider.make_request("anvil_setStorageAt", [CTF_EXCHANGE, "0x" + admin_slot.hex(), one_value])

    # Set Alice as operator (mapping at slot 2)
    op_slot = Web3.solidity_keccak(["bytes"], [bytes.fromhex(alice_padded + "0" * 63 + "2")])
    w3.provider.make_request("anvil_setStorageAt", [CTF_EXCHANGE, "0x" + op_slot.hex(), one_value])

    is_op_now = exchange.functions.operators(alice).call()
    is_admin_now = exchange.functions.admins(alice).call()
    log.info("Alice operator=%d, admin=%d (set via storage manipulation)", is_op_now, is_admin_now)

    w3.provider.make_request("anvil_stopImpersonatingAccount", [operator])

    # =========================================================
    # Step 2: Token setup + USDC approval for exchange
    # =========================================================
    log.info("\n=== Step 2: Token Setup ===")

    erc20_approve_abi = json.loads(
        '[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]'
    )
    usdc_full = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=erc20_approve_abi + ERC20_ABI)

    # USDC.e must be approved for the exchange (setup_accounts.py only approves CTF + adapter)
    max_uint = 2**256 - 1
    for _, addr in [("Alice", alice), ("Bob", bob)]:
        tx = usdc_full.functions.approve(Web3.to_checksum_address(CTF_EXCHANGE), max_uint).transact({"from": addr})
        w3.eth.wait_for_transaction_receipt(tx)
    log.info("USDC.e approved for CTF Exchange (Alice & Bob)")

    # Split $100 → YES + NO for both
    condition_bytes = bytes.fromhex(
        market["condition_id"][2:] if market["condition_id"].startswith("0x") else market["condition_id"]
    )
    for _, addr in [("Alice", alice), ("Bob", bob)]:
        bal = ctf.functions.balanceOf(addr, yes_id).call()
        if bal < 50 * 10**6:
            tx = ctf.functions.splitPosition(
                Web3.to_checksum_address(USDC_E), EMPTY_BYTES32, condition_bytes, [1, 2], 100 * 10**6
            ).transact({"from": addr, "gas": 500_000})
            w3.eth.wait_for_transaction_receipt(tx)

    alice_yes = ctf.functions.balanceOf(alice, yes_id).call()
    alice_no = ctf.functions.balanceOf(alice, no_id).call()
    bob_yes = ctf.functions.balanceOf(bob, yes_id).call()
    bob_no = ctf.functions.balanceOf(bob, no_id).call()
    alice_usdc_start = usdc.functions.balanceOf(alice).call()
    bob_usdc_start = usdc.functions.balanceOf(bob).call()
    log.info("Alice: %d YES, %d NO, $%.2f USDC", alice_yes, alice_no, alice_usdc_start / 1e6)
    log.info("Bob:   %d YES, %d NO, $%.2f USDC", bob_yes, bob_no, bob_usdc_start / 1e6)

    def order_tuple(o):
        return (o["salt"], o["maker"], o["signer"], o["taker"], o["tokenId"],
                o["makerAmount"], o["takerAmount"], o["expiration"], o["nonce"],
                o["feeRateBps"], o["side"], o["signatureType"], o["signature"])

    # =========================================================
    # Step 3: matchOrders — Alice sells YES, Bob buys YES
    # =========================================================
    # matchOrders is the primary mechanism: two signed orders, operator just submits the match
    log.info("\n=== Step 3: matchOrders (Alice sells YES, Bob buys YES) ===")
    log.info("Alice SELL 30 YES @ 0.50 | Bob BUY 30 YES @ 0.50")

    # Alice: SELL 30 YES tokens for 15 USDC (price = 0.50)
    alice_sell = make_order(
        maker=alice, signer=alice, token_id=yes_id,
        maker_amount=30 * 10**6, taker_amount=15 * 10**6,
        side=SIDE_SELL, private_key=ALICE_KEY, salt=100,
    )

    # Bob: BUY 30 YES tokens with 15 USDC (price = 0.50)
    bob_buy = make_order(
        maker=bob, signer=bob, token_id=yes_id,
        maker_amount=15 * 10**6, taker_amount=30 * 10**6,
        side=SIDE_BUY, private_key=BOB_KEY, salt=200,
    )

    try:
        tx = exchange.functions.matchOrders(
            order_tuple(bob_buy),       # taker order (BUY)
            [order_tuple(alice_sell)],   # maker orders (SELL)
            15 * 10**6,                  # taker fill amount (Bob's USDC)
            [30 * 10**6],               # maker fill amounts (Alice's YES tokens)
        ).transact({"from": alice, "gas": 1_000_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 1:
            log.info("matchOrders SUCCESS (gas: %d)", receipt["gasUsed"])
            for name, addr in [("Alice", alice), ("Bob", bob)]:
                y = ctf.functions.balanceOf(addr, yes_id).call()
                u = usdc.functions.balanceOf(addr).call()
                log.info("  %s: %d YES, $%.2f USDC", name, y, u / 1e6)
        else:
            log.info("matchOrders REVERTED (tx: %s)", receipt["transactionHash"].hex())
    except (ContractLogicError, Web3RPCError) as e:
        log.error("matchOrders failed: %s", e)

    # =========================================================
    # Step 4: fillOrder — Bob signs a BUY order, Alice (operator) fills as counterparty
    # =========================================================
    # In fillOrder, msg.sender is the counterparty. Alice (operator) fills Bob's BUY order.
    log.info("\n=== Step 4: fillOrder (Alice fills Bob's BUY order) ===")
    log.info("Bob BUY 20 YES @ 0.50 | Alice (operator+seller) fills it")

    bob_buy2 = make_order(
        maker=bob, signer=bob, token_id=yes_id,
        maker_amount=10 * 10**6, taker_amount=20 * 10**6,
        side=SIDE_BUY, private_key=BOB_KEY, salt=300,
    )

    try:
        # Alice (operator + counterparty) fills Bob's BUY order
        # Exchange will transfer: USDC from Bob → Alice, YES tokens from Alice → Bob
        tx = exchange.functions.fillOrder(
            order_tuple(bob_buy2),
            10 * 10**6,  # fill Bob's full maker amount (USDC)
        ).transact({"from": alice, "gas": 500_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 1:
            log.info("fillOrder SUCCESS (gas: %d)", receipt["gasUsed"])
            for name, addr in [("Alice", alice), ("Bob", bob)]:
                y = ctf.functions.balanceOf(addr, yes_id).call()
                u = usdc.functions.balanceOf(addr).call()
                log.info("  %s: %d YES, $%.2f USDC", name, y, u / 1e6)
        else:
            log.info("fillOrder REVERTED (tx: %s)", receipt["transactionHash"].hex())
    except (ContractLogicError, Web3RPCError) as e:
        log.error("fillOrder failed: %s", e)

    # =========================================================
    # Step 5: Verify non-operator can't call fillOrder
    # =========================================================
    log.info("\n=== Step 5: Non-operator access denied ===")
    charlie = w3.eth.accounts[2]
    w3.provider.make_request("anvil_setCode", [charlie, "0x"])

    bob_buy3 = make_order(
        maker=bob, signer=bob, token_id=yes_id,
        maker_amount=5 * 10**6, taker_amount=10 * 10**6,
        side=SIDE_BUY, private_key=BOB_KEY, salt=400,
    )

    try:
        tx = exchange.functions.fillOrder(
            order_tuple(bob_buy3), 5 * 10**6,
        ).transact({"from": charlie, "gas": 200_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.info("Non-operator fillOrder: REJECTED (reverted on-chain)")
        else:
            log.info("Non-operator fillOrder: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("Non-operator fillOrder: REJECTED (%s)", type(e).__name__)

    log.info("\n=== Phase 5 Operator: COMPLETE ===")


if __name__ == "__main__":
    main()
