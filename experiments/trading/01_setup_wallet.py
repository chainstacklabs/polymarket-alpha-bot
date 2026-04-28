"""
Polymarket Wallet Setup
=======================

Creates a new Polygon wallet and sets up all required Polymarket approvals.

WHAT IT DOES:
    1. Generates a new Ethereum-compatible wallet (private key + address)
    2. Saves credentials to .wallet.local.json (gitignored)
    3. Sets pUSD + CTF approvals for all Polymarket V2 contracts.

PREREQUISITES:
    - Fund the wallet with POL (for gas) and pUSD (for trading)
    - If you only have USDC.e, run 02_wrap_to_pusd.py first

USAGE:
    cd backend && uv run python ../experiments/trading/01_setup_wallet.py [command]

COMMANDS:
    generate    Create new wallet (default if no wallet exists)
    approve     Set Polymarket approvals (requires POL for gas)
    status      Check wallet balances and approval status
    all         Generate wallet + set approvals

EXAMPLES:
    uv run python ../experiments/trading/01_setup_wallet.py
    uv run python ../experiments/trading/01_setup_wallet.py status
    uv run python ../experiments/trading/01_setup_wallet.py approve
"""

import argparse
import json
import os
from pathlib import Path

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

# Polymarket V2 contracts on Polygon (post-2026-04-28 cutover).
CONTRACTS = {
    "PUSD": "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "CTF_EXCHANGE": "0xE111180000d2663C0091e4f400237545B87B996B",
    "NEG_RISK_CTF_EXCHANGE": "0xe2222d279d744050d28e00520010520000310F59",
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

CTF_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_operator", "type": "address"},
            {"name": "_approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
]


# =============================================================================
# HELPERS
# =============================================================================


def get_web3():
    from web3 import Web3

    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))


def load_wallet() -> dict | None:
    if WALLET_PATH.exists():
        return json.loads(WALLET_PATH.read_text())
    return None


def save_wallet(wallet: dict):
    WALLET_PATH.write_text(json.dumps(wallet, indent=2))


# =============================================================================
# COMMANDS
# =============================================================================


def cmd_generate():
    """Generate a new wallet."""
    from eth_account import Account

    if WALLET_PATH.exists():
        print(f"Wallet already exists at {WALLET_PATH}")
        wallet = load_wallet()
        print(f"Address: {wallet['address']}")
        print("\nTo create a new wallet, delete the existing file first.")
        return

    account = Account.create()
    wallet = {
        "address": account.address,
        "private_key": account.key.hex(),
    }
    save_wallet(wallet)

    print("=" * 60)
    print("NEW WALLET CREATED")
    print("=" * 60)
    print(f"\nAddress: {account.address}")
    print(f"Saved to: {WALLET_PATH}")
    print("\nNEXT STEPS:")
    print("  1. Send POL to this address (for gas, ~0.5 POL is enough)")
    print("  2. Send pUSD to this address (for trading)")
    print("  3. Run: uv run python 01_setup_wallet.py approve")


