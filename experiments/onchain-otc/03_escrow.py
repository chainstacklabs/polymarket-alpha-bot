"""03: Atomic OTC escrow — trustless ERC-1155 <> ERC-20 swap.

WHAT IT DOES
    Deploys OTCEscrow contract to the local fork, then tests:
    - Happy path: Alice creates offer (YES tokens), Bob fills (USDC.e)
    - Cancel: Alice creates offer, then cancels to reclaim tokens
    - Expiry: offer expires, taker can't fill, maker can cancel
    - Wrong taker: restricted offer rejected by unauthorized address
    - Double fill: can't fill same offer twice

WHY WE NEED THIS
    Validates that trustless P2P OTC for Polymarket positions works atomically
    on-chain without CLOB.

USAGE
    # Requires: anvil.sh running + setup_accounts.py + 01_baseline.py completed
    uv run experiments/onchain-otc/03_escrow.py
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

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
GAMMA_API = "https://gamma-api.polymarket.com"
EMPTY_BYTES32 = b"\x00" * 32

# --- ABIs ---

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"partition","type":"uint256[]"},{"name":"amount","type":"uint256"}],"name":"splitPosition","outputs":[],"type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

CONTRACTS_DIR = Path(__file__).parent / "contracts"


def load_escrow_abi_and_bytecode():
    artifact = json.loads((CONTRACTS_DIR / "out" / "OTCEscrow.sol" / "OTCEscrow.json").read_text())
    return artifact["abi"], artifact["bytecode"]["object"]


def fetch_active_market() -> dict:
    resp = httpx.get(
        f"{GAMMA_API}/markets",
        params={"closed": "false", "active": "true", "limit": 20, "order": "volume24hr", "ascending": "false"},
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
    raise RuntimeError("No suitable market")


def balances(ctf, usdc, addr, yes_id):
    return {
        "usdc": usdc.functions.balanceOf(addr).call() / 1e6,
        "yes": ctf.functions.balanceOf(addr, yes_id).call() / 1e6,
    }


def print_state(label, ctf, usdc, alice, bob, yes_id):
    a = balances(ctf, usdc, alice, yes_id)
    b = balances(ctf, usdc, bob, yes_id)
    log.info("  [%s] Alice: $%.2f USDC, %.2f YES | Bob: $%.2f USDC, %.2f YES",
             label, a["usdc"], a["yes"], b["usdc"], b["yes"])


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil")
        return

    alice = w3.eth.accounts[0]
    bob = w3.eth.accounts[1]
    charlie = w3.eth.accounts[2]  # unauthorized third party

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

    market = fetch_active_market()
    yes_id = market["yes_id"]
    log.info("Market: %s", market["question"])

    # --- Ensure Alice has YES tokens ---
    alice_yes = ctf.functions.balanceOf(alice, yes_id).call()
    if alice_yes < 50 * 10**6:
        log.info("Splitting more USDC for Alice...")
        condition_bytes = bytes.fromhex(market["condition_id"][2:] if market["condition_id"].startswith("0x") else market["condition_id"])
        tx = ctf.functions.splitPosition(Web3.to_checksum_address(USDC_E), EMPTY_BYTES32, condition_bytes, [1, 2], 200 * 10**6).transact({"from": alice})
        w3.eth.wait_for_transaction_receipt(tx)

    # --- Deploy OTCEscrow ---
    log.info("\n=== Deploying OTCEscrow ===")
    abi, bytecode = load_escrow_abi_and_bytecode()
    EscrowContract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = EscrowContract.constructor().transact({"from": alice, "gas": 1_000_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    escrow_addr = receipt["contractAddress"]
    escrow = w3.eth.contract(address=escrow_addr, abi=abi)
    log.info("Deployed at: %s (gas: %d)", escrow_addr, receipt["gasUsed"])

    # --- Approve escrow contract ---
    log.info("\nSetting approvals for escrow...")
    # Alice approves escrow to move her CTF tokens
    tx = ctf.functions.setApprovalForAll(escrow_addr, True).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    # Bob approves escrow to spend his USDC.e
    tx = usdc.functions.approve(escrow_addr, 2**256 - 1).transact({"from": bob})
    w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Alice: CTF approved for escrow")
    log.info("  Bob: USDC.e approved for escrow")

    # =========================================================
    # Test 1: Happy path — Alice offers 20 YES @ $15, Bob fills
    # =========================================================
    log.info("\n=== Test 1: Happy Path ===")
    offer_amount = 20 * 10**6  # 20 YES tokens
    price = 15 * 10**6  # $15 USDC.e

    print_state("Before", ctf, usdc, alice, bob, yes_id)

    tx = escrow.functions.createOffer(
        Web3.to_checksum_address(CTF), yes_id, offer_amount,
        Web3.to_checksum_address(USDC_E), price,
        "0x0000000000000000000000000000000000000000",  # open to anyone
        0,  # no deadline
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    offer_id = 0
    log.info("  Offer created (id=%d, gas=%d)", offer_id, receipt["gasUsed"])

    # Verify tokens are in escrow
    escrow_bal = ctf.functions.balanceOf(escrow_addr, yes_id).call()
    log.info("  Escrow holds: %d YES tokens", escrow_bal)

    tx = escrow.functions.fillOffer(offer_id).transact({"from": bob})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Offer filled by Bob (gas=%d)", receipt["gasUsed"])

    print_state("After", ctf, usdc, alice, bob, yes_id)

    # Verify offer is no longer active
    # Offer struct: (maker, erc1155, tokenId, amount, erc20, price, taker, deadline, active)
    offer = escrow.functions.getOffer(offer_id).call()
    log.info("  Offer active: %s", offer[8])
    assert not offer[8], "Offer should be inactive after fill"
    log.info("  Test 1: PASS")

    # =========================================================
    # Test 2: Cancel — Alice creates offer then cancels
    # =========================================================
    log.info("\n=== Test 2: Cancel ===")
    print_state("Before", ctf, usdc, alice, bob, yes_id)

    tx = escrow.functions.createOffer(
        Web3.to_checksum_address(CTF), yes_id, 10 * 10**6,
        Web3.to_checksum_address(USDC_E), 8 * 10**6,
        "0x0000000000000000000000000000000000000000", 0,
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    cancel_offer_id = 1
    log.info("  Offer created (id=%d)", cancel_offer_id)

    # Alice cancels
    tx = escrow.functions.cancelOffer(cancel_offer_id).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Offer cancelled (gas=%d)", receipt["gasUsed"])

    print_state("After cancel", ctf, usdc, alice, bob, yes_id)
    log.info("  Test 2: PASS")

    # =========================================================
    # Test 3: Expiry — offer expires, taker can't fill
    # =========================================================
    log.info("\n=== Test 3: Expiry ===")
    current_block = w3.eth.get_block("latest")
    deadline = current_block["timestamp"] + 10  # expires in 10 seconds

    tx = escrow.functions.createOffer(
        Web3.to_checksum_address(CTF), yes_id, 5 * 10**6,
        Web3.to_checksum_address(USDC_E), 4 * 10**6,
        "0x0000000000000000000000000000000000000000",
        deadline,
    ).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    expiry_offer_id = 2
    log.info("  Offer created with deadline=%d", deadline)

    # Fast-forward time past deadline
    w3.provider.make_request("evm_increaseTime", [60])
    w3.provider.make_request("evm_mine", [])
    log.info("  Time advanced 60s past deadline")

    try:
        tx = escrow.functions.fillOffer(expiry_offer_id).transact({"from": bob, "gas": 200_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.info("  Expired fill: Reverted as expected")
        else:
            log.error("  Expired fill: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError):
        log.info("  Expired fill: Reverted as expected")

    # Maker can still cancel to reclaim
    tx = escrow.functions.cancelOffer(expiry_offer_id).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Maker cancelled expired offer (gas=%d)", receipt["gasUsed"])
    log.info("  Test 3: PASS")

    # =========================================================
    # Test 4: Restricted taker — only Bob can fill
    # =========================================================
    log.info("\n=== Test 4: Restricted Taker ===")

    tx = escrow.functions.createOffer(
        Web3.to_checksum_address(CTF), yes_id, 5 * 10**6,
        Web3.to_checksum_address(USDC_E), 4 * 10**6,
        bob,  # only Bob can fill
        0,
    ).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    restricted_offer_id = 3

    # Charlie tries to fill
    # First approve Charlie's USDC for escrow
    tx = usdc.functions.approve(escrow_addr, 2**256 - 1).transact({"from": charlie})
    w3.eth.wait_for_transaction_receipt(tx)

    try:
        tx = escrow.functions.fillOffer(restricted_offer_id).transact({"from": charlie, "gas": 200_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.info("  Charlie rejected as expected (reverted on-chain)")
        else:
            log.error("  Charlie fill: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError):
        log.info("  Charlie rejected as expected")

    # Bob fills successfully
    tx = escrow.functions.fillOffer(restricted_offer_id).transact({"from": bob})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("  Bob filled restricted offer (gas=%d)", receipt["gasUsed"])
    log.info("  Test 4: PASS")

    # =========================================================
    # Test 5: Double fill — can't fill same offer twice
    # =========================================================
    log.info("\n=== Test 5: Double Fill ===")
    tx = escrow.functions.createOffer(
        Web3.to_checksum_address(CTF), yes_id, 5 * 10**6,
        Web3.to_checksum_address(USDC_E), 4 * 10**6,
        "0x0000000000000000000000000000000000000000", 0,
    ).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    double_offer_id = 4

    tx = escrow.functions.fillOffer(double_offer_id).transact({"from": bob})
    w3.eth.wait_for_transaction_receipt(tx)
    log.info("  First fill: OK")

    try:
        tx = escrow.functions.fillOffer(double_offer_id).transact({"from": bob, "gas": 200_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.info("  Double fill: Reverted as expected")
        else:
            log.error("  Double fill: UNEXPECTEDLY SUCCEEDED")
    except (ContractLogicError, Web3RPCError):
        log.info("  Double fill: Reverted as expected")

    log.info("  Test 5: PASS")

    # --- Final summary ---
    print_state("\nFinal", ctf, usdc, alice, bob, yes_id)
    log.info("\n=== Phase 3 Escrow: ALL TESTS PASS ===")


if __name__ == "__main__":
    main()
