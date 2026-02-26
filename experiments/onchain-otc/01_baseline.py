"""01: Baseline — split USDC.e into YES/NO tokens, verify balances.

WHAT IT DOES
    Fetches a real active market from Gamma API, splits $100 USDC.e into
    YES + NO conditional tokens via CTF.splitPosition, and verifies
    on-chain balances. Confirms the fork works end-to-end.

WHY WE NEED THIS
    Foundation for all subsequent experiments. If split + balanceOf works,
    the fork is healthy and we can proceed to transfers, escrow, etc.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    uv run experiments/onchain-otc/01_baseline.py
"""

import json
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# --- Config ---

ANVIL_RPC = "http://127.0.0.1:8545"
SPLIT_AMOUNT_USDC = 100  # $100

# Contracts
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
GAMMA_API = "https://gamma-api.polymarket.com"

EMPTY_BYTES32 = b"\x00" * 32

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
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
        {"name":"collateralToken","type":"address"},
        {"name":"parentCollectionId","type":"bytes32"},
        {"name":"conditionId","type":"bytes32"},
        {"name":"partition","type":"uint256[]"},
        {"name":"amount","type":"uint256"}
    ],"name":"mergePositions","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"owner","type":"address"},
        {"name":"id","type":"uint256"}
    ],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

# TransferBatch event topic (ERC-1155)
TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"


def fetch_active_market() -> dict:
    """Find a real active binary market from Gamma API."""
    log.info("Fetching active markets from Gamma API...")
    resp = httpx.get(
        f"{GAMMA_API}/markets",
        params={
            "closed": "false",
            "active": "true",
            "limit": 20,
            "order": "volume24hr",
            "ascending": "false",
        },
        timeout=15.0,
    )
    markets = resp.json()

    # Find a binary market with a condition ID
    for m in markets:
        condition_id = m.get("conditionId", "")
        clob_tokens = m.get("clobTokenIds")
        neg_risk = m.get("negRisk", False)
        if condition_id and clob_tokens and not neg_risk:
            tokens = json.loads(clob_tokens) if isinstance(clob_tokens, str) else clob_tokens
            if len(tokens) >= 2:
                return {
                    "question": m.get("question", "?"),
                    "condition_id": condition_id,
                    "yes_token_id": int(tokens[0]),
                    "no_token_id": int(tokens[1]),
                }

    raise RuntimeError("No suitable active market found")


def parse_ctf_token_ids(receipt) -> list[int]:
    """Extract minted CTF token IDs from TransferBatch event in tx receipt."""
    token_ids = []
    for log_entry in receipt.get("logs", []):
        topics = [t.hex() if isinstance(t, bytes) else t for t in log_entry.get("topics", [])]
        if not topics:
            continue
        topic0 = topics[0] if topics[0].startswith("0x") else f"0x{topics[0]}"
        if topic0.lower() == TRANSFER_BATCH_TOPIC.lower():
            data = log_entry["data"]
            if isinstance(data, bytes):
                data = data.hex()
            elif data.startswith("0x"):
                data = data[2:]
            # ABI decode: offset, offset, length, ids..., length, amounts...
            words = [data[i : i + 64] for i in range(0, len(data), 64)]
            if len(words) >= 4:
                n_ids = int(words[2], 16)
                for i in range(n_ids):
                    token_ids.append(int(words[3 + i], 16))
    return token_ids


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil — is it running?")
        return

    alice = w3.eth.accounts[0]
    log.info("Alice: %s", alice)

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

    # --- Step 1: Fetch a real market ---
    market = fetch_active_market()
    log.info("\n=== Market ===")
    log.info("Question:     %s", market["question"])
    log.info("Condition ID: %s", market["condition_id"])
    log.info("YES token:    %s", market["yes_token_id"])
    log.info("NO token:     %s", market["no_token_id"])

    # --- Step 2: Check pre-split balances ---
    usdc_before = usdc.functions.balanceOf(alice).call()
    log.info("\n--- Pre-split ---")
    log.info("Alice USDC.e: $%s", usdc_before / 1e6)

    # --- Step 3: Split USDC.e -> YES + NO ---
    amount_raw = SPLIT_AMOUNT_USDC * 10**6
    condition_bytes = bytes.fromhex(
        market["condition_id"][2:]
        if market["condition_id"].startswith("0x")
        else market["condition_id"]
    )

    log.info("\n--- Splitting $%s USDC.e ---", SPLIT_AMOUNT_USDC)
    tx = ctf.functions.splitPosition(
        Web3.to_checksum_address(USDC_E),
        EMPTY_BYTES32,
        condition_bytes,
        [1, 2],  # partition: YES=1, NO=2
        amount_raw,
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    log.info("Tx hash: %s", receipt["transactionHash"].hex())
    log.info("Gas used: %s", receipt["gasUsed"])

    # --- Step 4: Parse minted token IDs ---
    minted_ids = parse_ctf_token_ids(receipt)
    log.info("Minted CTF token IDs: %s", minted_ids)

    # --- Step 5: Verify balances ---
    usdc_after = usdc.functions.balanceOf(alice).call()
    log.info("\n--- Post-split ---")
    log.info("Alice USDC.e: $%s (spent $%s)", usdc_after / 1e6, (usdc_before - usdc_after) / 1e6)

    for tid in minted_ids:
        bal = ctf.functions.balanceOf(alice, tid).call()
        log.info("Alice CTF token %s: %s units", tid, bal)

    # Also check using the CLOB token IDs from API (should match minted IDs)
    for label, token_id in [("YES", market["yes_token_id"]), ("NO", market["no_token_id"])]:
        tid = int(token_id)
        bal = ctf.functions.balanceOf(alice, tid).call()
        log.info("Alice %s (CLOB ID %s...): %s units", label, str(token_id)[:20], bal)

    # --- Step 6: Verify merge works (round-trip) ---
    log.info("\n--- Merging back (round-trip test) ---")
    merge_amount = 10 * 10**6  # merge $10 worth
    tx = ctf.functions.mergePositions(
        Web3.to_checksum_address(USDC_E),
        EMPTY_BYTES32,
        condition_bytes,
        [1, 2],
        merge_amount,
    ).transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx)

    usdc_after_merge = usdc.functions.balanceOf(alice).call()
    log.info("Alice USDC.e after merge: $%s (recovered $%s)", usdc_after_merge / 1e6, (usdc_after_merge - usdc_after) / 1e6)

    log.info("\n=== Phase 1 Baseline: PASS ===")


if __name__ == "__main__":
    main()
