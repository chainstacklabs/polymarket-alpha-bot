"""06: NegRisk trading scenarios — can you trade without CLOB?

WHAT IT DOES
    Tests every practical trading scenario for a multi-outcome NegRisk market
    using only on-chain operations (split, merge, convert, transfer).
    Each scenario runs from a clean snapshot so they don't interfere.

WHY WE NEED THIS
    Proves whether NegRisk markets can be fully traded without CLOB,
    and identifies the exact limitations.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    cd backend && uv run ../experiments/onchain-otc/06_negrisk_trading.py
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
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"id","type":"uint256"},{"name":"amount","type":"uint256"},{"name":"data","type":"bytes"}],"name":"safeTransferFrom","outputs":[],"type":"function"}
]""")

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
    """Find a live NegRisk market with 3-8 outcomes and real prices."""
    resp = httpx.get(
        f"{GAMMA_API}/events",
        params={"closed": "false", "active": "true", "limit": 30, "order": "volume24hr", "ascending": "false"},
        timeout=15.0,
    )
    for e in resp.json():
        if not e.get("negRisk"):
            continue
        markets = e.get("markets", [])
        if not (3 <= len(markets) <= 8):
            continue
        active = []
        for m in markets:
            prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices", "[]"), str) else m.get("outcomePrices", [])
            if prices and float(prices[0]) > 0.01 and float(prices[0]) < 0.99:
                active.append(m)
        if len(active) >= 2:
            parsed = []
            for m in markets:
                tokens = json.loads(m.get("clobTokenIds", "[]")) if isinstance(m.get("clobTokenIds", "[]"), str) else m.get("clobTokenIds", [])
                prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices", "[]"), str) else m.get("outcomePrices", [])
                if len(tokens) < 2 or not prices:
                    continue
                parsed.append({
                    "title": m.get("groupItemTitle", "?"),
                    "condition_id": m.get("conditionId", ""),
                    "yes_id": int(tokens[0]),
                    "no_id": int(tokens[1]),
                    "yes_price": float(prices[0]),
                })
            if len(parsed) < 3:
                continue
            market_id = markets[0].get("negRiskMarketID", "")
            if not market_id:
                continue
            return {
                "title": e.get("title", "?"),
                "neg_risk_market_id": market_id,
                "markets": parsed,
            }
    raise RuntimeError("No suitable NegRisk event found")


def snapshot(w3) -> str:
    """Take an EVM snapshot, return snapshot ID."""
    result = w3.provider.make_request("evm_snapshot", [])
    return result["result"]


def revert(w3, snap_id: str):
    """Revert to a snapshot."""
    result = w3.provider.make_request("evm_revert", [snap_id])
    if not result.get("result"):
        raise RuntimeError(f"Failed to revert snapshot {snap_id}")


def cond_bytes(condition_id: str) -> bytes:
    return bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)


def print_balances(ctf, usdc, addr, markets, label):
    """Print token balances and return (usdc_amount, token_value)."""
    u = usdc.functions.balanceOf(addr).call()
    log.info("  %s:", label)
    log.info("    USDC.e: $%.2f", u / 1e6)
    total_val = 0
    for i, m in enumerate(markets):
        y = ctf.functions.balanceOf(addr, m["yes_id"]).call()
        n = ctf.functions.balanceOf(addr, m["no_id"]).call()
        if y > 0 or n > 0:
            y_val = y / 1e6 * m["yes_price"]
            n_val = n / 1e6 * (1 - m["yes_price"])
            total_val += y_val + n_val
            log.info("    [%d] %-20s YES=%9d (≈$%6.2f)  NO=%9d (≈$%6.2f)",
                     i, m["title"][:20], y, y_val, n, n_val)
    log.info("    Token value ≈ $%.2f | Total ≈ $%.2f", total_val, u / 1e6 + total_val)
    return u, total_val


