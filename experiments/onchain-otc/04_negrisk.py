"""04: NegRisk conversions — convert NO bundles to YES + collateral.

WHAT IT DOES
    Finds a real multi-outcome NegRisk market, splits into YES/NO tokens
    via the NegRiskAdapter, then tests convertPositions: converting NO
    tokens into YES tokens + collateral. Calculates PnL.

WHY WE NEED THIS
    convertPositions is permissionless and could enable on-chain arbitrage
    in multi-outcome markets without touching CLOB.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    uv run experiments/onchain-otc/04_negrisk.py
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
GAMMA_API = "https://gamma-api.polymarket.com"

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
EMPTY_BYTES32 = b"\x00" * 32

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"}
]""")

# NegRiskAdapter ABI — splitPosition, mergePositions, convertPositions
NEG_RISK_ABI = json.loads("""[
    {"inputs":[
        {"name":"conditionId","type":"bytes32"},
        {"name":"amount","type":"uint256"}
    ],"name":"splitPosition","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"conditionId","type":"bytes32"},
        {"name":"amount","type":"uint256"}
    ],"name":"mergePositions","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"marketId","type":"bytes32"},
        {"name":"indexSet","type":"uint256"},
        {"name":"amount","type":"uint256"}
    ],"name":"convertPositions","outputs":[],"type":"function"},
    {"inputs":[
        {"name":"","type":"bytes32"}
    ],"name":"getQuestionCount","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")


def fetch_negrisk_event() -> dict:
    """Find a live multi-outcome NegRisk market with interesting prices."""
    resp = httpx.get(
        f"{GAMMA_API}/events",
        params={"closed": "false", "active": "true", "limit": 20, "order": "volume24hr", "ascending": "false"},
        timeout=15.0,
    )
    for e in resp.json():
        if not e.get("negRisk"):
            continue
        markets = e.get("markets", [])
        if not (3 <= len(markets) <= 15):
            continue
        # Need at least 2 markets with non-trivial prices (skip huge markets)
        active = []
        for m in markets:
            prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices", "[]"), str) else m.get("outcomePrices", [])
            yes_price = float(prices[0]) if prices else 0
            if 0.01 < yes_price < 0.99:
                active.append(m)
        if len(active) >= 2:
            parsed_markets = []
            for m in markets:
                tokens = json.loads(m.get("clobTokenIds", "[]")) if isinstance(m.get("clobTokenIds", "[]"), str) else m.get("clobTokenIds", [])
                prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices", "[]"), str) else m.get("outcomePrices", [])
                if len(tokens) < 2 or not prices:
                    continue
                parsed_markets.append({
                    "title": m.get("groupItemTitle", "?"),
                    "condition_id": m.get("conditionId", ""),
                    "yes_token_id": int(tokens[0]),
                    "no_token_id": int(tokens[1]),
                    "yes_price": float(prices[0]),
                })
            return {
                "title": e.get("title", "?"),
                "event_id": e.get("id"),
                "neg_risk_market_id": markets[0].get("negRiskMarketID", ""),
                "markets": parsed_markets,
            }
    raise RuntimeError("No suitable NegRisk event found")


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil")
        return

    alice = w3.eth.accounts[0]
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)
    adapter = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI)

    event = fetch_negrisk_event()
    markets = event["markets"]
    market_id = event["neg_risk_market_id"]
    market_id_bytes = bytes.fromhex(market_id[2:] if market_id.startswith("0x") else market_id)

    log.info("=== NegRisk Event: %s ===", event["title"])
    log.info("Market ID: %s", market_id)
    log.info("Outcomes: %d\n", len(markets))
    for i, m in enumerate(markets):
        log.info("  [%d] %-25s YES=%.4f  cid=%s...", i, m["title"][:25], m["yes_price"], m["condition_id"][:16])

    # --- Check question count on-chain ---
    try:
        q_count = adapter.functions.getQuestionCount(market_id_bytes).call()
        log.info("\nOn-chain question count: %d", q_count)
    except Exception as e:
        log.info("\ngetQuestionCount failed: %s (may not exist on this adapter version)", e)

    # USDC.e -> NegRiskAdapter approval is set by setup_accounts.py

    # --- Step 1: Split via NegRiskAdapter on MULTIPLE questions ---
    # convertPositions needs NO tokens across multiple questions
    split_amount = 10 * 10**6  # $10 per question (smaller — splitting ALL questions)
    num_to_split = len(markets)  # must split ALL questions for convertPositions

    usdc_before = usdc.functions.balanceOf(alice).call()
    log.info("\n--- Step 1: Split $%d each on %d questions via NegRiskAdapter ---", split_amount // 10**6, num_to_split)
    log.info("Alice USDC.e before: $%.2f", usdc_before / 1e6)

    for i in range(num_to_split):
        m = markets[i]
        condition_bytes = bytes.fromhex(
            m["condition_id"][2:] if m["condition_id"].startswith("0x") else m["condition_id"]
        )
        try:
            tx = adapter.functions.splitPosition(condition_bytes, split_amount).transact({"from": alice, "gas": 500_000})
            receipt = w3.eth.wait_for_transaction_receipt(tx)
            if receipt["status"] == 0:
                log.error("  Split [%d] %s: REVERTED", i, m["title"][:20])
            else:
                log.info("  Split [%d] %s: OK (gas: %d)", i, m["title"][:20], receipt["gasUsed"])
        except (ContractLogicError, Web3RPCError) as e:
            log.error("  Split [%d] failed: %s", i, e)

    usdc_after = usdc.functions.balanceOf(alice).call()
    log.info("Alice USDC.e after: $%.2f (spent $%.2f)", usdc_after / 1e6, (usdc_before - usdc_after) / 1e6)

    # --- Step 2: Check all token balances ---
    log.info("\n--- Step 2: Token balances after split ---")
    for i, m in enumerate(markets):
        y = ctf.functions.balanceOf(alice, m["yes_token_id"]).call()
        n = ctf.functions.balanceOf(alice, m["no_token_id"]).call()
        if y > 0 or n > 0:
            log.info("  [%d] %-25s YES=%d  NO=%d", i, m["title"][:25], y, n)

    # =========================================================
    # Step 3: convertPositions — convert NO tokens to YES + collateral
    # =========================================================
    log.info("\n--- Step 3: convertPositions ---")
    # indexSet is a bitmask: bit i set means "include question i's NO tokens"
    # We split questions 0..4, so we have NO tokens for those.
    # Convert all 5: indexSet = 0b11111 = 31
    # This burns NO tokens for questions 0-4 and gives us:
    #   - YES tokens for all questions NOT in the indexSet
    #   - Collateral = amount * (num_questions_in_set - 1)
    # Convert a single question's NO tokens — indexSet = 1 (bit 0 = question 0)
    # The adapter will burn NO tokens for ALL other questions (not in indexSet)
    # and give us YES tokens for question 0 + collateral
    index_set = 1  # convert using question 0
    convert_amount = 5 * 10**6  # convert 5 units (we have 10 per question)

    bits_in_set = bin(index_set).count("1")
    log.info("indexSet=%d (binary: %s, %d bit(s) set) — converting via question 0",
             index_set, bin(index_set), bits_in_set)
    expected_collateral = (convert_amount // 10**6) * max(bits_in_set - 1, 0)
    log.info("Expected collateral: $%d (amount × (bits_set - 1) = %d × %d)",
             expected_collateral, convert_amount // 10**6, max(bits_in_set - 1, 0))

    usdc_before_convert = usdc.functions.balanceOf(alice).call()

    # Record all YES balances before conversion
    yes_before = {}
    no_before = {}
    for i, m in enumerate(markets):
        yes_before[i] = ctf.functions.balanceOf(alice, m["yes_token_id"]).call()
        no_before[i] = ctf.functions.balanceOf(alice, m["no_token_id"]).call()

    try:
        tx = adapter.functions.convertPositions(market_id_bytes, index_set, convert_amount).transact({"from": alice, "gas": 20_000_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 0:
            log.error("convertPositions reverted on-chain! Need to investigate NegRiskAdapter internals.")
        else:
            log.info("Convert tx: %s (gas: %d)", receipt["transactionHash"].hex(), receipt["gasUsed"])
    except (ContractLogicError, Web3RPCError) as e:
        log.error("convertPositions failed: %s", e)

    usdc_after_convert = usdc.functions.balanceOf(alice).call()
    collateral_received = (usdc_after_convert - usdc_before_convert) / 1e6
    log.info("Collateral received: $%.2f", collateral_received)

    log.info("\nToken balance changes after conversion:")
    for i, m in enumerate(markets[:num_to_split + 2]):
        y = ctf.functions.balanceOf(alice, m["yes_token_id"]).call()
        n = ctf.functions.balanceOf(alice, m["no_token_id"]).call()
        y_delta = y - yes_before[i]
        n_delta = n - no_before.get(i, 0)
        if y_delta != 0 or n_delta != 0:
            log.info("  [%d] %-25s YES=%d (%+d)  NO=%d (%+d)",
                     i, m["title"][:25], y, y_delta, n, n_delta)

    # =========================================================
    # Step 4: PnL analysis — is conversion profitable at current prices?
    # =========================================================
    log.info("\n--- Step 4: PnL Analysis ---")
    log.info("In a %d-outcome NegRisk market:", len(markets))
    log.info("  Split $1 → 1 YES + 1 NO for each question")
    log.info("  Convert 1 NO from question X → YES for all others + (n-1) collateral")
    log.info("")

    # Theoretical: if you hold NO on the most expensive outcome,
    # converting gives you YES on all cheaper outcomes + collateral
    sorted_markets = sorted(enumerate(markets), key=lambda x: x[1]["yes_price"], reverse=True)
    top = sorted_markets[0]
    rest = sorted_markets[1:]

    log.info("Most expensive outcome: [%d] %s (YES=%.4f, NO=%.4f)",
             top[0], top[1]["title"][:25], top[1]["yes_price"], 1 - top[1]["yes_price"])
    log.info("If you buy NO on this outcome at $%.4f...", 1 - top[1]["yes_price"])
    log.info("  Convert gives you:")
    total_yes_value = sum(m["yes_price"] for _, m in rest)
    n_outcomes = len(markets)
    log.info("    Collateral: $%.4f (from %d-1 merged positions)", (n_outcomes - 1) * 1.0 / n_outcomes, n_outcomes)
    log.info("    YES tokens on %d other outcomes worth: $%.4f", len(rest), total_yes_value)
    log.info("  Cost of NO: $%.4f", 1 - top[1]["yes_price"])
    log.info("  Gross value: $%.4f + $%.4f = $%.4f", (n_outcomes - 1) / n_outcomes, total_yes_value, (n_outcomes - 1) / n_outcomes + total_yes_value)

    # --- Step 5: Merge round-trip ---
    log.info("\n--- Step 5: Merge back (round-trip) ---")
    merge_amount = 10 * 10**6
    usdc_before_merge = usdc.functions.balanceOf(alice).call()
    cond0 = bytes.fromhex(markets[0]["condition_id"][2:] if markets[0]["condition_id"].startswith("0x") else markets[0]["condition_id"])
    try:
        tx = adapter.functions.mergePositions(cond0, merge_amount).transact({"from": alice, "gas": 500_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        if receipt["status"] == 1:
            usdc_after_merge = usdc.functions.balanceOf(alice).call()
            log.info("Merge: recovered $%.2f (gas: %d)", (usdc_after_merge - usdc_before_merge) / 1e6, receipt["gasUsed"])
        else:
            log.info("Merge reverted on-chain (may not have enough YES+NO pairs)")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("Merge failed: %s (may need YES+NO for same question)", e)

    log.info("\n=== Phase 4 NegRisk: COMPLETE ===")


if __name__ == "__main__":
    main()
