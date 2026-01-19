"""
09: Polymarket Trading via Official py-clob-client SDK.

WHAT IT DOES
    Demonstrates how to place bets on Polymarket using the official Python SDK.
    This is the recommended approach for most use cases - simpler, maintained by
    Polymarket, and handles all the complexity of order signing.

WHY THIS APPROACH
    - Official SDK maintained by Polymarket team
    - Handles EIP-712 signing automatically
    - Manages API credentials (L1/L2 authentication)
    - Supports all order types: GTC, FOK, FAK, GTD
    - Better error handling and edge cases

HOW IT WORKS
    Polymarket uses a hybrid-decentralized CLOB (Central Limit Order Book):

    1. OFF-CHAIN: Order matching and ordering by operator
       - Orders are signed locally with your private key
       - Submitted to CLOB API for matching
       - Near-instant order placement/cancellation

    2. ON-CHAIN: Settlement on Polygon
       - Matched orders executed via CTF Exchange contract
       - Atomic swaps: USDC <-> Conditional Tokens
       - Non-custodial (you control your keys)

AUTHENTICATION LEVELS
    L1 (Private Key):
        - Sign EIP-712 messages to prove wallet ownership
        - Used to create/derive API credentials
        - Required for signing orders locally

    L2 (API Key):
        - HMAC-SHA256 signed requests
        - Used for all authenticated API calls
        - Generated from L1 authentication

WALLET TYPES
    - EOA (signature_type=0): MetaMask, hardware wallets, direct private key
    - POLY_PROXY (signature_type=1): Magic Link email/Google accounts
    - GNOSIS_SAFE (signature_type=2): Browser proxy wallets (most common)

ORDER TYPES
    - GTC (Good-Till-Cancelled): Limit order, stays on book until filled/cancelled
    - FOK (Fill-Or-Kill): Market order, must fill entirely or cancelled
    - FAK (Fill-And-Kill): Market order, fills what's available, rest cancelled
    - GTD (Good-Till-Date): Limit order with expiration timestamp

PREREQUISITES
    1. Install py-clob-client: uv add py-clob-client
    2. Export private key from your wallet
    3. Have USDC on Polygon
    4. Set token allowances (for EOA wallets)

USAGE
    # Read-only mode (no private key needed)
    uv run python experiments/09_polymarket_api_trading.py

    # Trading mode (requires private key)
    POLYMARKET_PRIVATE_KEY=0x... POLYMARKET_FUNDER=0x... uv run python experiments/09_polymarket_api_trading.py --trade

REFERENCES
    - Docs: https://docs.polymarket.com/developers/CLOB/introduction
    - SDK: https://github.com/Polymarket/py-clob-client
    - Auth: https://docs.polymarket.com/developers/CLOB/authentication
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

# API Endpoints
CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Polygon Chain ID
CHAIN_ID = 137

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"
PORTFOLIOS_PATH = LIVE_DIR / "portfolios.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def load_portfolios() -> list[dict]:
    """Load portfolios from the live data directory."""
    if not PORTFOLIOS_PATH.exists():
        log.warning(f"Portfolios file not found: {PORTFOLIOS_PATH}")
        return []

    data = json.loads(PORTFOLIOS_PATH.read_text())
    portfolios = data.get("portfolios", []) if isinstance(data, dict) else data
    log.info(f"Loaded {len(portfolios)} portfolios")
    return portfolios


async def fetch_market_details(market_id: str) -> dict | None:
    """Fetch market details including token IDs from Gamma API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{GAMMA_API_URL}/markets/{market_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            log.error(f"Failed to fetch market {market_id}: {e}")
            return None


async def fetch_orderbook(token_id: str) -> dict | None:
    """Fetch current orderbook for a token from CLOB API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{CLOB_API_URL}/book", params={"token_id": token_id}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            log.error(f"Failed to fetch orderbook for {token_id}: {e}")
            return None


def calculate_execution_price(orderbook: dict, side: str, amount_usd: float) -> dict:
    """
    Calculate expected execution price for a market order.

    Args:
        orderbook: Order book with bids/asks
        side: 'BUY' or 'SELL'
        amount_usd: Amount in USD to trade

    Returns:
        Dict with avg_price, total_shares, filled_usd, slippage
    """
    # For BUY: we consume asks (people selling)
    # For SELL: we consume bids (people buying)
    orders = orderbook.get("asks" if side == "BUY" else "bids", [])

    if not orders:
        return {"error": "No liquidity"}

    remaining_usd = amount_usd
    total_shares = 0.0
    weighted_price_sum = 0.0

    for order in orders:
        price = float(order["price"])
        size = float(order["size"])

        if side == "BUY":
            # Cost to buy these shares = price * size
            order_cost = price * size
        else:
            # Value of selling these shares = price * size
            order_cost = price * size

        if order_cost <= remaining_usd:
            # Take entire order
            total_shares += size
            weighted_price_sum += price * size
            remaining_usd -= order_cost
        else:
            # Take partial order
            if side == "BUY":
                shares_we_get = remaining_usd / price
            else:
                shares_we_get = remaining_usd / price
            total_shares += shares_we_get
            weighted_price_sum += price * shares_we_get
            remaining_usd = 0
            break

    filled_usd = amount_usd - remaining_usd

    if total_shares == 0:
        return {"error": "Could not fill any amount"}

    avg_price = weighted_price_sum / total_shares
    best_price = float(orders[0]["price"])
    slippage = abs(avg_price - best_price) / best_price if best_price > 0 else 0

    return {
        "avg_price": round(avg_price, 4),
        "total_shares": round(total_shares, 2),
        "filled_usd": round(filled_usd, 2),
        "slippage_pct": round(slippage * 100, 2),
        "best_price": best_price,
    }


# =============================================================================
# READ-ONLY OPERATIONS (No authentication needed)
# =============================================================================


async def demo_read_only_operations():
    """Demonstrate read-only CLOB operations without authentication."""
    log.info("=" * 60)
    log.info("DEMO: Read-Only Operations (No Auth Required)")
    log.info("=" * 60)

    # 1. Check API health
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CLOB_API_URL}/")
        log.info(f"CLOB API Status: {resp.json()}")

        resp = await client.get(f"{CLOB_API_URL}/time")
        log.info(f"Server Time: {resp.json()}")

    # 2. Load a portfolio and analyze trading options
    portfolios = load_portfolios()
    if not portfolios:
        log.warning("No portfolios found - run the pipeline first")
        return

    # Take first portfolio as example
    portfolio = portfolios[0]
    log.info(f"\nAnalyzing portfolio: {portfolio.get('id', 'unknown')}")
    log.info(f"  Target: {portfolio.get('target_title', 'N/A')}")
    log.info(f"  Cover:  {portfolio.get('cover_title', 'N/A')}")

    # 3. Fetch market details
    target_market_id = portfolio.get("target_market_id")
    cover_market_id = portfolio.get("cover_market_id")

    if target_market_id:
        target_market = await fetch_market_details(target_market_id)
        if target_market:
            log.info(f"\nTarget Market: {target_market.get('question', 'N/A')[:60]}...")

            # Get token IDs for YES and NO outcomes
            clob_token_ids = target_market.get("clobTokenIds", [])
            if clob_token_ids:
                log.info(f"  YES Token: {clob_token_ids[0][:20]}...")
                log.info(
                    f"  NO Token:  {clob_token_ids[1][:20] if len(clob_token_ids) > 1 else 'N/A'}..."
                )

                # 4. Fetch orderbook and calculate execution price
                orderbook = await fetch_orderbook(clob_token_ids[0])  # YES token
                if orderbook:
                    log.info("\nOrderbook for YES token:")
                    log.info(
                        f"  Best Bid: {orderbook.get('bids', [{}])[0].get('price', 'N/A') if orderbook.get('bids') else 'N/A'}"
                    )
                    log.info(
                        f"  Best Ask: {orderbook.get('asks', [{}])[0].get('price', 'N/A') if orderbook.get('asks') else 'N/A'}"
                    )

                    # Calculate execution for $10 buy
                    execution = calculate_execution_price(orderbook, "BUY", 10.0)
                    if "error" not in execution:
                        log.info("\nSimulated $10 BUY order:")
                        log.info(f"  Avg Price: ${execution['avg_price']}")
                        log.info(f"  Shares: {execution['total_shares']}")
                        log.info(f"  Slippage: {execution['slippage_pct']}%")


# =============================================================================
# AUTHENTICATED TRADING (Requires private key)
# =============================================================================


def setup_trading_client():
    """
    Set up authenticated CLOB client for trading.

    Requires environment variables:
        POLYMARKET_PRIVATE_KEY: Your wallet's private key (0x...)
        POLYMARKET_FUNDER: Your funder address (shown on Polymarket profile)
        POLYMARKET_SIGNATURE_TYPE: 0=EOA, 1=Magic, 2=Browser proxy (default: 2)
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError:
        log.error("py-clob-client not installed. Run: uv add py-clob-client")
        return None

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    funder = os.environ.get("POLYMARKET_FUNDER")
    sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "2"))

    if not private_key:
        log.error("POLYMARKET_PRIVATE_KEY environment variable not set")
        log.info("Export your private key from wallet and set:")
        log.info("  export POLYMARKET_PRIVATE_KEY=0x...")
        return None

    if not funder:
        log.error("POLYMARKET_FUNDER environment variable not set")
        log.info("Find your funder address on your Polymarket profile and set:")
        log.info("  export POLYMARKET_FUNDER=0x...")
        return None

    log.info("Setting up trading client...")
    log.info(f"  Funder: {funder[:10]}...{funder[-6:]}")
    log.info(f"  Signature Type: {sig_type}")

    client = ClobClient(
        CLOB_API_URL,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=sig_type,
        funder=funder,
    )

    # Create or derive API credentials
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        log.info("API credentials set up successfully")
        return client
    except Exception as e:
        log.error(f"Failed to set up API credentials: {e}")
        return None


