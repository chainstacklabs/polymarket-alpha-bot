"""Fund test actors on local Anvil fork (Polymarket V2).

WHAT IT DOES
    Impersonates a USDC.e whale to fund Alice and Bob, wraps a portion to pUSD
    (the V2 exchange's cash collateral), then sets approvals.

COLLATERAL MODEL (verified on-chain 2026-06-22)
    Polymarket V2 left the CTF position layer UNCHANGED: conditional tokens are
    still collateralised in **USDC.e** (177/177 live non-NegRisk markets derive
    against USDC.e; pUSD derives to non-existent token IDs). What changed is the
    **CLOB exchange**: the V2 exchanges (0xE111.../0xe2222d...) report
    `getCollateral() = pUSD`, so the order *cash* leg settles in pUSD while the
    traded CTF tokens remain USDC.e-backed.

    So: split/merge/convert/escrow/transfer all use **USDC.e**. Only the
    exchange order cash leg (scenario 5) uses **pUSD**.

WHY WE NEED THIS
    Every experiment script needs funded accounts with approvals.
    Run this once after starting anvil.sh.

USAGE
    # Start anvil first:  ./experiments/onchain-otc/anvil.sh
    # Then in another terminal:
    cd backend && uv run ../experiments/onchain-otc/setup_accounts.py
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
FUND_AMOUNT_USDC = 10_000  # $10k USDC.e each
PUSD_WRAP_AMOUNT = 5_000  # wrap $5k each to pUSD for the exchange cash leg

# Polymarket contracts (Polygon mainnet, available on fork).
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # CTF collateral (unchanged in V2)
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # V2 exchange cash collateral
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"  # USDC.e -> pUSD (1:1)
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"  # V2
NEG_RISK_CTF_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"  # V2
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"  # USDC.e-collateralised (unchanged)

# Known USDC.e whale on Polygon (Aave pool holds millions)
USDC_WHALE = "0x625E7708f30cA75bfd92586e17077590C60eb4cD"

# Minimal ABIs
ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

# CollateralOnramp ABI (verified against Polygonscan source 2026-04-27).
ONRAMP_ABI = json.loads("""[
    {"inputs":[{"name":"_asset","type":"address"},{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"wrap","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"_asset","type":"address"}],"name":"paused","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}
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
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    onramp = w3.eth.contract(address=Web3.to_checksum_address(COLLATERAL_ONRAMP), abi=ONRAMP_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

    # --- Step 1: Impersonate USDC whale and fund accounts with USDC.e ---
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
        log.info("Funded %s with $%s USDC.e (balance: $%s)", name, FUND_AMOUNT_USDC, usdc.functions.balanceOf(addr).call() / 1e6)

    w3.provider.make_request("anvil_stopImpersonatingAccount", [whale])

    # --- Step 2: Wrap a portion of USDC.e -> pUSD (exchange cash leg, scenario 5) ---
    if onramp.functions.paused(Web3.to_checksum_address(USDC_E)).call():
        log.error("CollateralOnramp is paused for USDC.e — cannot wrap to pUSD. Abort.")
        return

    wrap_raw = PUSD_WRAP_AMOUNT * 10**6
    log.info("\n--- Wrapping $%s USDC.e -> pUSD each (V2 exchange cash) ---", PUSD_WRAP_AMOUNT)
    for name, addr in [("Alice", alice), ("Bob", bob)]:
        tx = usdc.functions.approve(Web3.to_checksum_address(COLLATERAL_ONRAMP), 2**256 - 1).transact({"from": addr})
        w3.eth.wait_for_transaction_receipt(tx)
        tx = onramp.functions.wrap(Web3.to_checksum_address(USDC_E), addr, wrap_raw).transact({"from": addr})
        w3.eth.wait_for_transaction_receipt(tx)
        log.info("  %s: $%s USDC.e + $%s pUSD", name, usdc.functions.balanceOf(addr).call() / 1e6, pusd.functions.balanceOf(addr).call() / 1e6)

    # --- Step 3: Approvals ---
    max_uint = 2**256 - 1
    # USDC.e spenders for split (CTF + NegRisk adapter — both pull USDC.e collateral).
    usdce_spenders = [("CTF", CTF), ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER)]
    # pUSD spenders for the exchange order cash leg (V2 exchanges only).
    pusd_spenders = [("CTF_EXCHANGE (V2)", CTF_EXCHANGE), ("NEG_RISK_CTF_EXCHANGE (V2)", NEG_RISK_CTF_EXCHANGE)]
    # CTF operators that move conditional tokens.
    ctf_operators = [
        ("CTF_EXCHANGE (V2)", CTF_EXCHANGE),
        ("NEG_RISK_CTF_EXCHANGE (V2)", NEG_RISK_CTF_EXCHANGE),
        ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER),
    ]

    for name, addr in [("Alice", alice), ("Bob", bob)]:
        log.info("\n--- Setting approvals for %s ---", name)
        for label, spender in usdce_spenders:
            tx = usdc.functions.approve(Web3.to_checksum_address(spender), max_uint).transact({"from": addr})
            w3.eth.wait_for_transaction_receipt(tx)
            log.info("  USDC.e -> %s: approved", label)
        for label, spender in pusd_spenders:
            tx = pusd.functions.approve(Web3.to_checksum_address(spender), max_uint).transact({"from": addr})
            w3.eth.wait_for_transaction_receipt(tx)
            log.info("  pUSD -> %s: approved", label)
        for op_name, op_addr in ctf_operators:
            tx = ctf.functions.setApprovalForAll(Web3.to_checksum_address(op_addr), True).transact({"from": addr})
            w3.eth.wait_for_transaction_receipt(tx)
            log.info("  CTF -> %s: approved", op_name)

    # --- Summary ---
    log.info("\n=== Setup Complete ===")
    for name, addr in [("Alice", alice), ("Bob", bob)]:
        log.info("%s: $%s USDC.e, $%s pUSD, all approvals set",
                 name, usdc.functions.balanceOf(addr).call() / 1e6, pusd.functions.balanceOf(addr).call() / 1e6)


if __name__ == "__main__":
    main()
