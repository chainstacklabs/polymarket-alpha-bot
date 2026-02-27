"""
Buy YES or NO Position on Polymarket
=====================================

Buys a single side (YES or NO) by splitting USDC and selling the unwanted side.

HOW IT WORKS:
    1. Split USDC into YES + NO tokens (on-chain, always works)
    2. Sell the unwanted side via CLOB API (requires API access)
    3. Result: You hold only the side you want

ARCHITECTURE NOTE:
    Pure on-chain single-side trading is IMPOSSIBLE on Polymarket because:
    - CTF Exchange fillOrder is operator-only (Polymarket backend)
    - FPMM/AMM pools are deprecated (zero liquidity since 2022)
    - The only on-chain option (split) gives you BOTH sides

    To get a single side, you MUST use the CLOB API to sell the unwanted side.
    The CLOB API blocks datacenter IPs - use a residential proxy or local machine.

USAGE:
    cd backend && uv run python ../experiments/trading/03_buy_position.py [options]

OPTIONS:
    --list          List available markets from portfolios
    --market N      Market index (from --list)
    --side YES|NO   Side to buy
    --amount N      Amount in USD
    --split-only    Only split, don't sell (keep both sides)
    --yes           Auto-confirm

PROXY SUPPORT:
    # From datacenter, use residential proxy:
    HTTPS_PROXY=http://user:pass@proxy:port uv run python 03_buy_position.py ...

    # Or run from your local machine (residential IP)

EXAMPLES:
    uv run python ../experiments/trading/03_buy_position.py --list
    uv run python ../experiments/trading/03_buy_position.py -m 0 -s YES -a 10
    uv run python ../experiments/trading/03_buy_position.py -m 0 -s NO -a 5 --split-only
"""

import argparse
import asyncio
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
PORTFOLIOS_PATH = PROJECT_ROOT / "data" / "_live" / "portfolios.json"
RPC_URL = os.environ["CHAINSTACK_NODE"]

