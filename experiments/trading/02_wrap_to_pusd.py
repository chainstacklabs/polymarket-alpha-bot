"""
Wrap USDC.e -> pUSD on Polygon (Polymarket V2 collateral)
=========================================================

After the 2026-04-28 V2 cutover, Polymarket's collateral changes from USDC.e
to pUSD. To keep trading, you wrap your existing USDC.e through the
CollateralOnramp contract; the offramp unwraps the other way.

This is the V2-prep counterpart to ``02_swap_to_usdc_e.py``. Order of ops once
the cutover lands:

    01_setup_wallet.py   -> 02_swap_to_usdc_e.py (still need USDC.e first)
                         -> 02_wrap_to_pusd.py   (wrap into pUSD for V2)
                         -> 03_buy_position.py

WHAT IT DOES:
    1. Reads USDC.e balance.
    2. Approves CollateralOnramp to pull USDC.e.
    3. Calls ``wrap(amount)`` on CollateralOnramp; receives pUSD 1:1.

USAGE:
    cd backend && uv run python ../experiments/trading/02_wrap_to_pusd.py

PREREQUISITES:
    - Wallet created (01_setup_wallet.py).
    - USDC.e in wallet (run 02_swap_to_usdc_e.py first if needed).
    - POL for gas.

ABI verified against Polygonscan source 2026-04-27 (BUSL-1.1, Solidity 0.8.34).
``wrap(address asset, address to, uint256 amount)`` requires a prior
``approve`` on the source token (USDC.e) and is gated by an `onlyUnpaused`
modifier — this script reads ``Onramp.paused(USDC.e)`` first and aborts on true.

Set ``WRAP_AMOUNT_USD=<decimal>`` to wrap a partial balance (default: full).
"""

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# experiments/trading/ -> experiments/ -> project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import os  # noqa: E402  (load_dotenv must run first)

WALLET_PATH = Path(__file__).parent / ".wallet.local.json"
RPC_URL = os.environ["CHAINSTACK_NODE"]

# Polygon mainnet addresses (confirmed 2026-04-26 against Polymarket V2 docs).
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

# Verified against Polygonscan source 2026-04-27 (CollateralOnramp.sol BUSL-1.1).
# wrap/unwrap take (asset, to, amount); caller must hold prior `approve` on asset.
# Onramp also exposes paused(asset) -> bool; pre-flight check before wrapping.
ONRAMP_ABI = [
    {
        "inputs": [
            {"name": "_asset", "type": "address"},
            {"name": "_to", "type": "address"},
            {"name": "_amount", "type": "uint256"},
        ],
        "name": "wrap",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "_asset", "type": "address"},
            {"name": "_to", "type": "address"},
            {"name": "_amount", "type": "uint256"},
        ],
        "name": "unwrap",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "_asset", "type": "address"}],
        "name": "paused",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def get_web3():
    from web3 import Web3

    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))


def load_wallet() -> dict:
    if not WALLET_PATH.exists():
        print("ERROR: Wallet not found. Run 01_setup_wallet.py first")
        sys.exit(1)
    return json.loads(WALLET_PATH.read_text())


def retry_call(fn, retries=3, delay=3):
    for i in range(retries):
        try:
            return fn()
        except Exception:
            if i < retries - 1:
                print(f"  Retry {i + 1}/{retries}...")
                time.sleep(delay * (i + 1))
            else:
                raise


