"""Fund test actors on local Anvil fork.

WHAT IT DOES
    Impersonates a USDC.e whale on the forked chain to fund Alice and Bob
    with USDC.e, then sets up CTF approvals for both accounts.

WHY WE NEED THIS
    Every experiment script needs funded accounts with approvals.
    Run this once after starting anvil.sh.

USAGE
    # Start anvil first:  ./experiments/onchain-otc/anvil.sh
    # Then in another terminal:
    uv run experiments/onchain-otc/setup_accounts.py
"""

import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# --- Config ---

ANVIL_RPC = "http://127.0.0.1:8545"
FUND_AMOUNT_USDC = 10_000  # $10k each

# Polymarket contracts (Polygon mainnet, available on fork)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Known USDC.e whale on Polygon (Aave pool holds millions)
USDC_WHALE = "0x625E7708f30cA75bfd92586e17077590C60eb4cD"

# Minimal ABIs
ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"constant":false,"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")


def main():
    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        log.error("Cannot connect to Anvil at %s — is it running?", ANVIL_RPC)
        return

    accounts = w3.eth.accounts
    alice = accounts[0]
    bob = accounts[1]
    log.info("Alice: %s", alice)
    log.info("Bob:   %s", bob)

    # Clear any contract code on test accounts (Polygon fork may have EIP-7702
    # delegations on these well-known Anvil addresses, which breaks ERC-1155
    # safeTransfer callbacks).
    for addr in [alice, bob]:
        w3.provider.make_request("anvil_setCode", [addr, "0x"])
    log.info("Cleared contract code on Alice & Bob (pure EOAs now)")

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

    # --- Step 1: Impersonate USDC whale and fund accounts ---
    whale = Web3.to_checksum_address(USDC_WHALE)
    log.info("\n--- Impersonating USDC.e whale: %s ---", whale)
    w3.provider.make_request("anvil_impersonateAccount", [whale])
    # Give whale MATIC for gas (impersonated accounts have 0 native balance)
    w3.provider.make_request("anvil_setBalance", [whale, hex(10**18)])

    whale_balance = usdc.functions.balanceOf(whale).call()
    log.info("Whale USDC.e balance: $%s", whale_balance / 1e6)

    amount_raw = FUND_AMOUNT_USDC * 10**6  # 6 decimals

    for name, addr in [("Alice", alice), ("Bob", bob)]:
        tx = usdc.functions.transfer(addr, amount_raw).transact({"from": whale})
        w3.eth.wait_for_transaction_receipt(tx)
        balance = usdc.functions.balanceOf(addr).call()
        log.info("Funded %s with $%s USDC.e (balance: $%s)", name, FUND_AMOUNT_USDC, balance / 1e6)

    w3.provider.make_request("anvil_stopImpersonatingAccount", [whale])

    # --- Step 2: Set CTF approvals for both accounts ---
    max_uint = 2**256 - 1
    operators = [
        ("CTF_EXCHANGE", CTF_EXCHANGE),
        ("NEG_RISK_CTF_EXCHANGE", NEG_RISK_CTF_EXCHANGE),
        ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER),
    ]

    for name, addr in [("Alice", alice), ("Bob", bob)]:
        log.info("\n--- Setting approvals for %s ---", name)

        # USDC.e approve CTF + NegRiskAdapter (for splitPosition)
        for label, spender in [("CTF", CTF), ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER)]:
            tx = usdc.functions.approve(Web3.to_checksum_address(spender), max_uint).transact({"from": addr})
            w3.eth.wait_for_transaction_receipt(tx)
            log.info("  USDC.e -> %s: approved", label)

        # CTF setApprovalForAll for exchanges + adapter
        for op_name, op_addr in operators:
            tx = ctf.functions.setApprovalForAll(
                Web3.to_checksum_address(op_addr), True
            ).transact({"from": addr})
            w3.eth.wait_for_transaction_receipt(tx)
            log.info("  CTF -> %s: approved", op_name)

    # --- Summary ---
    log.info("\n=== Setup Complete ===")
    for name, addr in [("Alice", alice), ("Bob", bob)]:
        balance = usdc.functions.balanceOf(addr).call()
        log.info("%s: $%s USDC.e, all CTF approvals set", name, balance / 1e6)


if __name__ == "__main__":
    main()
