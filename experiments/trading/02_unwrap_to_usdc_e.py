"""
Unwrap pUSD -> USDC.e on Polygon  (lossless: 1:1 + gas only)
============================================================

The reverse of ``02_wrap_to_pusd.py``. Uses Polymarket's CollateralOfframp
to convert pUSD back to USDC.e at exactly 1:1 — no slippage, no spread,
no fees. You pay only Polygon gas (~$0.003 at current rates).

This is the on-ramp flow's exit path. The Polymarket app's swap UI runs
through DEX routers and charges slippage; this contract path doesn't.

WHAT IT DOES:
    1. Reads pUSD balance.
    2. Approves CollateralOfframp to pull pUSD.
    3. Calls ``unwrap(asset=USDC.e, to, amount)`` on the Offramp;
       receives USDC.e 1:1.

USAGE:
    cd backend && uv run python ../experiments/trading/02_unwrap_to_usdc_e.py

    # Partial amount (default is full pUSD balance):
    UNWRAP_AMOUNT_USD=5 uv run python ../experiments/trading/02_unwrap_to_usdc_e.py

PREREQUISITES:
    - Wallet with pUSD (run 02_wrap_to_pusd.py first to mint some).
    - POL for gas (~0.01 POL).

ABI verified against Polygonscan source (CollateralOfframp.sol BUSL-1.1).
``unwrap(address asset, address to, uint256 amount)`` requires a prior
``approve`` on pUSD and is gated by an `onlyUnpaused` modifier on the
target asset — this script reads ``Offramp.paused(USDC.e)`` first and
aborts on true.

The ``asset`` parameter is the token you want to receive, NOT the token
being burned. Pass USDC.e to get USDC.e back; passing pUSD reverts with
``InvalidAsset()`` (selector 0xc891add2).
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
COLLATERAL_OFFRAMP = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"

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

# unwrap(asset, to, amount): `asset` is the asset to RECEIVE (e.g. USDC.e).
# Caller must hold prior `approve` on pUSD against the Offramp.
OFFRAMP_ABI = [
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
    print("UNWRAP: pUSD -> USDC.e  (1:1, gas only)")
    print("=" * 60)
    print(f"\nWallet: {address}")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)
    offramp = w3.eth.contract(
        address=Web3.to_checksum_address(COLLATERAL_OFFRAMP), abi=OFFRAMP_ABI
    )

    bal_usdce_pre = retry_call(lambda: usdc_e.functions.balanceOf(address).call())
    bal_pusd_pre = retry_call(lambda: pusd.functions.balanceOf(address).call())
    pol_pre_wei = retry_call(lambda: w3.eth.get_balance(address))

    print("\nCurrent balances:")
    print(f"  POL:    {w3.from_wei(pol_pre_wei, 'ether'):.4f}")
    print(f"  pUSD:   ${bal_pusd_pre / 1e6:.2f}")
    print(f"  USDC.e: ${bal_usdce_pre / 1e6:.2f}")

    if bal_pusd_pre == 0:
        print("\nNo pUSD to unwrap. Run 02_wrap_to_pusd.py first.")
        return

    if pol_pre_wei < int(0.01 * 1e18):
        print("\nERROR: Insufficient POL for gas")
        return

    # Pre-flight: Offramp.paused(USDC.e) must be false
    is_paused = retry_call(
        lambda: offramp.functions.paused(Web3.to_checksum_address(USDC_E)).call()
    )
    if is_paused:
        print("\nERROR: Offramp is paused for USDC.e — abort")
        return

    # Custom amount via UNWRAP_AMOUNT_USD env var; default = full pUSD balance.
    custom_amount_usd = os.environ.get("UNWRAP_AMOUNT_USD")
    if custom_amount_usd:
        amount = int(float(custom_amount_usd) * 1e6)
        if amount > bal_pusd_pre:
            print(f"\nERROR: UNWRAP_AMOUNT_USD=${custom_amount_usd} exceeds balance")
            return
    else:
        amount = bal_pusd_pre
    print(f"\nUnwrapping ${amount / 1e6:.2f} pUSD -> USDC.e")
    confirm = input("Proceed? (yes/no): ")
    if confirm.lower() != "yes":
        print("Cancelled")
        return

    spender = Web3.to_checksum_address(COLLATERAL_OFFRAMP)
    allowance = retry_call(lambda: pusd.functions.allowance(address, spender).call())

    gas_used_total = 0

    if allowance < amount:
        print("\n[1/2] Approving CollateralOfframp...")
        tx = pusd.functions.approve(spender, 2**256 - 1).build_transaction(
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

    print("\n[2/2] Calling unwrap(asset=USDC.e, to, amount)...")
    unwrap_tx = offramp.functions.unwrap(
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
    signed = account.sign_transaction(unwrap_tx)
    tx_hash = retry_call(lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
    print(f"  TX: {tx_hash.hex()}")
    print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        print("  ERROR: Unwrap failed")
        return
    gas_used_total += receipt["gasUsed"] * receipt["effectiveGasPrice"]

    print("  Unwrap complete!")

    time.sleep(2)
    bal_usdce_post = retry_call(lambda: usdc_e.functions.balanceOf(address).call())
    bal_pusd_post = retry_call(lambda: pusd.functions.balanceOf(address).call())

    pusd_delta = (bal_pusd_post - bal_pusd_pre) / 1e6
    usdce_delta = (bal_usdce_post - bal_usdce_pre) / 1e6
    gas_pol = w3.from_wei(gas_used_total, "ether")

    print("\n" + "=" * 60)
    print("UNWRAP COMPLETE — 1:1, no slippage")
    print("=" * 60)
    print("\nDelta:")
    print(f"  pUSD:   {pusd_delta:+.6f}")
    print(f"  USDC.e: {usdce_delta:+.6f}")
    print(f"  Gas:    {gas_pol:.6f} POL")
    print("\nFinal balances:")
    print(f"  pUSD:   ${bal_pusd_post / 1e6:.2f}")
    print(f"  USDC.e: ${bal_usdce_post / 1e6:.2f}")


if __name__ == "__main__":
    main()