def main():
    from web3 import Web3

    wallet = load_wallet()
    address = Web3.to_checksum_address(wallet["address"])
    private_key = wallet["private_key"]

    print("=" * 60)
    print("WRAP: USDC.e -> pUSD (Polymarket V2 collateral)")
    print("=" * 60)
    print(f"\nWallet: {address}")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    onramp = w3.eth.contract(
        address=Web3.to_checksum_address(COLLATERAL_ONRAMP), abi=ONRAMP_ABI
    )

    bal_usdce = retry_call(lambda: usdc_e.functions.balanceOf(address).call())
    bal_pusd = retry_call(lambda: pusd.functions.balanceOf(address).call())
    pol_balance = w3.from_wei(w3.eth.get_balance(address), "ether")

    print("\nCurrent balances:")
    print(f"  POL:    {pol_balance:.4f}")
    print(f"  USDC.e: ${bal_usdce / 1e6:.2f}")
    print(f"  pUSD:   ${bal_pusd / 1e6:.2f}")

    if bal_usdce == 0:
        print("\nNo USDC.e to wrap. Run 02_swap_to_usdc_e.py first.")
        return

    if pol_balance < 0.01:
        print("\nERROR: Insufficient POL for gas")
        return

    # Pre-flight: Onramp.paused(USDC.e) must be false
    is_paused = retry_call(
        lambda: onramp.functions.paused(Web3.to_checksum_address(USDC_E)).call()
    )
    if is_paused:
        print("\nERROR: Onramp is paused for USDC.e — abort")
        return

    # Wrap supports a custom amount via WRAP_AMOUNT_USD env var (decimal dollars).
    # Default: full USDC.e balance.
    custom_amount_usd = os.environ.get("WRAP_AMOUNT_USD")
    if custom_amount_usd:
        amount = int(float(custom_amount_usd) * 1e6)
        if amount > bal_usdce:
            print(f"\nERROR: WRAP_AMOUNT_USD=${custom_amount_usd} exceeds balance")
            return
    else:
        amount = bal_usdce
    print(f"\nWrapping ${amount / 1e6:.2f} USDC.e -> pUSD")
    confirm = input("Proceed? (yes/no): ")
    if confirm.lower() != "yes":
        print("Cancelled")
        return

    spender = Web3.to_checksum_address(COLLATERAL_ONRAMP)
    allowance = retry_call(lambda: usdc_e.functions.allowance(address, spender).call())

    if allowance < amount:
        print("\n[1/2] Approving CollateralOnramp...")
        tx = usdc_e.functions.approve(spender, 2**256 - 1).build_transaction(
            {
                "from": address,
                "nonce": retry_call(lambda: w3.eth.get_transaction_count(address)),
                "gas": 100000,
                "gasPrice": int(retry_call(lambda: w3.eth.gas_price) * 1.2),
                "chainId": 137,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = retry_call(
            lambda: w3.eth.send_raw_transaction(signed.raw_transaction)
        )
        print(f"  TX: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt["status"] != 1:
            print("  ERROR: Approval failed")
            return
        print("  Approved!")
        time.sleep(3)
    else:
        print("\n[1/2] Already approved")

    print("\n[2/2] Calling wrap(asset, to, amount)...")
    wrap_tx = onramp.functions.wrap(
        Web3.to_checksum_address(USDC_E),
        address,
        amount,
    ).build_transaction(
        {
            "from": address,
            "nonce": retry_call(lambda: w3.eth.get_transaction_count(address)),
            "gas": 250000,
            "gasPrice": int(retry_call(lambda: w3.eth.gas_price) * 1.2),
            "chainId": 137,
        }
    )
    signed = account.sign_transaction(wrap_tx)
    tx_hash = retry_call(lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
    print(f"  TX: {tx_hash.hex()}")
    print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        print("  ERROR: Wrap failed")
        return

    print("  Wrap complete!")

    time.sleep(2)
    bal_usdce = retry_call(lambda: usdc_e.functions.balanceOf(address).call())
    bal_pusd = retry_call(lambda: pusd.functions.balanceOf(address).call())

    print("\n" + "=" * 60)
    print("WRAP COMPLETE")
    print("=" * 60)
    print("\nFinal balances:")
    print(f"  USDC.e: ${bal_usdce / 1e6:.2f}")
    print(f"  pUSD:   ${bal_pusd / 1e6:.2f}")


if __name__ == "__main__":
    main()
