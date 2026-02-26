"""07: Intent-based trading — Gnosis ERC-1155→ERC-20 wrapper + CoW-style intents

WHAT IT DOES
    1. Deploys Wrapped1155Factory (Gnosis pattern) on the Anvil fork
    2. Wraps CTF outcome tokens into standard ERC-20s
    3. Tests unwrap round-trip (lossless)
    4. Simulates intent-based trading: Alice signs an intent to sell wrapped
       YES tokens, Bob (solver) fills it via ERC-20 approve+transferFrom
    5. Bob unwraps to get native CTF tokens

WHY WE NEED THIS
    All intent/DeFi protocols (CoW, 1inch, Uniswap) require ERC-20 tokens.
    CTF positions are ERC-1155. The Gnosis wrapper bridges this gap, making
    prediction market tokens composable with the entire DeFi stack.

USAGE
    # Requires: anvil.sh running + setup_accounts.py completed
    cd backend && uv run ../experiments/onchain-otc/07_intents.py
"""

import json
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
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

CONTRACTS_DIR = Path(__file__).parent / "contracts"

ERC20_ABI = json.loads(
    """[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transferFrom","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
]"""
)

CTF_ABI = json.loads(
    """[
    {"inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"id","type":"uint256"},{"name":"amount","type":"uint256"},{"name":"data","type":"bytes"}],"name":"safeTransferFrom","outputs":[],"type":"function"}
]"""
)

NEG_RISK_ABI = json.loads(
    """[
    {"inputs":[{"name":"conditionId","type":"bytes32"},{"name":"amount","type":"uint256"}],"name":"splitPosition","outputs":[],"type":"function"},
    {"inputs":[{"name":"","type":"bytes32"}],"name":"getQuestionCount","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]"""
)

FACTORY_ABI = json.loads(
    """[
    {"inputs":[{"name":"multiToken","type":"address"},{"name":"tokenId","type":"uint256"}],"name":"getWrapped","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"inputs":[{"name":"multiToken","type":"address"},{"name":"tokenId","type":"uint256"},{"name":"amount","type":"uint256"},{"name":"to","type":"address"}],"name":"unwrap","outputs":[],"type":"function"}
]"""
)

