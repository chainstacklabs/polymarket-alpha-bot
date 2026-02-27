"""
Swap Native USDC to USDC.e on Polygon
=====================================

Polymarket uses USDC.e (bridged USDC), not native USDC.
This script swaps your native USDC to USDC.e using DEX aggregators.

WHY THIS IS NEEDED:
    - Polygon has TWO types of USDC:
      - Native USDC (0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359) - newer
      - USDC.e (0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174) - bridged, used by Polymarket
    - If you bridged USDC from Ethereum, you likely have native USDC
    - Polymarket ONLY accepts USDC.e

WHAT IT DOES:
    1. Checks your native USDC balance
    2. Gets a swap quote from ParaSwap
    3. Approves the DEX aggregator
    4. Executes the swap

USAGE:
    cd backend && uv run python ../experiments/trading/02_swap_to_usdc_e.py

PREREQUISITES:
    - Wallet created (01_setup_wallet.py)
    - Native USDC in wallet
    - POL for gas (~0.1 POL)
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# =============================================================================
# CONFIGURATION
# =============================================================================

# Load .env from project root (experiments/trading/ -> experiments/ -> project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Wallet stored in this folder (experiments/trading/)
WALLET_PATH = Path(__file__).parent / ".wallet.local.json"
RPC_URL = os.environ["CHAINSTACK_NODE"]

# Token addresses
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

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


# =============================================================================
# HELPERS
# =============================================================================


def get_web3():
    from web3 import Web3

    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))


def load_wallet() -> dict:
    if not WALLET_PATH.exists():
        print("ERROR: Wallet not found. Run 01_setup_wallet.py first")
        sys.exit(1)
    return json.loads(WALLET_PATH.read_text())


def retry_call(fn, retries=3, delay=3):
    """Retry RPC calls with exponential backoff."""
    for i in range(retries):
        try:
            return fn()
        except Exception:
            if i < retries - 1:
                print(f"  Retry {i + 1}/{retries}...")
                time.sleep(delay * (i + 1))
            else:
                raise


# =============================================================================
# SWAP PROVIDERS
# =============================================================================


def get_paraswap_quote(address: str, amount: int) -> dict | None:
    """Get swap quote from ParaSwap."""
    try:
        with httpx.Client(timeout=30.0) as http:
            # Get price
            price_resp = http.get(
                "https://apiv5.paraswap.io/prices",
                params={
                    "srcToken": USDC_NATIVE,
                    "destToken": USDC_E,
                    "amount": str(amount),
                    "srcDecimals": "6",
                    "destDecimals": "6",
                    "side": "SELL",
                    "network": "137",
                },
            )

            if price_resp.status_code != 200:
                return None

            price_data = price_resp.json()
            price_route = price_data["priceRoute"]
            dest_amount = int(price_route["destAmount"])

            # Build transaction
            tx_resp = http.post(
                "https://apiv5.paraswap.io/transactions/137",
                params={"ignoreChecks": "true"},
                json={
                    "srcToken": USDC_NATIVE,
                    "destToken": USDC_E,
                    "srcAmount": str(amount),
                    "destAmount": str(int(dest_amount * 0.99)),  # 1% slippage
                    "priceRoute": price_route,
                    "userAddress": address,
                    "partner": "alphapoly",
                },
            )

            if tx_resp.status_code != 200:
                return None

            tx_data = tx_resp.json()
            return {
                "provider": "ParaSwap",
                "buy_amount": dest_amount,
                "to": tx_data["to"],
                "data": tx_data["data"],
                "value": int(tx_data.get("value", 0)),
                "gas": int(tx_data["gas"]),
            }

    except Exception:
        return None


# =============================================================================
# MAIN
# =============================================================================


def main():
    from web3 import Web3

    wallet = load_wallet()
    address = Web3.to_checksum_address(wallet["address"])
    private_key = wallet["private_key"]

    print("=" * 60)
    print("SWAP: Native USDC -> USDC.e")
    print("=" * 60)
    print(f"\nWallet: {address}")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    # Check balances
    usdc_native = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI
    )
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    balance_native = retry_call(lambda: usdc_native.functions.balanceOf(address).call())
    balance_e = retry_call(lambda: usdc_e.functions.balanceOf(address).call())
    pol_balance = w3.from_wei(w3.eth.get_balance(address), "ether")

    print("\nCurrent balances:")
    print(f"  POL:         {pol_balance:.4f}")
    print(f"  USDC native: ${balance_native / 1e6:.2f}")
    print(f"  USDC.e:      ${balance_e / 1e6:.2f}")

    if balance_native == 0:
        print("\nNo native USDC to swap. You're all set!")
        return

    if pol_balance < 0.01:
        print("\nERROR: Insufficient POL for gas")
        return

    swap_amount = balance_native
    print(f"\nGetting quote for ${swap_amount / 1e6:.2f}...")

    # Get quote
    quote = get_paraswap_quote(address, swap_amount)
    if not quote:
        print("ERROR: Could not get swap quote")
        return

    print(f"\nQuote from {quote['provider']}:")
    print(f"  Send:    ${swap_amount / 1e6:.2f} USDC (native)")
    print(f"  Receive: ${quote['buy_amount'] / 1e6:.2f} USDC.e")
    print(f"  Rate:    1:{quote['buy_amount'] / swap_amount:.4f}")

    # Confirm
    confirm = input("\nProceed with swap? (yes/no): ")
    if confirm.lower() != "yes":
        print("Cancelled")
        return

    # Approve
    spender = Web3.to_checksum_address(quote["to"])
    allowance = retry_call(
        lambda: usdc_native.functions.allowance(address, spender).call()
    )

    if allowance < swap_amount:
        print("\n[1/2] Approving DEX...")
        tx = usdc_native.functions.approve(spender, 2**256 - 1).build_transaction(
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

    # Execute swap
    print("\n[2/2] Executing swap...")

    swap_tx = {
        "from": address,
        "to": spender,
        "data": quote["data"],
        "value": quote["value"],
        "gas": int(quote["gas"] * 1.3),
        "gasPrice": int(retry_call(lambda: w3.eth.gas_price) * 1.2),
        "nonce": retry_call(lambda: w3.eth.get_transaction_count(address)),
        "chainId": 137,
    }

    signed = account.sign_transaction(swap_tx)
    tx_hash = retry_call(lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
    print(f"  TX: {tx_hash.hex()}")
    print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        print("  ERROR: Swap failed")
        return

    print("  Swap complete!")

    # Final balances
    time.sleep(2)
    balance_native = retry_call(lambda: usdc_native.functions.balanceOf(address).call())
    balance_e = retry_call(lambda: usdc_e.functions.balanceOf(address).call())

    print("\n" + "=" * 60)
    print("SWAP COMPLETE")
    print("=" * 60)
    print("\nFinal balances:")
    print(f"  USDC native: ${balance_native / 1e6:.2f}")
    print(f"  USDC.e:      ${balance_e / 1e6:.2f}")
    print("\nNext: Run 01_setup_wallet.py approve (if not done)")
    print("Then: Run 03_buy_position.py --list")


if __name__ == "__main__":
    main()