async def demo_trading_operations(client):
    """
    Demonstrate trading operations with authenticated client.

    WARNING: This will place REAL orders if uncommented!
    """
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
    except ImportError:
        log.error("py-clob-client not installed")
        return

    log.info("=" * 60)
    log.info("DEMO: Trading Operations (Authenticated)")
    log.info("=" * 60)

    # 1. Check balances
    log.info("\nFetching account balances...")
    try:
        # Note: This would show your USDC and token balances
        # balance = client.get_balance_allowance(asset_type="USDC")
        # log.info(f"USDC Balance: {balance}")
        pass
    except Exception as e:
        log.warning(f"Could not fetch balances: {e}")

    # 2. Load portfolio and prepare trade
    portfolios = load_portfolios()
    if not portfolios:
        log.warning("No portfolios found")
        return

    portfolio = portfolios[0]
    target_market_id = portfolio.get("target_market_id")

    if not target_market_id:
        log.warning("No target market ID in portfolio")
        return

    market = await fetch_market_details(target_market_id)
    if not market:
        return

    clob_token_ids = market.get("clobTokenIds", [])
    if not clob_token_ids:
        log.warning("No CLOB token IDs for market")
        return

    yes_token_id = clob_token_ids[0]

    # 3. Example: Create a limit order (NOT submitted)
    log.info("\nExample: Creating a limit order for YES token")
    log.info(f"  Token ID: {yes_token_id[:30]}...")

    # THIS IS A SIMULATION - uncomment to actually create orders
    """
    order_args = OrderArgs(
        token_id=yes_token_id,
        price=0.50,  # Limit price
        size=10.0,   # Number of shares
        side=BUY,
    )

    # Sign the order locally
    signed_order = client.create_order(order_args)
    log.info(f"Order signed: {signed_order}")

    # Post the order to CLOB
    # WARNING: This will place a REAL order!
    # response = client.post_order(signed_order, OrderType.GTC)
    # log.info(f"Order placed: {response}")
    """

    # 4. Example: Create a market order (NOT submitted)
    log.info("\nExample: Creating a market order")

    """
    market_order_args = MarketOrderArgs(
        token_id=yes_token_id,
        amount=5.0,  # Amount in USDC
        side=BUY,
        order_type=OrderType.FOK,  # Fill-or-Kill
    )

    # Sign the order
    signed_market_order = client.create_market_order(market_order_args)

    # Post the order
    # WARNING: This will place a REAL order!
    # response = client.post_order(signed_market_order, OrderType.FOK)
    """

    log.info("\nTrading demo complete (no real orders placed)")
    log.info("Uncomment the code sections to enable actual trading")