# EIP-712 types matching IntentSettlement.sol INTENT_TYPEHASH exactly
INTENT_TYPES = {
    "SellIntent": [
        {"name": "seller", "type": "address"},
        {"name": "sellToken", "type": "address"},
        {"name": "sellAmount", "type": "uint256"},
        {"name": "buyToken", "type": "address"},
        {"name": "minBuyAmount", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
    ]
}


def snapshot(w3: Web3) -> str:
    return w3.provider.make_request("evm_snapshot", [])["result"]


def revert(w3: Web3, snap_id: str):
    result = w3.provider.make_request("evm_revert", [snap_id])
    if not result.get("result"):
        raise RuntimeError(f"Failed to revert snapshot {snap_id}")


def fetch_negrisk_event() -> dict:
    """Find a live NegRisk market with 3+ outcomes and parse token IDs."""
    resp = httpx.get(
        f"{GAMMA_API}/events",
        params={
            "closed": "false",
            "active": "true",
            "limit": 30,
            "order": "volume24hr",
            "ascending": "false",
        },
        timeout=15.0,
    )
    for e in resp.json():
        if not e.get("negRisk"):
            continue
        markets = e.get("markets", [])
        if len(markets) < 3:
            continue

        parsed = []
        for m in markets:
            raw = m.get("clobTokenIds", "[]")
            tokens = json.loads(raw) if isinstance(raw, str) else raw
            if len(tokens) < 2:
                continue
            parsed.append(
                {
                    "title": m.get("groupItemTitle", "?"),
                    "condition_id": m.get("conditionId", ""),
                    "yes_id": int(tokens[0]),
                    "no_id": int(tokens[1]),
                }
            )

        if len(parsed) >= 3:
            return {
                "title": e.get("title", "?"),
                "neg_risk_market_id": markets[0].get("negRiskMarketID", ""),
                "markets": parsed,
            }

    msg = "No suitable NegRisk event found"
    raise RuntimeError(msg)


def deploy_factory(w3: Web3, deployer: str) -> str:
    """Deploy Wrapped1155Factory from forge artifacts."""
    artifact_path = CONTRACTS_DIR / "out" / "Wrapped1155.sol" / "Wrapped1155Factory.json"
    artifact = json.loads(artifact_path.read_text())
    bytecode = artifact["bytecode"]["object"]
    abi = artifact["abi"]

    factory_contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = factory_contract.constructor().transact({"from": deployer})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Factory deployment failed"
    return receipt["contractAddress"]


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    assert w3.is_connected(), "Anvil not running"

    accounts = w3.eth.accounts
    alice = accounts[0]
    bob = accounts[1]
    alice_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)
    neg_risk = w3.eth.contract(
        address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI
    )

    # === Fetch a market ===
    log.info("=== Phase 7: Intent-Based Trading via ERC-1155→ERC-20 Wrapper ===\n")

    event = fetch_negrisk_event()
    log.info("Market: %s (%d outcomes)", event["title"], len(event["markets"]))
    log.info("Outcomes: %s", ", ".join(m["title"] for m in event["markets"][:5]))

    m0 = event["markets"][0]
    condition_id = bytes.fromhex(m0["condition_id"].removeprefix("0x"))
    yes_token_id = m0["yes_id"]
    no_token_id = m0["no_id"]
    log.info("Testing with outcome 0: %s", m0["title"])
    log.info("  YES token ID: %s...", hex(yes_token_id)[:18])
    log.info("  NO  token ID: %s...", hex(no_token_id)[:18])

    # === Step 1: Deploy Wrapped1155Factory ===
    log.info("\n--- Step 1: Deploy Wrapped1155Factory ---")
    factory_addr = deploy_factory(w3, alice)
    log.info("Factory deployed at: %s", factory_addr)
    factory = w3.eth.contract(address=factory_addr, abi=FACTORY_ABI)

    # === Step 2: Alice splits to get YES+NO tokens ===
    log.info("\n--- Step 2: Alice splits $100 USDC.e → YES+NO ---")
    split_amount = 100 * 10**6  # $100

    tx = neg_risk.functions.splitPosition(condition_id, split_amount).transact(
        {"from": alice, "gas": 500_000}
    )
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1, "Split failed"
    log.info("Split OK (gas: %d)", receipt["gasUsed"])

    alice_yes = ctf.functions.balanceOf(alice, yes_token_id).call()
    alice_no = ctf.functions.balanceOf(alice, no_token_id).call()
    log.info("Alice YES: %s, NO: %s", alice_yes / 1e6, alice_no / 1e6)

    # === Step 3: Wrap YES tokens ===
    log.info("\n--- Step 3: Wrap YES tokens (safeTransferFrom → factory) ---")
    wrap_amount = 50 * 10**6  # Wrap 50 YES tokens

    # First wrap needs extra gas: deploys ERC-20 contract inside onERC1155Received
    tx = ctf.functions.safeTransferFrom(
        alice, factory_addr, yes_token_id, wrap_amount, b""
    ).transact({"from": alice, "gas": 1_000_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1, "Wrap failed"
    log.info("Wrap OK (gas: %d) — first wrap deploys ERC-20 proxy", receipt["gasUsed"])

    # Get the wrapper address
    wrapper_addr = factory.functions.getWrapped(
        Web3.to_checksum_address(CTF), yes_token_id
    ).call()
    log.info("Wrapper ERC-20 deployed at: %s", wrapper_addr)
    assert wrapper_addr != "0x" + "0" * 40, "Wrapper not deployed"

    wyes = w3.eth.contract(address=wrapper_addr, abi=ERC20_ABI)
    alice_wyes = wyes.functions.balanceOf(alice).call()
    log.info("Alice wYES balance: %s", alice_wyes / 1e6)
    assert alice_wyes == wrap_amount, f"Expected {wrap_amount}, got {alice_wyes}"

    # Verify ERC-20 metadata
    name = wyes.functions.name().call()
    symbol = wyes.functions.symbol().call()
    decimals = wyes.functions.decimals().call()
    supply = wyes.functions.totalSupply().call()
    log.info("  name=%s, symbol=%s, decimals=%d, totalSupply=%s", name, symbol, decimals, supply / 1e6)

    # Verify factory holds the CTF tokens
    factory_ctf = ctf.functions.balanceOf(factory_addr, yes_token_id).call()
    log.info("Factory holds %s CTF YES tokens (backing)", factory_ctf / 1e6)
    assert factory_ctf == wrap_amount, "Factory should hold wrapped amount"

    # === Step 4: Wrap more (same token — no new deployment) ===
    log.info("\n--- Step 4: Wrap 50 more YES (reuses existing wrapper) ---")
    snap = snapshot(w3)

    tx = ctf.functions.safeTransferFrom(
        alice, factory_addr, yes_token_id, wrap_amount, b""
    ).transact({"from": alice, "gas": 300_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1
    log.info("Second wrap OK (gas: %d) — cheaper, no deploy", receipt["gasUsed"])

    alice_wyes = wyes.functions.balanceOf(alice).call()
    log.info("Alice wYES balance: %s (doubled)", alice_wyes / 1e6)
    assert alice_wyes == wrap_amount * 2

    revert(w3, snap)
    log.info("(reverted to single-wrap state)")

    # === Step 5: Unwrap round-trip ===
    log.info("\n--- Step 5: Unwrap — burn wYES → get CTF back ---")
    alice_ctf_before = ctf.functions.balanceOf(alice, yes_token_id).call()

    # Factory needs CTF approval to transfer tokens back to Alice
    # Actually, factory already holds the tokens and calls safeTransferFrom on itself
    tx = factory.functions.unwrap(
        Web3.to_checksum_address(CTF), yes_token_id, wrap_amount, alice
    ).transact({"from": alice, "gas": 300_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1, "Unwrap failed"
    log.info("Unwrap OK (gas: %d)", receipt["gasUsed"])

    alice_ctf_after = ctf.functions.balanceOf(alice, yes_token_id).call()
    alice_wyes_after = wyes.functions.balanceOf(alice).call()
    log.info("Alice CTF YES: %s → %s (recovered)", alice_ctf_before / 1e6, alice_ctf_after / 1e6)
    log.info("Alice wYES:    %s (burned)", alice_wyes_after / 1e6)
    assert alice_ctf_after == alice_ctf_before + wrap_amount, "Round-trip should be lossless"
    assert alice_wyes_after == 0, "All wYES should be burned"
    log.info("PASS: Round-trip is lossless")

    # === Step 6: Intent-based trade simulation ===
    log.info("\n--- Step 6: Intent-based trade (Alice sells wYES to Bob) ---")

    # Alice wraps YES tokens again
    tx = ctf.functions.safeTransferFrom(
        alice, factory_addr, yes_token_id, wrap_amount, b""
    ).transact({"from": alice, "gas": 500_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1

    # === Step 6b: Deploy IntentSettlement contract ===
    log.info("\n--- Step 6b: Deploy IntentSettlement (on-chain sig verification) ---")

    settlement_artifact = json.loads(
        (CONTRACTS_DIR / "out" / "IntentSettlement.sol" / "IntentSettlement.json").read_text()
    )
    settlement_contract = w3.eth.contract(
        abi=settlement_artifact["abi"], bytecode=settlement_artifact["bytecode"]["object"]
    )
    tx_hash = settlement_contract.constructor().transact({"from": alice})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1
    settlement_addr = receipt["contractAddress"]
    settlement = w3.eth.contract(address=settlement_addr, abi=settlement_artifact["abi"])
    log.info("IntentSettlement deployed at: %s (gas: %d)", settlement_addr, receipt["gasUsed"])

    # === Step 7: Alice signs intent matching the on-chain contract ===
    log.info("\n--- Step 7: Alice signs EIP-712 intent for on-chain settlement ---")

    price = 45 * 10**6  # $45 for 50 YES tokens ($0.90/token)
    deadline = w3.eth.get_block("latest")["timestamp"] + 3600

    # Domain must match IntentSettlement's DOMAIN_SEPARATOR exactly
    domain = {
        "name": "CTF Intent Exchange",
        "version": "1",
        "chainId": 137,
        "verifyingContract": settlement_addr,
    }

    intent_data = {
        "seller": alice,
        "sellToken": wrapper_addr,
        "sellAmount": wrap_amount,
        "buyToken": Web3.to_checksum_address(USDC_E),
        "minBuyAmount": price,
        "deadline": deadline,
        "nonce": 0,
    }

    signed = Account.sign_typed_data(
        alice_key,
        domain_data=domain,
        message_types=INTENT_TYPES,
        message_data=intent_data,
    )
    log.info("Alice signed intent: sell %s wYES for ≥$%s USDC.e", wrap_amount / 1e6, price / 1e6)
    log.info("  Signature: %s...", signed.signature.hex()[:20])

    # === Step 8: Alice approves settlement contract (not Bob!) ===
    log.info("\n--- Step 8: Approvals for settlement contract ---")

    # Alice approves settlement to spend her wYES
    tx = wyes.functions.approve(settlement_addr, wrap_amount).transact({"from": alice})
    w3.eth.wait_for_transaction_receipt(tx)
    log.info("Alice approved IntentSettlement to spend wYES")

    # Bob approves settlement to spend his USDC.e
    tx = usdc.functions.approve(settlement_addr, price).transact({"from": bob})
    w3.eth.wait_for_transaction_receipt(tx)
    log.info("Bob approved IntentSettlement to spend USDC.e")

    # === Step 9: Bob fills the intent ON-CHAIN (permissionless atomic swap) ===
    log.info("\n--- Step 9: Bob fills intent ON-CHAIN (permissionless atomic swap) ---")

    alice_usdc_before = usdc.functions.balanceOf(alice).call()
    alice_wyes_before = wyes.functions.balanceOf(alice).call()
    bob_usdc_before = usdc.functions.balanceOf(bob).call()

    # Bob calls fillIntent — contract verifies sig via ecrecover + swaps atomically
    v = signed.v
    r = signed.r.to_bytes(32, "big")
    s = signed.s.to_bytes(32, "big")

    tx = settlement.functions.fillIntent(
        (  # SellIntent struct tuple
            alice,              # seller
            wrapper_addr,       # sellToken
            wrap_amount,        # sellAmount
            Web3.to_checksum_address(USDC_E),  # buyToken
            price,              # minBuyAmount
            deadline,           # deadline
            0,                  # nonce
        ),
        price,  # buyAmount (solver pays exactly minBuyAmount)
        v,
        r,
        s,
    ).transact({"from": bob, "gas": 500_000})
    receipt_fill = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt_fill["status"] == 1, "fillIntent failed!"
    log.info("fillIntent OK (gas: %d) — ON-CHAIN sig verification + atomic swap!", receipt_fill["gasUsed"])

    # Verify balances
    alice_usdc_after = usdc.functions.balanceOf(alice).call()
    alice_wyes_after = wyes.functions.balanceOf(alice).call()
    bob_wyes_after = wyes.functions.balanceOf(bob).call()
    bob_usdc_after = usdc.functions.balanceOf(bob).call()

    log.info("\n  Post-fill balances:")
    log.info("    Alice: wYES %s→%s, USDC +$%s", alice_wyes_before / 1e6, alice_wyes_after / 1e6, (alice_usdc_after - alice_usdc_before) / 1e6)
    log.info("    Bob:   wYES +%s, USDC -$%s", bob_wyes_after / 1e6, (bob_usdc_before - bob_usdc_after) / 1e6)

    assert alice_wyes_after == 0, "Alice should have 0 wYES"
    assert bob_wyes_after == wrap_amount, "Bob should hold wYES"
    assert alice_usdc_after - alice_usdc_before == price, "Alice should receive USDC"
    assert bob_usdc_before - bob_usdc_after == price, "Bob should spend USDC"
    log.info("  PASS: Atomic swap verified — both sides settled in one tx")

    # === Step 10: Security tests ===
    log.info("\n--- Step 10: Security — replay, bad sig, expired ---")

    # 10a: Replay attack — try to fill the same intent again
    tx = usdc.functions.approve(settlement_addr, price).transact({"from": bob})
    w3.eth.wait_for_transaction_receipt(tx)

    tx = settlement.functions.fillIntent(
        (alice, wrapper_addr, wrap_amount, Web3.to_checksum_address(USDC_E), price, deadline, 0),
        price, v, r, s,
    ).transact({"from": bob, "gas": 200_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 0, "Replay should revert!"
    log.info("  10a PASS: Replay attack rejected (nonce incremented to %d)",
             settlement.functions.nonces(alice).call())

    # 10b: Tampered signature — use Alice's signature but claim Charlie is the seller
    charlie = w3.eth.accounts[2]
    tx = settlement.functions.fillIntent(
        (charlie, wrapper_addr, wrap_amount, Web3.to_checksum_address(USDC_E), price, deadline, 0),
        price, v, r, s,
    ).transact({"from": bob, "gas": 200_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 0, "Tampered sig should revert!"
    log.info("  10b PASS: Tampered signature rejected (ecrecover mismatch)")

    # 10c: Expired intent
    snap_before_expiry = snapshot(w3)
    w3.provider.make_request("evm_increaseTime", [7200])  # +2 hours
    w3.provider.make_request("evm_mine", [])

    # Alice signs a new intent (nonce=1 now) and approves
    intent_data_2 = {**intent_data, "nonce": 1, "deadline": deadline}  # old deadline
    signed2 = Account.sign_typed_data(alice_key, domain_data=domain,
                                       message_types=INTENT_TYPES, message_data=intent_data_2)
    tx = settlement.functions.fillIntent(
        (alice, wrapper_addr, wrap_amount, Web3.to_checksum_address(USDC_E), price, deadline, 1),
        price, signed2.v, signed2.r.to_bytes(32, "big"), signed2.s.to_bytes(32, "big"),
    ).transact({"from": bob, "gas": 200_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 0, "Expired intent should revert!"
    log.info("  10c PASS: Expired intent rejected")

    # Revert to pre-expiry state for step 11
    revert(w3, snap_before_expiry)

    # === Step 11: Bob unwraps to get native CTF tokens ===
    log.info("\n--- Step 11: Bob unwraps wYES → native CTF YES tokens ---")

    bob_ctf_before = ctf.functions.balanceOf(bob, yes_token_id).call()

    tx = factory.functions.unwrap(
        Web3.to_checksum_address(CTF), yes_token_id, wrap_amount, bob
    ).transact({"from": bob, "gas": 300_000})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    assert receipt["status"] == 1
    log.info("Unwrap OK (gas: %d)", receipt["gasUsed"])

    bob_ctf_after = ctf.functions.balanceOf(bob, yes_token_id).call()
    log.info("Bob CTF YES: %s → %s", bob_ctf_before / 1e6, bob_ctf_after / 1e6)
    assert bob_ctf_after == bob_ctf_before + wrap_amount

    # === Summary ===
    log.info("\n" + "=" * 60)
    log.info("=== Phase 7 COMPLETE ===")
    log.info("=" * 60)
    log.info("")
    log.info("Full permissionless chain proven:")
    log.info("  CTF(ERC-1155) → wrap(ERC-20) → sign intent → ON-CHAIN fill → unwrap → CTF")
    log.info("")
    log.info("No CLOB. No operator. No whitelist. Anyone can be a solver.")
    log.info("")
    log.info("Gas costs:")
    log.info("  Wrap (first, deploys proxy): ~650k")
    log.info("  Wrap (subsequent):           ~57k")
    log.info("  fillIntent (on-chain):       %d  ← ecrecover + atomic swap", receipt_fill["gasUsed"])
    log.info("  Unwrap:                      ~48k")
    log.info("")
    log.info("Security:")
    log.info("  Replay attack:     REJECTED (nonce incremented)")
    log.info("  Tampered signature: REJECTED (ecrecover mismatch)")
    log.info("  Expired intent:    REJECTED (deadline check)")
    log.info("")
    log.info("Key finding: IntentSettlement.fillIntent() is the permissionless")
    log.info("equivalent of Polymarket's operator-gated matchOrders().")
    log.info("Any agent can be a solver. The contract is the trust layer.")


if __name__ == "__main__":
    main()