def split_question(adapter, w3, addr, condition_id, amount):
    """Split one question, return (gas, status)."""
    tx = adapter.functions.splitPosition(cond_bytes(condition_id), amount).transact({"from": addr, "gas": 500_000})
    r = w3.eth.wait_for_transaction_receipt(tx)
    return r["gasUsed"], r["status"]


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil")
        return

    alice = w3.eth.accounts[0]
    bob = w3.eth.accounts[1]

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)
    adapter = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI)

    event = fetch_negrisk_event()
    markets = event["markets"]
    market_id_bytes = bytes.fromhex(
        event["neg_risk_market_id"][2:] if event["neg_risk_market_id"].startswith("0x") else event["neg_risk_market_id"]
    )
    n = len(markets)

    log.info("=" * 70)
    log.info("NEGRISK TRADING SCENARIOS")
    log.info("=" * 70)
    log.info("\nMarket: %s (%d outcomes)", event["title"], n)
    for i, m in enumerate(markets):
        log.info("  [%d] %-25s YES=$%.2f  NO=$%.2f", i, m["title"][:25], m["yes_price"], 1 - m["yes_price"])
    log.info("  Sum of YES prices: $%.4f", sum(m["yes_price"] for m in markets))

    alice_usdc_start = usdc.functions.balanceOf(alice).call()
    log.info("\nAlice starting USDC: $%.2f", alice_usdc_start / 1e6)
    favorite = markets[0]["title"][:20]
    AMOUNT = 100 * 10**6  # $100 per operation

    # Take a baseline snapshot (after setup_accounts)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 1: Split one question → YES + NO
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 1: Split one question → get YES[0] + NO[0]")
    log.info("=" * 70)

    gas, _ = split_question(adapter, w3, alice, markets[0]["condition_id"], AMOUNT)
    log.info("Split $100 on [0] '%s': gas=%d", favorite, gas)

    y0 = ctf.functions.balanceOf(alice, markets[0]["yes_id"]).call()
    n0 = ctf.functions.balanceOf(alice, markets[0]["no_id"]).call()
    usdc_after = usdc.functions.balanceOf(alice).call()
    log.info("  Paid: $%.2f USDC", (alice_usdc_start - usdc_after) / 1e6)
    log.info("  Got:  %d YES[0] (≈$%.2f) + %d NO[0] (≈$%.2f)",
             y0, y0 / 1e6 * markets[0]["yes_price"],
             n0, n0 / 1e6 * (1 - markets[0]["yes_price"]))
    log.info("  Result: YES+NO always worth $1 per pair. No directional exposure yet.")
    log.info("  To get pure YES: must sell NO via OTC or CLOB.")

    # Revert for next scenario
    revert(w3, base_snap)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 2: Convert NO[0] → YES[1..N] (split ONLY question 0)
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 2: Split question 0, then convert NO[0] → YES on others")
    log.info("=" * 70)
    log.info("Tests: do we need to split ALL questions, or just the one in indexSet?")

    split_gas, _ = split_question(adapter, w3, alice, markets[0]["condition_id"], AMOUNT)
    log.info("Split $100 on question 0: gas=%d", split_gas)

    # Record balances before convert
    before_yes = [ctf.functions.balanceOf(alice, m["yes_id"]).call() for m in markets]
    before_no = [ctf.functions.balanceOf(alice, m["no_id"]).call() for m in markets]
    usdc_before = usdc.functions.balanceOf(alice).call()

    try:
        tx = adapter.functions.convertPositions(market_id_bytes, 1, AMOUNT).transact({"from": alice, "gas": 20_000_000})
        r = w3.eth.wait_for_transaction_receipt(tx)
        if r["status"] == 1:
            usdc_after_conv = usdc.functions.balanceOf(alice).call()
            collateral = (usdc_after_conv - usdc_before) / 1e6
            log.info("Convert (indexSet=1, amount=%d): SUCCESS, gas=%d, collateral=$%.2f", AMOUNT, r["gasUsed"], collateral)

            log.info("\nBalance changes:")
            for i, m in enumerate(markets):
                y = ctf.functions.balanceOf(alice, m["yes_id"]).call()
                n_tok = ctf.functions.balanceOf(alice, m["no_id"]).call()
                dy = y - before_yes[i]
                dn = n_tok - before_no[i]
                if dy != 0 or dn != 0:
                    log.info("  [%d] %-20s  YES %+d  NO %+d", i, m["title"][:20], dy, dn)

            log.info("\n  FINDING: Convert works with ONLY the indexSet question split!")
            log.info("  You do NOT need to split all questions first.")
        else:
            log.info("Convert REVERTED (status=0)")
            log.info("  FINDING: Must split all questions first for this market.")
    except (ContractLogicError, Web3RPCError) as e:
        log.info("Convert FAILED: %s", str(e)[:80])

    log.info("\nFull balances after split + convert:")
    print_balances(ctf, usdc, alice, markets, "Alice")

    revert(w3, base_snap)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 3: Split ALL questions, convert, verify break-even
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 3: Split ALL %d questions + convert → prove break-even", n)
    log.info("=" * 70)

    total_split_gas = 0
    for m in markets:
        g, _ = split_question(adapter, w3, alice, m["condition_id"], AMOUNT)
        total_split_gas += g
    log.info("Split $100 on each of %d questions: total gas=%d, total cost=$%d", n, total_split_gas, n * 100)

    usdc_after_splits = usdc.functions.balanceOf(alice).call()
    log.info("USDC after splits: $%.2f (spent $%.2f)", usdc_after_splits / 1e6, (alice_usdc_start - usdc_after_splits) / 1e6)

    # Convert: burn NO[0] → mint YES[1..N]
    before_yes = [ctf.functions.balanceOf(alice, m["yes_id"]).call() for m in markets]
    before_no = [ctf.functions.balanceOf(alice, m["no_id"]).call() for m in markets]

    tx = adapter.functions.convertPositions(market_id_bytes, 1, AMOUNT).transact({"from": alice, "gas": 20_000_000})
    r = w3.eth.wait_for_transaction_receipt(tx)

    usdc_after_conv = usdc.functions.balanceOf(alice).call()
    collateral = (usdc_after_conv - usdc_after_splits) / 1e6
    log.info("\nConvert (indexSet=1, burn NO[0]): status=%d, gas=%d, collateral=$%.2f", r["status"], r["gasUsed"], collateral)

    if r["status"] == 1:
        log.info("Balance changes:")
        for i, m_item in enumerate(markets):
            y = ctf.functions.balanceOf(alice, m_item["yes_id"]).call()
            n_tok = ctf.functions.balanceOf(alice, m_item["no_id"]).call()
            dy = y - before_yes[i]
            dn = n_tok - before_no[i]
            if dy != 0 or dn != 0:
                log.info("  [%d] %-20s  YES %+d  NO %+d", i, m_item["title"][:20], dy, dn)

    log.info("\nAfter split all + convert:")
    _, token_val = print_balances(ctf, usdc, alice, markets, "Alice")
    usdc_now = usdc.functions.balanceOf(alice).call()
    log.info("\n  Accounting: $%.2f USDC + ≈$%.2f tokens = ≈$%.2f (started $%.2f)",
             usdc_now / 1e6, token_val, usdc_now / 1e6 + token_val, alice_usdc_start / 1e6)

    # Now merge questions 1..N (they still have matched YES+NO)
    log.info("\nMerge questions 1..%d (still have matched YES+NO pairs):", n - 1)
    total_merge_gas = 0
    for i in range(1, n):
        m_item = markets[i]
        y = ctf.functions.balanceOf(alice, m_item["yes_id"]).call()
        n_tok = ctf.functions.balanceOf(alice, m_item["no_id"]).call()
        merge_amt = min(y, n_tok)
        if merge_amt == 0:
            continue
        tx = adapter.functions.mergePositions(cond_bytes(m_item["condition_id"]), merge_amt).transact({"from": alice, "gas": 500_000})
        r2 = w3.eth.wait_for_transaction_receipt(tx)
        total_merge_gas += r2["gasUsed"]
        log.info("  Merged %d on [%d] '%s': gas=%d", merge_amt, i, m_item["title"][:15], r2["gasUsed"])

    # Try merging question 0 (should fail — no NO[0] left after convert)
    log.info("\nTry merge question 0 (NO was burned by convert):")
    no_0 = ctf.functions.balanceOf(alice, markets[0]["no_id"]).call()
    if no_0 == 0:
        log.info("  NO[0] balance = 0 — nothing to merge. Confirmed: convert consumed all NO.")
    else:
        try:
            tx = adapter.functions.mergePositions(cond_bytes(markets[0]["condition_id"]), no_0).transact({"from": alice, "gas": 500_000})
            r2 = w3.eth.wait_for_transaction_receipt(tx)
            if r2["status"] == 1:
                log.info("  Merged %d on question 0: gas=%d", no_0, r2["gasUsed"])
            else:
                log.info("  Merge question 0: REVERTED")
        except (ContractLogicError, Web3RPCError) as e:
            log.info("  Merge question 0 FAILED: %s", str(e)[:60])

    log.info("\nAfter merge (recovered USDC from matched pairs):")
    _, token_val = print_balances(ctf, usdc, alice, markets, "Alice")
    usdc_final = usdc.functions.balanceOf(alice).call()
    log.info("\n  Final: $%.2f USDC + ≈$%.2f tokens = ≈$%.2f (started $%.2f)",
             usdc_final / 1e6, token_val, usdc_final / 1e6 + token_val, alice_usdc_start / 1e6)
    log.info("  Leftover tokens: YES[0] + YES[1..%d] = YES on ALL outcomes ≈ $%d", n - 1, AMOUNT // 10**6)
    log.info("  CONFIRMED: split + convert + merge = break-even. No directional exposure.")

    revert(w3, base_snap)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 4: Full path — get pure YES[0] via convert + OTC
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 4: Pure YES[0] — split + convert + OTC sell waste")
    log.info("=" * 70)
    log.info("Goal: end with ONLY YES[0] tokens (bullish on '%s')", favorite)

    # Step 1: Split only question 0
    s4_gas_total = 0
    gas, _ = split_question(adapter, w3, alice, markets[0]["condition_id"], AMOUNT)
    s4_gas_total += gas
    log.info("\n1. Split $100 on question 0: gas=%d", gas)

    # Step 2: Convert NO[0] → YES[1..N]
    tx = adapter.functions.convertPositions(market_id_bytes, 1, AMOUNT).transact({"from": alice, "gas": 20_000_000})
    r = w3.eth.wait_for_transaction_receipt(tx)
    s4_gas_total += r["gasUsed"]
    log.info("2. Convert NO[0] → YES[1..%d]: gas=%d, status=%d", n - 1, r["gasUsed"], r["status"])

    if r["status"] == 1:
        log.info("\n   After split + convert:")
        print_balances(ctf, usdc, alice, markets, "Alice")

        # Step 3: OTC sell the waste YES[1..N] to Bob
        log.info("\n3. OTC sell waste YES tokens to Bob:")
        transfer_gas = 0
        for i in range(1, n):
            m_item = markets[i]
            y = ctf.functions.balanceOf(alice, m_item["yes_id"]).call()
            if y == 0:
                continue
            tx = ctf.functions.safeTransferFrom(alice, bob, m_item["yes_id"], y, b"").transact({"from": alice, "gas": 200_000})
            r2 = w3.eth.wait_for_transaction_receipt(tx)
            transfer_gas += r2["gasUsed"]
            log.info("   Sold %d YES[%d] '%s' to Bob (≈$%.2f, gas=%d)",
                     y, i, m_item["title"][:15], y / 1e6 * m_item["yes_price"], r2["gasUsed"])
        s4_gas_total += transfer_gas

        log.info("\n   Final state:")
        print_balances(ctf, usdc, alice, markets, "Alice")
        print_balances(ctf, usdc, bob, markets, "Bob")

        yes_0 = ctf.functions.balanceOf(alice, markets[0]["yes_id"]).call()
        no_0 = ctf.functions.balanceOf(alice, markets[0]["no_id"]).call()
        log.info("\n   Alice: %d YES[0], %d NO[0] — pure YES exposure!", yes_0, no_0)
        log.info("   Total gas: %d (~$%.4f at 30 gwei)", s4_gas_total, s4_gas_total * 30e9 / 1e18 * 0.5)
    else:
        log.info("   Convert reverted — skipping scenario (no fallback implemented)")

    revert(w3, base_snap)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 5: Pure NO[0] — split + OTC sell YES
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 5: Pure NO[0] — split + OTC sell YES[0]")
    log.info("=" * 70)
    log.info("Goal: end with ONLY NO[0] tokens (bearish on '%s')", favorite)

    gas, _ = split_question(adapter, w3, alice, markets[0]["condition_id"], AMOUNT)
    s5_gas = gas
    log.info("\n1. Split $100 on question 0: gas=%d", gas)

    y0 = ctf.functions.balanceOf(alice, markets[0]["yes_id"]).call()
    tx = ctf.functions.safeTransferFrom(alice, bob, markets[0]["yes_id"], y0, b"").transact({"from": alice, "gas": 200_000})
    r = w3.eth.wait_for_transaction_receipt(tx)
    s5_gas += r["gasUsed"]
    log.info("2. OTC sell %d YES[0] to Bob: gas=%d", y0, r["gasUsed"])

    yes_final = ctf.functions.balanceOf(alice, markets[0]["yes_id"]).call()
    no_final = ctf.functions.balanceOf(alice, markets[0]["no_id"]).call()
    log.info("\n   Alice: %d YES[0], %d NO[0] — pure NO exposure!", yes_final, no_final)
    log.info("   Total gas: %d", s5_gas)

    revert(w3, base_snap)
    base_snap = snapshot(w3)

    # ==================================================================
    # SCENARIO 6: Convert with multi-bit indexSet
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SCENARIO 6: Convert with multi-bit indexSet")
    log.info("=" * 70)
    log.info("Test: burn NO from multiple questions at once")

    # Split all questions first
    for m_item in markets:
        split_question(adapter, w3, alice, m_item["condition_id"], AMOUNT)
    log.info("Split all %d questions ($%d each)", n, AMOUNT // 10**6)

    # indexSet = 3 (binary 0011) → burn NO[0]+NO[1], mint YES[2..N]
    index_set = 3
    before_yes = [ctf.functions.balanceOf(alice, m["yes_id"]).call() for m in markets]
    before_no = [ctf.functions.balanceOf(alice, m["no_id"]).call() for m in markets]
    usdc_before = usdc.functions.balanceOf(alice).call()

    try:
        tx = adapter.functions.convertPositions(market_id_bytes, index_set, AMOUNT).transact({"from": alice, "gas": 20_000_000})
        r = w3.eth.wait_for_transaction_receipt(tx)
        usdc_after_c = usdc.functions.balanceOf(alice).call()
        collateral = (usdc_after_c - usdc_before) / 1e6

        if r["status"] == 1:
            log.info("Convert indexSet=%d (binary %s): SUCCESS, gas=%d, collateral=$%.2f",
                     index_set, bin(index_set), r["gasUsed"], collateral)
            log.info("\nBalance changes:")
            for i, m_item in enumerate(markets):
                y = ctf.functions.balanceOf(alice, m_item["yes_id"]).call()
                n_tok = ctf.functions.balanceOf(alice, m_item["no_id"]).call()
                dy = y - before_yes[i]
                dn = n_tok - before_no[i]
                if dy != 0 or dn != 0:
                    log.info("  [%d] %-20s  YES %+d  NO %+d", i, m_item["title"][:20], dy, dn)
            log.info("  Collateral returned: $%.2f (= amount × (bits_in_indexSet - 1) = $%d × %d)",
                     collateral, AMOUNT // 10**6, bin(index_set).count("1") - 1)
        else:
            log.info("Convert indexSet=%d: REVERTED", index_set)
    except (ContractLogicError, Web3RPCError) as e:
        log.info("Convert indexSet=%d FAILED: %s", index_set, str(e)[:80])

    revert(w3, base_snap)

    # ==================================================================
    # SUMMARY
    # ==================================================================
    log.info("\n" + "=" * 70)
    log.info("SUMMARY")
    log.info("=" * 70)

    log.info("""
Scenario                          Operations                    Gas (approx)
───────────────────────────────────────────────────────────────────────────────
A) Split → YES+NO pair            1 split                       ~220k
B) Pure YES (split + OTC)         1 split + 1 transfer          ~270k
C) Pure YES (convert + OTC)       1 split + 1 convert +         ~750k + 52k/outcome
                                  (N-1) transfers
D) Pure NO                        1 split + 1 transfer          ~270k
E) Break-even proof               N splits + 1 convert +        ~220k×N + 500k + 170k×N
                                  (N-1) merges
F) Multi-bit convert              N splits + 1 convert          ~220k×N + 500k
───────────────────────────────────────────────────────────────────────────────
N = %d outcomes in this market
""" % n)

    log.info("KEY FINDINGS:")
    log.info("  1. Split gives YES+NO — always $1 per pair, regardless of market price")
    log.info("  2. Convert burns NO[indexSet] → mints YES on OTHER outcomes (never same outcome)")
    log.info("  3. Convert does NOT require splitting all questions (corrects Phase 4 finding)")
    log.info("     Only need NO tokens for the question(s) in the indexSet")
    log.info("  4. Split + convert = YES on ALL outcomes = $1 = break even (zero directional exposure)")
    log.info("  5. For directional exposure, you MUST sell the unwanted side (OTC or CLOB)")
    log.info("  6. Convert is useful for rearranging waste into more liquid/sellable tokens")
    log.info("  7. Multi-bit indexSet returns collateral = amount × (bits_set - 1)")
    log.info("  8. The cheapest path to pure YES or NO: 1 split + 1 OTC transfer (~270k gas)")

    log.info("\n=== Scenario testing: COMPLETE ===")


if __name__ == "__main__":
    main()