# =============================================================================
# TOKEN ALLOWANCES (Required for EOA wallets)
# =============================================================================


def explain_allowances():
    """Explain token allowances required for trading with EOA wallets."""
    log.info("=" * 60)
    log.info("TOKEN ALLOWANCES (Required for MetaMask/EOA wallets)")
    log.info("=" * 60)
    log.info("""
Before you can trade with a direct wallet (MetaMask, hardware wallet),
you need to approve the exchange contracts to spend your tokens.

CONTRACTS TO APPROVE:
    Exchange Contracts (approve both USDC and CTF tokens for these):
    - 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E (CTF Exchange)
    - 0xC5d563A36AE78145C45a50134d48A1215220f80a (Neg Risk CTF Exchange)
    - 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296 (Neg Risk Adapter)

TOKEN ADDRESSES:
    - USDC: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
    - CTF:  0x4D97DCd97eC945f40cF65F87097ACe5EA0476045

HOW TO SET ALLOWANCES:
    1. Go to PolygonScan for each token
    2. Connect wallet via "Write Contract"
    3. Call approve(spender, amount) for each exchange contract
    4. Amount: Use max uint256 for unlimited, or specific amount

    Example Python code using web3.py:

    import os
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(os.environ["CHAINSTACK_NODE"]))

    usdc = w3.eth.contract(
        address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        abi=ERC20_ABI
    )

    # Approve CTF Exchange to spend USDC
    tx = usdc.functions.approve(
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        2**256 - 1  # Max approval
    ).build_transaction({...})

NOTE: Email/Magic wallet users don't need to do this - it's automatic.
""")


# =============================================================================
# PORTFOLIO EXECUTION STRATEGY
# =============================================================================