CONTRACTS = {
    "USDC_E": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
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
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "splitPosition",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
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


def load_portfolios() -> list[dict]:
    if not PORTFOLIOS_PATH.exists():
        return []
    data = json.loads(PORTFOLIOS_PATH.read_text())
    return data.get("portfolios", []) if isinstance(data, dict) else data


async def get_market_info(market_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(f"https://gamma-api.polymarket.com/markets/{market_id}")
        return resp.json()


def get_clob_client():
    """Initialize CLOB client with optional proxy support."""
    try:
        from py_clob_client.client import ClobClient
        import py_clob_client.http_helpers.helpers as clob_helpers
    except ImportError:
        print("py-clob-client not installed. Run: uv add py-clob-client")
        return None

    # Proxy support
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        print(f"Using proxy: {proxy[:30]}...")
        clob_helpers._http_client = httpx.Client(http2=True, proxy=proxy, timeout=30.0)

    wallet = load_wallet()

    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            key=wallet["private_key"],
            chain_id=137,
            signature_type=0,
            funder=wallet["address"],
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client
    except Exception as e:
        print(f"CLOB API error: {e}")
        print("\nBlocked by Cloudflare? Use HTTPS_PROXY or run from local machine.")
        return None


# =============================================================================
# COMMANDS
# =============================================================================


async def list_markets():
    """List available markets from portfolios."""
    portfolios = load_portfolios()
    if not portfolios:
        print("No portfolios found. Run the pipeline first.")
        return

    print("=" * 70)
    print("AVAILABLE MARKETS")
    print("=" * 70)

    for i, p in enumerate(portfolios[:10]):
        question = p.get("target_question", "Unknown")[:55]
        print(f"\n[{i}] {question}...")

        try:
            market = await get_market_info(p.get("target_market_id"))
            prices = json.loads(market.get("outcomePrices", "[0, 0]"))
            yes_price = float(prices[0]) if prices else 0
            no_price = float(prices[1]) if len(prices) > 1 else 0
            print(f"    YES: ${yes_price:.2f}  |  NO: ${no_price:.2f}")
        except Exception:
            print("    (prices unavailable)")


async def buy_position(
    market_idx: int, side: str, amount: float, split_only: bool, auto_confirm: bool
):
    """Buy a single side position."""
    from web3 import Web3

    side = side.upper()
    if side not in ["YES", "NO"]:
        print("ERROR: Side must be YES or NO")
        return

    portfolios = load_portfolios()
    if market_idx >= len(portfolios):
        print(f"ERROR: Market index {market_idx} out of range")
        return

    # Get market info
    portfolio = portfolios[market_idx]
    market = await get_market_info(portfolio.get("target_market_id"))

    question = market.get("question", "Unknown")
    condition_id = market.get("conditionId")
    clob_tokens = json.loads(market.get("clobTokenIds", "[]"))
    prices = json.loads(market.get("outcomePrices", "[0, 0]"))

    yes_token, no_token = (
        clob_tokens[0],
        clob_tokens[1] if len(clob_tokens) > 1 else None,
    )
    yes_price = float(prices[0]) if prices else 0
    no_price = float(prices[1]) if len(prices) > 1 else 0

    unwanted_side = "NO" if side == "YES" else "YES"
    unwanted_token = no_token if side == "YES" else yes_token
    unwanted_price = no_price if side == "YES" else yes_price

    # Display plan
    print("=" * 70)
    print(f"BUY {side} POSITION")
    print("=" * 70)
    print(f"\nMarket: {question[:60]}...")
    print(f"\nPrices: YES ${yes_price:.2f} | NO ${no_price:.2f}")
    print("\nPlan:")
    print(f"  1. Split ${amount:.2f} USDC -> {amount:.2f} YES + {amount:.2f} NO")
    print(f"  2. Keep {amount:.2f} {side} tokens")

    if split_only:
        print("  3. SKIP selling (--split-only)")
    else:
        expected_return = amount * unwanted_price
        print(
            f"  3. Sell {amount:.2f} {unwanted_side} @ ~${unwanted_price:.2f} -> ~${expected_return:.2f}"
        )
        print(f"\n  Net cost: ~${amount - expected_return:.2f}")

    # Setup web3
    w3 = get_web3()
    wallet = load_wallet()
    address = Web3.to_checksum_address(wallet["address"])
    account = w3.eth.account.from_key(wallet["private_key"])

    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["USDC_E"]), abi=ERC20_ABI
    )
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI
    )

    usdc_balance = usdc.functions.balanceOf(address).call()
    amount_wei = int(amount * 1e6)

    print(f"\nYour USDC.e: ${usdc_balance / 1e6:.2f}")

    if usdc_balance < amount_wei:
        print("ERROR: Insufficient USDC.e")
        return

    if not auto_confirm:
        if input("\nProceed? (yes/no): ").lower() != "yes":
            print("Cancelled")
            return

    # =========================================================================
    # STEP 1: APPROVE (if needed)
    # =========================================================================

    ctf_address = Web3.to_checksum_address(CONTRACTS["CTF"])
    allowance = usdc.functions.allowance(address, ctf_address).call()

    if allowance < amount_wei:
        print("\n[1/3] Approving USDC.e...")
        tx = usdc.functions.approve(ctf_address, 2**256 - 1).build_transaction(
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
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        print(f"  {'OK' if receipt['status'] == 1 else 'FAILED'}")
        time.sleep(2)
    else:
        print("\n[1/3] Already approved")

    # =========================================================================
    # STEP 2: SPLIT
    # =========================================================================

    print("\n[2/3] Splitting USDC.e -> YES + NO...")

    tx = ctf.functions.splitPosition(
        Web3.to_checksum_address(CONTRACTS["USDC_E"]),
        bytes(32),
        bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        ),
        [1, 2],
        amount_wei,
    ).build_transaction(
        {
            "from": address,
            "nonce": w3.eth.get_transaction_count(address),
            "gas": 300000,
            "gasPrice": int(w3.eth.gas_price * 1.2),
            "chainId": 137,
        }
    )

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        print("  FAILED")
        return

    print("  OK")
    time.sleep(2)

    # =========================================================================
    # STEP 3: SELL UNWANTED SIDE
    # =========================================================================

    if split_only:
        print("\n[3/3] Skipping sell (--split-only)")
    else:
        print(f"\n[3/3] Selling {unwanted_side} via CLOB...")

        client = get_clob_client()
        if client:
            try:
                from py_clob_client.clob_types import OrderArgs, OrderType
                from py_clob_client.order_builder.constants import SELL

                # Fetch tick size for correct precision (varies per market)
                try:
                    tick_size = float(client.get_tick_size(unwanted_token))
                except Exception:
                    tick_size = 0.01
                tick_str = f"{tick_size:.10f}".rstrip("0")
                decimals = len(tick_str.split(".")[1]) if "." in tick_str else 0
                sell_price = round(max(unwanted_price * 0.98, tick_size), decimals)

                order = client.create_order(
                    OrderArgs(
                        token_id=unwanted_token,
                        price=sell_price,
                        size=amount,
                        side=SELL,
                    )
                )
                result = client.post_order(order, OrderType.GTC)
                print(f"  Order placed: {result.get('orderID', result)[:20]}...")

            except Exception as e:
                print(f"  Error: {e}")
        else:
            print("  Skipped (CLOB unavailable)")

    # =========================================================================
    # FINAL STATUS
    # =========================================================================

    time.sleep(2)
    yes_bal = ctf.functions.balanceOf(address, int(yes_token)).call() / 1e6
    no_bal = ctf.functions.balanceOf(address, int(no_token)).call() / 1e6
    usdc_bal = usdc.functions.balanceOf(address).call() / 1e6

    print("\n" + "=" * 70)
    print("RESULT")
    print("=" * 70)
    print(f"\n  USDC.e: ${usdc_bal:.2f}")
    print(f"  YES:    {yes_bal:.2f} tokens")
    print(f"  NO:     {no_bal:.2f} tokens")

    wanted = yes_bal if side == "YES" else no_bal
    unwanted = no_bal if side == "YES" else yes_bal

    if unwanted == 0:
        print(f"\n  SUCCESS: You hold only {side}!")
    elif unwanted > 0 and not split_only:
        print(f"\n  PENDING: {unwanted:.2f} {unwanted_side} tokens in sell order")
        print(f"  When filled, you'll receive ~${unwanted * unwanted_price:.2f} USDC.e")


# =============================================================================
# MAIN
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Buy YES or NO position on Polymarket")
    parser.add_argument("--list", action="store_true", help="List markets")
    parser.add_argument("--market", "-m", type=int, help="Market index")
    parser.add_argument(
        "--side", "-s", choices=["YES", "NO", "yes", "no"], help="Side to buy"
    )
    parser.add_argument("--amount", "-a", type=float, help="Amount in USD")
    parser.add_argument(
        "--split-only", action="store_true", help="Only split, keep both sides"
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-confirm")
    args = parser.parse_args()

    if args.list:
        await list_markets()
    elif args.market is not None and args.side and args.amount:
        await buy_position(
            args.market, args.side, args.amount, args.split_only, args.yes
        )
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
