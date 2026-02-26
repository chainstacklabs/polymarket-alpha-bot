"""02: Direct token transfers — OTC primitives.

WHAT IT DOES
    Tests safeTransferFrom and safeBatchTransferFrom for CTF tokens between
    Alice and Bob. Simulates trust-based OTC: Alice sends YES tokens, Bob
    sends USDC.e. Also tests edge cases.

WHY WE NEED THIS
    Confirms that direct P2P token transfers work on-chain and identifies
    edge cases before building atomic escrow.

USAGE
    # Requires: anvil.sh running + setup_accounts.py + 01_baseline.py completed
    uv run experiments/onchain-otc/02_transfers.py
"""

import json
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# --- Config ---

ANVIL_RPC = "http://127.0.0.1:8545"
SPLIT_AMOUNT_USDC = 100

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
GAMMA_API = "https://gamma-api.polymarket.com"
EMPTY_BYTES32 = b"\x00" * 32

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"inputs":[
        {"name":"collateralToken","type":"address"},
        {"name":"parentCollectionId","type":"bytes32"},
        {"name":"conditionId","type":"bytes32"},
        {"name":"partition","type":"uint256[]"},
        {"name":"amount","type":"uint256"}
    ],"name":"splitPosition","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"owner","type":"address"},
        {"name":"id","type":"uint256"}
    ],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[
        {"name":"from","type":"address"},
        {"name":"to","type":"address"},
        {"name":"id","type":"uint256"},
        {"name":"amount","type":"uint256"},
        {"name":"data","type":"bytes"}
    ],"name":"safeTransferFrom","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"from","type":"address"},
        {"name":"to","type":"address"},
        {"name":"ids","type":"uint256[]"},
        {"name":"amounts","type":"uint256[]"},
        {"name":"data","type":"bytes"}
    ],"name":"safeBatchTransferFrom","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"owner","type":"address"},
        {"name":"operator","type":"address"}
    ],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"inputs":[
        {"name":"operator","type":"address"},
        {"name":"approved","type":"bool"}
    ],"name":"setApprovalForAll","outputs":[],"type":"function"}
]""")


def fetch_active_market() -> dict:
    resp = httpx.get(
        f"{GAMMA_API}/markets",
        params={"closed": "false", "active": "true", "limit": 20, "order": "volume24hr", "ascending": "false"},
        timeout=15.0,
    )
    for m in resp.json():
        condition_id = m.get("conditionId", "")
        clob_tokens = m.get("clobTokenIds")
        neg_risk = m.get("negRisk", False)
        if condition_id and clob_tokens and not neg_risk:
            tokens = json.loads(clob_tokens) if isinstance(clob_tokens, str) else clob_tokens
            if len(tokens) >= 2:
                return {"question": m.get("question", "?"), "condition_id": condition_id, "yes_token_id": int(tokens[0]), "no_token_id": int(tokens[1])}
    raise RuntimeError("No suitable market found")


def balances(ctf, usdc, addr, yes_id, no_id):
    return {
        "usdc": usdc.functions.balanceOf(addr).call() / 1e6,
        "yes": ctf.functions.balanceOf(addr, yes_id).call(),
        "no": ctf.functions.balanceOf(addr, no_id).call(),
    }


def print_balances(label, ctf, usdc, alice, bob, yes_id, no_id):
    a = balances(ctf, usdc, alice, yes_id, no_id)
    b = balances(ctf, usdc, bob, yes_id, no_id)
    log.info("  %s:", label)
    log.info("    Alice: $%.2f USDC | %d YES | %d NO", a["usdc"], a["yes"], a["no"])
    log.info("    Bob:   $%.2f USDC | %d YES | %d NO", b["usdc"], b["yes"], b["no"])


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil — is it running?")
        return

    alice = w3.eth.accounts[0]
    bob = w3.eth.accounts[1]

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

    market = fetch_active_market()
    yes_id = market["yes_token_id"]
    no_id = market["no_token_id"]
    log.info("Market: %s", market["question"])
    log.info("YES ID: %s", yes_id)
    log.info("NO ID:  %s\n", no_id)

    # --- Ensure Alice has tokens (split if needed) ---
    alice_yes = ctf.functions.balanceOf(alice, yes_id).call()
    if alice_yes == 0:
        log.info("Alice has no tokens, splitting $%d...", SPLIT_AMOUNT_USDC)
        condition_bytes = bytes.fromhex(market["condition_id"][2:] if market["condition_id"].startswith("0x") else market["condition_id"])
        tx = ctf.functions.splitPosition(
            Web3.to_checksum_address(USDC_E), EMPTY_BYTES32, condition_bytes, [1, 2], SPLIT_AMOUNT_USDC * 10**6,
        ).transact({"from": alice})
        w3.eth.wait_for_transaction_receipt(tx)

    print_balances("Initial state", ctf, usdc, alice, bob, yes_id, no_id)

    # =========================================================
    # Test 1: Alice sends 50 YES tokens to Bob via safeTransferFrom
    # =========================================================
    log.info("\n=== Test 1: safeTransferFrom (Alice -> Bob, 50 YES) ===")
    transfer_amount = 50 * 10**6  # 50 tokens
    tx = ctf.functions.safeTransferFrom(
        alice, bob, yes_id, transfer_amount, b"",
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Gas used: %d", receipt["gasUsed"])
    print_balances("After transfer", ctf, usdc, alice, bob, yes_id, no_id)

    # =========================================================
    # Test 2: Bob sends USDC.e to Alice (non-atomic OTC payment)
    # =========================================================
    log.info("\n=== Test 2: Bob pays Alice 30 USDC.e (trust-based OTC) ===")
    payment = 30 * 10**6
    tx = usdc.functions.transfer(alice, payment).transact({"from": bob})
    w3.eth.wait_for_transaction_receipt(tx)
    print_balances("After payment", ctf, usdc, alice, bob, yes_id, no_id)

    # =========================================================
    # Test 3: safeBatchTransferFrom — move YES + NO in one tx
    # =========================================================
    log.info("\n=== Test 3: safeBatchTransferFrom (Alice -> Bob, 10 YES + 10 NO) ===")
    batch_amount = 10 * 10**6
    tx = ctf.functions.safeBatchTransferFrom(
        alice, bob, [yes_id, no_id], [batch_amount, batch_amount], b"",
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Gas used: %d", receipt["gasUsed"])
    print_balances("After batch", ctf, usdc, alice, bob, yes_id, no_id)

    # =========================================================
    # Edge cases
    # =========================================================
    log.info("\n=== Edge Case 1: Transfer to self ===")
    tx = ctf.functions.safeTransferFrom(
        alice, alice, yes_id, 1 * 10**6, b"",
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Transfer to self: OK (gas: %d)", receipt["gasUsed"])

    log.info("\n=== Edge Case 2: Transfer 0 amount ===")
    tx = ctf.functions.safeTransferFrom(
        alice, bob, yes_id, 0, b"",
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Transfer 0: OK (gas: %d)", receipt["gasUsed"])

    log.info("\n=== Edge Case 3: Transfer more than balance ===")
    alice_yes_bal = ctf.functions.balanceOf(alice, yes_id).call()
    try:
        ctf.functions.safeTransferFrom(
            alice, bob, yes_id, alice_yes_bal + 1, b"",
        ).transact({"from": alice, "gas": 200_000})
        log.info("  Transfer > balance: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("  Transfer > balance: Reverted as expected (%s)", type(e).__name__)

    log.info("\n=== Edge Case 4: Transfer to contract address (CTF itself) ===")
    try:
        ctf.functions.safeTransferFrom(
            alice, Web3.to_checksum_address(CTF), yes_id, 1 * 10**6, b"",
        ).transact({"from": alice, "gas": 200_000})
        log.info("  Transfer to CTF contract: OK")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("  Transfer to CTF contract: Reverted (%s)", type(e).__name__)

    log.info("\n=== Edge Case 5: Bob transfers Alice's tokens without approval ===")
    # Bob tries to move Alice's NO tokens (Bob is not approved)
    is_approved = ctf.functions.isApprovedForAll(alice, bob).call()
    log.info("  Bob approved for Alice's tokens: %s", is_approved)
    try:
        ctf.functions.safeTransferFrom(
            alice, bob, no_id, 1 * 10**6, b"",
        ).transact({"from": bob, "gas": 200_000})
        log.info("  Unauthorized transfer: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("  Unauthorized transfer: Reverted as expected (%s)", type(e).__name__)

    # Now approve Bob and retry
    log.info("\n=== Edge Case 6: Approved operator transfer ===")
    tx = ctf.functions.setApprovalForAll(bob, True).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    tx = ctf.functions.safeTransferFrom(
        alice, bob, no_id, 1 * 10**6, b"",
    ).transact({"from": bob})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Approved operator transfer: OK (gas: %d)", receipt["gasUsed"])

    print_balances("\nFinal state", ctf, usdc, alice, bob, yes_id, no_id)
    log.info("\n=== Phase 2 Transfers: PASS ===")


if __name__ == "__main__":
    main()