async def analyze_portfolio_execution(portfolio: dict):
    """
    Analyze how to execute a portfolio trade.

    For covering portfolios, we typically need to:
    1. Buy YES on target market
    2. Buy YES on cover market

    The sum should be < $1.00 for guaranteed $1.00 payout.
    """
    log.info("=" * 60)
    log.info("PORTFOLIO EXECUTION ANALYSIS")
    log.info("=" * 60)

    target_market_id = portfolio.get("target_market_id")
    cover_market_id = portfolio.get("cover_market_id")

    log.info(f"\nPortfolio: {portfolio.get('id', 'unknown')}")
    log.info(f"Target: {portfolio.get('target_title', 'N/A')}")
    log.info(f"Cover:  {portfolio.get('cover_title', 'N/A')}")

    # Fetch both markets
    target_market = (
        await fetch_market_details(target_market_id) if target_market_id else None
    )
    cover_market = (
        await fetch_market_details(cover_market_id) if cover_market_id else None
    )

    if not target_market or not cover_market:
        log.error("Could not fetch market details")
        return

    # Get YES token IDs (first token is YES)
    target_yes_token = target_market.get("clobTokenIds", [None])[0]
    cover_yes_token = cover_market.get("clobTokenIds", [None])[0]

    if not target_yes_token or not cover_yes_token:
        log.error("Could not get token IDs")
        return

    # Fetch orderbooks
    target_book = await fetch_orderbook(target_yes_token)
    cover_book = await fetch_orderbook(cover_yes_token)

    if not target_book or not cover_book:
        log.error("Could not fetch orderbooks")
        return

    # Get best ask prices (what we'd pay to buy YES)
    target_asks = target_book.get("asks", [])
    cover_asks = cover_book.get("asks", [])

    target_best_ask = float(target_asks[0]["price"]) if target_asks else None
    cover_best_ask = float(cover_asks[0]["price"]) if cover_asks else None

    if target_best_ask is None or cover_best_ask is None:
        log.error("No liquidity in one or both markets")
        return

    log.info("\nCurrent Prices (Best Ask):")
    log.info(f"  Target YES: ${target_best_ask:.4f}")
    log.info(f"  Cover YES:  ${cover_best_ask:.4f}")
    log.info(f"  Total Cost: ${target_best_ask + cover_best_ask:.4f}")

    # Calculate if there's still alpha
    total_cost = target_best_ask + cover_best_ask
    guaranteed_payout = 1.00
    profit = guaranteed_payout - total_cost

    log.info("\nArbitrage Analysis:")
    log.info("  Guaranteed Payout: $1.00")
    log.info(f"  Total Cost: ${total_cost:.4f}")
    log.info(f"  Profit: ${profit:.4f} ({profit / total_cost * 100:.1f}%)")

    if profit > 0:
        log.info("\n*** PROFITABLE OPPORTUNITY ***")
        log.info("Execution Strategy:")
        log.info(f"  1. Buy 1 share Target YES @ ${target_best_ask:.4f}")
        log.info(f"  2. Buy 1 share Cover YES @ ${cover_best_ask:.4f}")
        log.info("  3. Wait for market resolution")
        log.info("  4. Collect $1.00 (one must be true)")
    else:
        log.info("\n*** NO ALPHA (cost >= $1.00) ***")

    # Simulate execution for $100 position
    log.info("\nSimulated $100 Position Execution:")

    target_exec = calculate_execution_price(target_book, "BUY", 50.0)
    cover_exec = calculate_execution_price(cover_book, "BUY", 50.0)

    if "error" not in target_exec and "error" not in cover_exec:
        log.info(
            f"  Target YES: {target_exec['total_shares']:.2f} shares @ ${target_exec['avg_price']:.4f} (slippage: {target_exec['slippage_pct']}%)"
        )
        log.info(
            f"  Cover YES:  {cover_exec['total_shares']:.2f} shares @ ${cover_exec['avg_price']:.4f} (slippage: {cover_exec['slippage_pct']}%)"
        )


# =============================================================================
# MAIN
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading via py-clob-client"
    )
    parser.add_argument(
        "--trade",
        action="store_true",
        help="Enable trading mode (requires credentials)",
    )
    parser.add_argument(
        "--allowances", action="store_true", help="Explain token allowances"
    )
    parser.add_argument(
        "--analyze", action="store_true", help="Analyze portfolio execution"
    )
    args = parser.parse_args()

    if args.allowances:
        explain_allowances()
        return

    # Always run read-only demo
    await demo_read_only_operations()

    # Analyze portfolio execution
    if args.analyze:
        portfolios = load_portfolios()
        if portfolios:
            await analyze_portfolio_execution(portfolios[0])

    # Trading mode
    if args.trade:
        client = setup_trading_client()
        if client:
            await demo_trading_operations(client)


if __name__ == "__main__":
    asyncio.run(main())
