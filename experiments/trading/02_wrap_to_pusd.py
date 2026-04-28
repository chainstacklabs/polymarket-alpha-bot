"""
Wrap USDC.e -> pUSD on Polygon  (lossless: 1:1 + gas only)
==========================================================

Polymarket V2 (post-2026-04-28 cutover) settles in pUSD. The
CollateralOnramp contract converts USDC.e -> pUSD at exactly 1:1 — no
slippage, no spread, no fees. You pay only Polygon gas (~$0.003 at
current rates).

This is the V2 collateral entry point. The Polymarket app's swap UI
runs through DEX routers and charges slippage; this contract path
doesn't. Reverse direction: ``02_unwrap_to_usdc_e.py``.

Order of ops for new users:

    01_setup_wallet.py   -> 02_swap_to_usdc_e.py (only if starting from native USDC)
                         -> 02_wrap_to_pusd.py   (wrap into pUSD for V2)
                         -> 03_buy_position.py

WHAT IT DOES:
    1. Reads USDC.e balance.
    2. Approves CollateralOnramp to pull USDC.e.
    3. Calls ``wrap(asset=USDC.e, to, amount)``; receives pUSD 1:1.

USAGE:
    cd backend && uv run python ../experiments/trading/02_wrap_to_pusd.py

PREREQUISITES:
    - Wallet created (01_setup_wallet.py).
    - USDC.e in wallet (run 02_swap_to_usdc_e.py first if needed).
    - POL for gas.

ABI verified against Polygonscan source 2026-04-27 (BUSL-1.1, Solidity 0.8.34).
``wrap(address asset, address to, uint256 amount)`` requires a prior
``approve`` on the source token and is gated by an `onlyUnpaused` modifier
— this script reads ``Onramp.paused(asset)`` first and aborts on true.

Set ``WRAP_AMOUNT_USD=<decimal>`` to wrap a partial balance (default: full).

Native USDC support is staged but paused
----------------------------------------
The Onramp's `wrap` function takes an `asset` arg, so it can in principle
on-ramp any whitelisted ERC-20. As of 2026-04-28, ``Onramp.paused(asset)``
returns:

    USDC.e (0x2791…4174) -> False  (active, the path this script uses)
    Native USDC (0x3c49…3359) -> True  (explicitly paused, only token in
                                        that state — provisioned but off)
    everything else      -> default False (no signal either way)

The explicit pause on native USDC means Polymarket has staged direct
native-USDC -> pUSD support and is gating it behind a feature flag. When
they lift the pause, you can flip this script to native USDC by setting
``WRAP_ASSET=native`` (handled below) — no contract change, no DEX hop,
no slippage. Until then, native USDC users still need ``02_swap_to_usdc_e.py``
first to pick up USDC.e (which is a DEX swap with ~0.05–0.3% slippage).
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
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"

# WRAP_ASSET selects the source token. Default 'usdc.e' is the only one
# currently active (paused=False on the Onramp). 'native' targets
# Circle's native USDC — staged on the Onramp but currently paused; the
# `paused()` pre-flight below will abort cleanly until Polymarket lifts it.
_WRAP_ASSET_MAP = {
    "usdc.e": (USDC_E, "USDC.e"),
    "usdce": (USDC_E, "USDC.e"),
    "native": (USDC_NATIVE, "USDC"),
    "usdc": (USDC_NATIVE, "USDC"),
}

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

    asset_choice = os.environ.get("WRAP_ASSET", "usdc.e").lower()
    if asset_choice not in _WRAP_ASSET_MAP:
        print(
            f"ERROR: unknown WRAP_ASSET={asset_choice!r}; "
            f"expected one of {sorted(set(_WRAP_ASSET_MAP))}"
        )
        return
    src_addr, src_label = _WRAP_ASSET_MAP[asset_choice]

    print("=" * 60)
    print(f"WRAP: {src_label} -> pUSD (Polymarket V2 collateral)")
    print("=" * 60)
    print(f"\nWallet: {address}")
    print(f"Source: {src_label} ({src_addr})")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    src = w3.eth.contract(address=Web3.to_checksum_address(src_addr), abi=ERC20_ABI)
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    onramp = w3.eth.contract(
        address=Web3.to_checksum_address(COLLATERAL_ONRAMP), abi=ONRAMP_ABI
    )

    bal_src_pre = retry_call(lambda: src.functions.balanceOf(address).call())
    bal_pusd_pre = retry_call(lambda: pusd.functions.balanceOf(address).call())
    pol_pre_wei = retry_call(lambda: w3.eth.get_balance(address))
    pol_balance = w3.from_wei(pol_pre_wei, "ether")

    print("\nCurrent balances:")
    print(f"  POL:    {pol_balance:.4f}")
    print(f"  {src_label:<6} ${bal_src_pre / 1e6:.2f}")
    print(f"  pUSD:   ${bal_pusd_pre / 1e6:.2f}")

    if bal_src_pre == 0:
        if asset_choice in ("native", "usdc"):
            print(f"\nNo {src_label} to wrap. Bridge or buy native USDC first.")
        else:
            print(f"\nNo {src_label} to wrap. Run 02_swap_to_usdc_e.py first.")
        return

    if pol_balance < 0.01:
        print("\nERROR: Insufficient POL for gas")
        return

    # Pre-flight: Onramp.paused(asset) must be false. Native USDC is currently
    # staged-but-paused on this contract; this check fails cleanly there.
    is_paused = retry_call(
        lambda: onramp.functions.paused(Web3.to_checksum_address(src_addr)).call()
    )
    if is_paused:
        print(
            f"\nERROR: Onramp is paused for {src_label} — abort.\n"
            f"  (If WRAP_ASSET=native, Polymarket has not yet enabled "
            f"native USDC on-ramp; use USDC.e for now.)"
        )
        return

    # Wrap supports a custom amount via WRAP_AMOUNT_USD env var (decimal dollars).
    # Default: full source-token balance.
    custom_amount_usd = os.environ.get("WRAP_AMOUNT_USD")
    if custom_amount_usd:
        amount = int(float(custom_amount_usd) * 1e6)
        if amount > bal_src_pre:
            print(f"\nERROR: WRAP_AMOUNT_USD=${custom_amount_usd} exceeds balance")
            return
    else:
        amount = bal_src_pre
    print(f"\nWrapping ${amount / 1e6:.2f} {src_label} -> pUSD")
    confirm = input("Proceed? (yes/no): ")
    if confirm.lower() != "yes":
        print("Cancelled")
        return

    spender = Web3.to_checksum_address(COLLATERAL_ONRAMP)
    allowance = retry_call(lambda: src.functions.allowance(address, spender).call())

    gas_used_total = 0

    if allowance < amount:
        print("\n[1/2] Approving CollateralOnramp...")
        tx = src.functions.approve(spender, 2**256 - 1).build_transaction(
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
        gas_used_total += receipt["gasUsed"] * receipt["effectiveGasPrice"]
        print("  Approved!")
        time.sleep(3)
    else:
        print("\n[1/2] Already approved")

    print("\n[2/2] Calling wrap(asset, to, amount)...")
    wrap_tx = onramp.functions.wrap(
        Web3.to_checksum_address(src_addr),
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
    gas_used_total += receipt["gasUsed"] * receipt["effectiveGasPrice"]

    print("  Wrap complete!")

    time.sleep(2)
    bal_src_post = retry_call(lambda: src.functions.balanceOf(address).call())
    bal_pusd_post = retry_call(lambda: pusd.functions.balanceOf(address).call())

    src_delta = (bal_src_post - bal_src_pre) / 1e6
    pusd_delta = (bal_pusd_post - bal_pusd_pre) / 1e6
    gas_pol = w3.from_wei(gas_used_total, "ether")

    print("\n" + "=" * 60)
    print("WRAP COMPLETE — 1:1, no slippage")
    print("=" * 60)
    print("\nDelta:")
    print(f"  {src_label:<6} {src_delta:+.6f}")
    print(f"  pUSD:   {pusd_delta:+.6f}")
    print(f"  Gas:    {gas_pol:.6f} POL")
    print("\nFinal balances:")
    print(f"  {src_label:<6} ${bal_src_post / 1e6:.2f}")
    print(f"  pUSD:   ${bal_pusd_post / 1e6:.2f}")


if __name__ == "__main__":
    main()
