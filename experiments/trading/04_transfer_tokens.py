"""
Transfer USDC and USDC.e to Another Wallet
==========================================

Transfers all USDC and USDC.e from the local wallet to a specified address.

USAGE:
    cd backend && uv run python ../experiments/trading/04_transfer_tokens.py <target_address>
"""

import json
import sys
import time
import os
from pathlib import Path

from dotenv import load_dotenv

# =============================================================================
# CONFIGURATION
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

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
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
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


# =============================================================================
# MAIN
# =============================================================================


def main():
    from web3 import Web3

    if len(sys.argv) < 2:
        print("Usage: uv run python 04_transfer_tokens.py <target_address> [--yes]")
        sys.exit(1)

    target_address = Web3.to_checksum_address(sys.argv[1])
    skip_confirm = "--yes" in sys.argv

    wallet = load_wallet()
    address = Web3.to_checksum_address(wallet["address"])
    private_key = wallet["private_key"]

    print("=" * 60)
    print("TRANSFER TOKENS")
    print("=" * 60)
    print(f"\nFrom: {address}")
    print(f"To:   {target_address}")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    # Check balances
    usdc_native = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI
    )
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    balance_native = usdc_native.functions.balanceOf(address).call()
    balance_e = usdc_e.functions.balanceOf(address).call()
    pol_balance = w3.from_wei(w3.eth.get_balance(address), "ether")

    print("\nCurrent balances:")
    print(f"  POL:         {pol_balance:.4f}")
    print(f"  USDC native: ${balance_native / 1e6:.2f}")
    print(f"  USDC.e:      ${balance_e / 1e6:.2f}")

    if balance_native == 0 and balance_e == 0:
        print("\nNo tokens to transfer.")
        return

    if pol_balance < 0.01:
        print("\nERROR: Insufficient POL for gas")
        return

    # Confirm
    total = (balance_native + balance_e) / 1e6
    if skip_confirm:
        print(f"\nTransferring ${total:.2f} total to {target_address}...")
    else:
        confirm = input(
            f"\nTransfer ${total:.2f} total to {target_address}? (yes/no): "
        )
        if confirm.lower() != "yes":
            print("Cancelled")
            return

    tx_count = 0

    # Transfer USDC.e
    if balance_e > 0:
        tx_count += 1
        print(f"\n[{tx_count}] Transferring ${balance_e / 1e6:.2f} USDC.e...")

        tx = usdc_e.functions.transfer(target_address, balance_e).build_transaction(
            {
                "from": address,
                "nonce": w3.eth.get_transaction_count(address),
                "gas": 100000,
                "gasPrice": int(w3.eth.gas_price * 1.2),
                "chainId": 137,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  TX: {tx_hash.hex()}")
        print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt["status"] != 1:
            print("  ERROR: Transfer failed")
            return
        print("  USDC.e transfer complete!")
        time.sleep(2)

    # Transfer native USDC
    if balance_native > 0:
        tx_count += 1
        print(f"\n[{tx_count}] Transferring ${balance_native / 1e6:.2f} native USDC...")

        tx = usdc_native.functions.transfer(
            target_address, balance_native
        ).build_transaction(
            {
                "from": address,
                "nonce": w3.eth.get_transaction_count(address),
                "gas": 100000,
                "gasPrice": int(w3.eth.gas_price * 1.2),
                "chainId": 137,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  TX: {tx_hash.hex()}")
        print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt["status"] != 1:
            print("  ERROR: Transfer failed")
            return
        print("  Native USDC transfer complete!")

    # Final balances
    time.sleep(2)
    balance_native = usdc_native.functions.balanceOf(address).call()
    balance_e = usdc_e.functions.balanceOf(address).call()

    print("\n" + "=" * 60)
    print("TRANSFER COMPLETE")
    print("=" * 60)
    print("\nFinal balances:")
    print(f"  USDC native: ${balance_native / 1e6:.2f}")
    print(f"  USDC.e:      ${balance_e / 1e6:.2f}")


def main_pol():
    """Transfer all POL to target address."""
    from web3 import Web3

    if len(sys.argv) < 2:
        print(
            "Usage: uv run python 04_transfer_tokens.py --pol <target_address> [--yes]"
        )
        sys.exit(1)

    # Find target address (skip --pol flag)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("ERROR: No target address provided")
        sys.exit(1)

    target_address = Web3.to_checksum_address(args[0])
    skip_confirm = "--yes" in sys.argv

    wallet = load_wallet()
    address = Web3.to_checksum_address(wallet["address"])
    private_key = wallet["private_key"]

    print("=" * 60)
    print("TRANSFER POL")
    print("=" * 60)
    print(f"\nFrom: {address}")
    print(f"To:   {target_address}")

    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)

    # Check balance
    pol_balance_wei = w3.eth.get_balance(address)
    pol_balance = w3.from_wei(pol_balance_wei, "ether")

    print(f"\nCurrent POL balance: {pol_balance:.6f}")

    if pol_balance < 0.001:
        print("\nNo POL to transfer.")
        return

    # Calculate max transfer (balance - gas cost)
    gas_price = int(w3.eth.gas_price * 1.2)
    gas_limit = 21000  # Standard ETH/POL transfer
    gas_cost = gas_price * gas_limit

    transfer_amount = pol_balance_wei - gas_cost
    if transfer_amount <= 0:
        print("\nERROR: Insufficient POL to cover gas")
        return

    transfer_pol = w3.from_wei(transfer_amount, "ether")
    gas_cost_pol = w3.from_wei(gas_cost, "ether")

    print(f"Transfer amount: {transfer_pol:.6f} POL")
    print(f"Gas cost:        {gas_cost_pol:.6f} POL")

    # Confirm
    if skip_confirm:
        print(f"\nTransferring {transfer_pol:.6f} POL to {target_address}...")
    else:
        confirm = input(
            f"\nTransfer {transfer_pol:.6f} POL to {target_address}? (yes/no): "
        )
        if confirm.lower() != "yes":
            print("Cancelled")
            return

    # Execute transfer
    print("\nExecuting transfer...")

    tx = {
        "from": address,
        "to": target_address,
        "value": transfer_amount,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "nonce": w3.eth.get_transaction_count(address),
        "chainId": 137,
    }

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX: {tx_hash.hex()}")
    print(f"  View: https://polygonscan.com/tx/{tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        print("  ERROR: Transfer failed")
        return

    # Final balance
    time.sleep(2)
    final_balance = w3.from_wei(w3.eth.get_balance(address), "ether")

    print("\n" + "=" * 60)
    print("POL TRANSFER COMPLETE")
    print("=" * 60)
    print(f"\nFinal POL balance: {final_balance:.6f}")


if __name__ == "__main__":
    if "--pol" in sys.argv:
        main_pol()
    else:
        main()