def cmd_status():
    """Check wallet balances and approval status."""
    from web3 import Web3

    wallet = load_wallet()
    if not wallet:
        print("No wallet found. Run: uv run python 01_setup_wallet.py generate")
        return

    w3 = get_web3()
    address = Web3.to_checksum_address(wallet["address"])

    print("=" * 60)
    print("WALLET STATUS")
    print("=" * 60)
    print(f"\nAddress: {address}")

    # Balances
    pol = w3.from_wei(w3.eth.get_balance(address), "ether")
    pusd = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["PUSD"]), abi=ERC20_ABI
    )
    pusd_balance = pusd.functions.balanceOf(address).call() / 1e6

    print("\nBalances:")
    print(f"  POL:  {pol:.4f}")
    print(f"  pUSD: ${pusd_balance:.2f}")

    print("\nApprovals (pUSD collateral):")
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI
    )

    approvals = [
        (
            "pUSD → CTF",
            pusd.functions.allowance(address, CONTRACTS["CTF"]).call(),
        ),
        (
            "pUSD → Exchange",
            pusd.functions.allowance(address, CONTRACTS["CTF_EXCHANGE"]).call(),
        ),
        (
            "pUSD → NegRisk Exchange",
            pusd.functions.allowance(
                address, CONTRACTS["NEG_RISK_CTF_EXCHANGE"]
            ).call(),
        ),
        (
            "CTF → Exchange",
            ctf.functions.isApprovedForAll(
                address, CONTRACTS["CTF_EXCHANGE"]
            ).call(),
        ),
        (
            "CTF → NegRisk Exchange",
            ctf.functions.isApprovedForAll(
                address, CONTRACTS["NEG_RISK_CTF_EXCHANGE"]
            ).call(),
        ),
    ]

    all_approved = True
    for name, approved in approvals:
        status = "OK" if (approved is True or approved > 0) else "MISSING"
        if status == "MISSING":
            all_approved = False
        print(f"  {name}: {status}")

    if not all_approved:
        print("\nRun: uv run python 01_setup_wallet.py approve")


def cmd_approve():
    """Set all Polymarket approvals."""
    from web3 import Web3
    import time

    wallet = load_wallet()
    if not wallet:
        print("No wallet found. Run: uv run python 01_setup_wallet.py generate")
        return

    w3 = get_web3()
    address = Web3.to_checksum_address(wallet["address"])
    account = w3.eth.account.from_key(wallet["private_key"])

    # Check POL balance
    pol = w3.from_wei(w3.eth.get_balance(address), "ether")
    if pol < 0.01:
        print(f"ERROR: Insufficient POL for gas (have {pol:.4f}, need ~0.01)")
        return

    print("=" * 60)
    print("SETTING POLYMARKET V2 APPROVALS (pUSD collateral)")
    print("=" * 60)

    pusd = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["PUSD"]), abi=ERC20_ABI
    )
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI
    )

    MAX_UINT256 = 2**256 - 1
    approvals = [
        ("pUSD → CTF", pusd, "approve", CONTRACTS["CTF"], MAX_UINT256),
        ("pUSD → Exchange", pusd, "approve", CONTRACTS["CTF_EXCHANGE"], MAX_UINT256),
        (
            "pUSD → NegRisk Exchange",
            pusd,
            "approve",
            CONTRACTS["NEG_RISK_CTF_EXCHANGE"],
            MAX_UINT256,
        ),
        ("CTF → Exchange", ctf, "setApprovalForAll", CONTRACTS["CTF_EXCHANGE"], True),
        (
            "CTF → NegRisk Exchange",
            ctf,
            "setApprovalForAll",
            CONTRACTS["NEG_RISK_CTF_EXCHANGE"],
            True,
        ),
    ]

    for i, (name, contract, method, spender, value) in enumerate(approvals, 1):
        print(f"\n[{i}/{len(approvals)}] {name}...")

        try:
            fn = getattr(contract.functions, method)
            tx = fn(Web3.to_checksum_address(spender), value).build_transaction(
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
            print(f"  TX: {tx_hash.hex()[:20]}...")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            status = "OK" if receipt["status"] == 1 else "FAILED"
            print(f"  Status: {status}")

            time.sleep(1)

        except Exception as e:
            print(f"  Error: {e}")

    print("\n" + "=" * 60)
    print("APPROVALS COMPLETE")
    print("=" * 60)
    print("\nYou can now trade on Polymarket!")
    print("Run: uv run python 03_buy_position.py --list")


# =============================================================================
# MAIN
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Polymarket Wallet Setup")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["generate", "approve", "status", "all"],
        help="Command to run",
    )
    args = parser.parse_args()

    if args.command == "generate":
        cmd_generate()
    elif args.command == "approve":
        cmd_approve()
    elif args.command == "status":
        if not WALLET_PATH.exists():
            cmd_generate()
        else:
            cmd_status()
    elif args.command == "all":
        cmd_generate()
        print()
        cmd_approve()


if __name__ == "__main__":
    main()
