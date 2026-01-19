"""
10: Direct Blockchain Trading via web3.py (Advanced).

WHAT IT DOES
    Demonstrates direct interaction with Polymarket smart contracts on Polygon,
    bypassing the CLOB API entirely. This is the "low-level" approach.

WHY THIS APPROACH
    - Full control over transaction parameters
    - Direct interaction with Polygon blockchain
    - Educational: understand how trades actually settle
    - Useful for building custom execution infrastructure
    - Can execute on-chain cancellations if CLOB is down

WHEN TO USE THIS
    - Advanced users who need fine-grained control
    - Building custom MEV/execution strategies
    - Research and educational purposes
    - Backup execution if CLOB API is unavailable

WHEN NOT TO USE THIS
    - Most trading use cases (use py-clob-client instead)
    - If you're not familiar with web3/blockchain
    - For simple order placement

HOW POLYMARKET WORKS ON-CHAIN
    1. Markets are created via Conditional Tokens Framework (CTF)
       - Each market has a condition with 2 outcomes (YES/NO)
       - Outcomes are ERC1155 tokens (position tokens)

    2. Trading uses CTF Exchange contract
       - Atomic swaps: USDC <-> Outcome Tokens
       - Operator submits matched orders via matchOrders()
       - Users can also directly redeem winning positions

    3. Key Contracts (Polygon):
       - CTF:                0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
       - CTF Exchange:       0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
       - NegRisk CTF Exchange: 0xC5d563A36AE78145C45a50134d48A1215220f80a
       - NegRisk Adapter:    0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
       - USDC:               0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174

ORDER STRUCTURE (EIP-712)
    Orders are signed messages with:
    - salt: Random nonce
    - maker: Order creator address
    - signer: Key that signs the order
    - taker: Address that can fill (0 for anyone)
    - tokenId: CTF token being traded
    - makerAmount: Amount maker gives
    - takerAmount: Amount maker receives
    - expiration: Unix timestamp
    - nonce: For replay protection
    - feeRateBps: Fee in basis points
    - side: 0=BUY, 1=SELL
    - signatureType: 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE

PREREQUISITES
    1. Install web3.py: uv add web3
    2. Have POL for gas (Polygon native token)
    3. Have USDC on Polygon
    4. RPC endpoint (use public or your own)

USAGE
    uv run python experiments/10_blockchain_direct_trading.py

REFERENCES
    - CTF Exchange: https://github.com/Polymarket/ctf-exchange
    - CTF Docs: https://docs.gnosis.io/conditionaltokens/
    - Contract: https://polygonscan.com/address/0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
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

# Polygon RPC endpoint (single node)
CHAINSTACK_NODE = os.environ.get("CHAINSTACK_NODE")
if not CHAINSTACK_NODE:
    raise ValueError("CHAINSTACK_NODE environment variable not set")

# Contract Addresses (Polygon Mainnet)
CONTRACTS = {
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "CTF_EXCHANGE": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "NEGRISK_CTF_EXCHANGE": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "NEGRISK_ADAPTER": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}

# Chain ID
POLYGON_CHAIN_ID = 137

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
# MINIMAL ABIs (Only functions we need)
# =============================================================================

# ERC20 ABI (USDC)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
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
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]

# ERC1155 ABI (CTF Tokens)
ERC1155_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
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

# CTF Exchange ABI (partial - key functions)
CTF_EXCHANGE_ABI = [
    # Events
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "orderHash", "type": "bytes32"},
            {"indexed": True, "name": "maker", "type": "address"},
            {"indexed": True, "name": "taker", "type": "address"},
            {"indexed": False, "name": "makerAssetId", "type": "uint256"},
            {"indexed": False, "name": "takerAssetId", "type": "uint256"},
            {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
            {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
            {"indexed": False, "name": "fee", "type": "uint256"},
        ],
        "name": "OrderFilled",
        "type": "event",
    },
    # View functions
    {
        "constant": True,
        "inputs": [{"name": "", "type": "bytes32"}],
        "name": "orderStatus",
        "outputs": [
            {"name": "isFilledOrCancelled", "type": "bool"},
            {"name": "remaining", "type": "uint256"},
        ],
        "type": "function",
    },
    # Cancel order (user-callable)
    {
        "constant": False,
        "inputs": [
            {
                "components": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                    {"name": "signer", "type": "address"},
                    {"name": "taker", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "makerAmount", "type": "uint256"},
                    {"name": "takerAmount", "type": "uint256"},
                    {"name": "expiration", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "feeRateBps", "type": "uint256"},
                    {"name": "side", "type": "uint8"},
                    {"name": "signatureType", "type": "uint8"},
                ],
                "name": "order",
                "type": "tuple",
            }
        ],
        "name": "cancelOrder",
        "outputs": [],
        "type": "function",
    },
]


# =============================================================================
# WEB3 HELPERS
# =============================================================================


def get_web3():
    """Initialize web3 connection to Polygon."""
    try:
        from web3 import Web3
    except ImportError:
        log.error("web3 not installed. Run: uv add web3")
        return None

    try:
        w3 = Web3(Web3.HTTPProvider(CHAINSTACK_NODE, request_kwargs={"timeout": 10}))
        if w3.is_connected():
            log.info("Connected to Polygon via Chainstack")
            return w3
        else:
            log.error("Failed to connect to Chainstack node")
            return None
    except Exception as e:
        log.error(f"Failed to connect to Chainstack node: {e}")
        return None


def get_contract(w3, address: str, abi: list):
    """Get contract instance."""
    from web3 import Web3

    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


# =============================================================================
# READ-ONLY OPERATIONS
# =============================================================================


async def demo_blockchain_reads():
    """Demonstrate reading blockchain state."""
    log.info("=" * 60)
    log.info("DEMO: Direct Blockchain Reads")
    log.info("=" * 60)

    w3 = get_web3()
    if not w3:
        return

    # 1. Get latest block
    block = w3.eth.block_number
    log.info(f"Latest Polygon block: {block}")

    # 2. Check contract code exists
    for name, address in CONTRACTS.items():
        code = w3.eth.get_code(address)
        log.info(f"{name}: {address[:10]}...{address[-6:]} (code: {len(code)} bytes)")

    # 3. Read USDC contract info
    usdc = get_contract(w3, CONTRACTS["USDC"], ERC20_ABI)
    decimals = usdc.functions.decimals().call()
    log.info(f"\nUSDC decimals: {decimals}")

    # 4. If we have an address, check balances
    test_address = os.environ.get("POLYMARKET_FUNDER")
    if test_address:
        from web3 import Web3

        test_address = Web3.to_checksum_address(test_address)

        # USDC balance
        usdc_balance = usdc.functions.balanceOf(test_address).call()
        log.info(f"\nAddress: {test_address}")
        log.info(f"USDC Balance: {usdc_balance / 10**decimals:.2f} USDC")

        # POL balance (native token for gas)
        pol_balance = w3.eth.get_balance(test_address)
        log.info(f"POL Balance: {w3.from_wei(pol_balance, 'ether'):.4f} POL")

        # Check allowances
        ctf_exchange = CONTRACTS["CTF_EXCHANGE"]
        allowance = usdc.functions.allowance(test_address, ctf_exchange).call()
        log.info(f"USDC Allowance for CTF Exchange: {allowance / 10**decimals:.2f}")


async def fetch_recent_trades():
    """Fetch recent OrderFilled events from CTF Exchange."""
    log.info("=" * 60)
    log.info("DEMO: Fetching Recent On-Chain Trades")
    log.info("=" * 60)

    w3 = get_web3()
    if not w3:
        return

    exchange = get_contract(w3, CONTRACTS["CTF_EXCHANGE"], CTF_EXCHANGE_ABI)

    # Get recent blocks (Polygon ~2s blocks, so 1000 blocks ~ 33 minutes)
    latest = w3.eth.block_number
    from_block = latest - 1000

    log.info(f"Scanning blocks {from_block} to {latest} for OrderFilled events...")

    try:
        # Get OrderFilled events
        events = exchange.events.OrderFilled.get_logs(
            from_block=from_block, to_block=latest
        )

        log.info(f"Found {len(events)} trades in last ~33 minutes")

        # Show last 5 trades
        for event in events[-5:]:
            log.info(f"\n  Block: {event['blockNumber']}")
            log.info(f"  Maker: {event['args']['maker'][:10]}...")
            log.info(f"  Taker: {event['args']['taker'][:10]}...")
            log.info(f"  Amount: {event['args']['makerAmountFilled']}")

    except Exception as e:
        log.error(f"Failed to fetch events: {e}")
        log.info("Note: Public RPCs may rate-limit event queries")


# =============================================================================
# POSITION TRACKING
# =============================================================================


async def check_token_positions(token_ids: list[str]):
    """Check positions for specific CTF token IDs."""
    log.info("=" * 60)
    log.info("DEMO: Checking Token Positions")
    log.info("=" * 60)

    w3 = get_web3()
    if not w3:
        return

    address = os.environ.get("POLYMARKET_FUNDER")
    if not address:
        log.warning("Set POLYMARKET_FUNDER to check positions")
        return

    from web3 import Web3

    address = Web3.to_checksum_address(address)
    ctf = get_contract(w3, CONTRACTS["CTF"], ERC1155_ABI)

    for token_id in token_ids:
        try:
            # Token ID is a large number, convert from hex or decimal
            if token_id.startswith("0x"):
                token_int = int(token_id, 16)
            else:
                token_int = int(token_id)

            balance = ctf.functions.balanceOf(address, token_int).call()

            if balance > 0:
                log.info(f"Token {token_id[:20]}...: {balance / 10**6:.2f} shares")
            else:
                log.info(f"Token {token_id[:20]}...: No position")

        except Exception as e:
            log.error(f"Error checking token {token_id[:20]}...: {e}")


# =============================================================================
# TOKEN ALLOWANCE MANAGEMENT
# =============================================================================


def explain_direct_allowances():
    """Explain how to set allowances via direct blockchain calls."""
    log.info("=" * 60)
    log.info("SETTING ALLOWANCES VIA BLOCKCHAIN")
    log.info("=" * 60)
    log.info(
        """
Direct allowance setting requires:
1. POL for gas fees
2. Private key access
3. Building and signing transactions

Example Code:

```python
import os
from web3 import Web3

# Connect (requires CHAINSTACK_NODE env var)
w3 = Web3(Web3.HTTPProvider(os.environ["CHAINSTACK_NODE"]))

# Your account
private_key = "0x..."
account = w3.eth.account.from_key(private_key)

# USDC contract
usdc = w3.eth.contract(
    address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    abi=ERC20_ABI
)

# Exchange to approve
ctf_exchange = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Build approve transaction
tx = usdc.functions.approve(
    ctf_exchange,
    2**256 - 1  # Max approval (or specific amount)
).build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 100000,
    'gasPrice': w3.eth.gas_price,
    'chainId': 137
})

# Sign and send
signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
print(f"TX: {tx_hash.hex()}")

# Wait for confirmation
receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
print(f"Status: {'Success' if receipt['status'] == 1 else 'Failed'}")
```

For CTF tokens (ERC1155), use setApprovalForAll:

```python
ctf = w3.eth.contract(
    address="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    abi=ERC1155_ABI
)

tx = ctf.functions.setApprovalForAll(
    ctf_exchange,
    True
).build_transaction({...})
```
"""
    )


# =============================================================================
# ORDER CREATION (EIP-712)
# =============================================================================


def explain_order_signing():
    """Explain how orders are structured and signed."""
    log.info("=" * 60)
    log.info("ORDER STRUCTURE AND SIGNING (EIP-712)")
    log.info("=" * 60)
    log.info(
        """
Polymarket orders use EIP-712 typed data signing for security.

ORDER STRUCTURE:
```python
order = {
    "salt": 12345,              # Random nonce for uniqueness
    "maker": "0x...",           # Your address
    "signer": "0x...",          # Key that signs (may differ for proxy)
    "taker": "0x0...",          # 0 = anyone can fill
    "tokenId": 123...,          # CTF token ID (large uint256)
    "makerAmount": 1000000,     # What you give (6 decimals)
    "takerAmount": 2000000,     # What you receive
    "expiration": 1735689600,   # Unix timestamp
    "nonce": 0,                 # Incrementing nonce
    "feeRateBps": 0,            # Fee in basis points
    "side": 0,                  # 0=BUY, 1=SELL
    "signatureType": 0,         # 0=EOA, 1=PROXY, 2=SAFE
}
```

DOMAIN (EIP-712):
```python
domain = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": 137,
    "verifyingContract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
}
```

SIGNING PROCESS:
1. Hash the order struct per EIP-712
2. Sign with private key
3. Signature format: r + s + v (65 bytes)

WHY USE PY-CLOB-CLIENT INSTEAD:
- Handles all the EIP-712 complexity
- Manages nonces automatically
- Proper error handling
- Tested and maintained

Direct signing is only needed for:
- Custom execution strategies
- Research and understanding
- Fallback if API is down
"""
    )


# =============================================================================
# MONITORING TRADES
# =============================================================================


async def monitor_market_activity(token_id: str, duration_seconds: int = 60):
    """Monitor live trading activity for a specific token."""
    log.info("=" * 60)
    log.info(f"MONITORING: Token activity for {duration_seconds}s")
    log.info("=" * 60)

    w3 = get_web3()
    if not w3:
        return

    log.info(f"Token: {token_id[:30]}...")
    log.info("Watching for OrderFilled events...")

    exchange = get_contract(w3, CONTRACTS["CTF_EXCHANGE"], CTF_EXCHANGE_ABI)
    start_block = w3.eth.block_number

    # Poll for new events
    import time

    start_time = time.time()
    last_block = start_block
    trades_seen = 0

    while time.time() - start_time < duration_seconds:
        current_block = w3.eth.block_number

        if current_block > last_block:
            try:
                events = exchange.events.OrderFilled.get_logs(
                    from_block=last_block + 1, to_block=current_block
                )

                for event in events:
                    # Check if this trade involves our token
                    maker_asset = str(event["args"]["makerAssetId"])
                    taker_asset = str(event["args"]["takerAssetId"])

                    if token_id in [maker_asset, taker_asset]:
                        trades_seen += 1
                        log.info(
                            f"\n  Trade #{trades_seen} at block {event['blockNumber']}"
                        )
                        log.info(
                            f"  Amount: {event['args']['makerAmountFilled'] / 10**6:.2f}"
                        )

                last_block = current_block

            except Exception as e:
                log.warning(f"Error fetching events: {e}")

        await asyncio.sleep(2)  # Polygon ~2s blocks

    log.info(f"\nMonitoring complete. Saw {trades_seen} trades for this token.")


# =============================================================================
# PORTFOLIO TOKEN ANALYSIS
# =============================================================================


async def analyze_portfolio_tokens():
    """Load portfolios and analyze their on-chain token IDs."""
    log.info("=" * 60)
    log.info("PORTFOLIO TOKEN ANALYSIS")
    log.info("=" * 60)

    # Load portfolios
    if not PORTFOLIOS_PATH.exists():
        log.warning(f"No portfolios found at {PORTFOLIOS_PATH}")
        return

    data = json.loads(PORTFOLIOS_PATH.read_text())
    portfolios = data.get("portfolios", []) if isinstance(data, dict) else data

    if not portfolios:
        log.warning("No portfolios in file")
        return

    log.info(f"Loaded {len(portfolios)} portfolios")

    # Fetch market details to get token IDs
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, portfolio in enumerate(portfolios[:3]):  # First 3
            log.info(f"\nPortfolio {i + 1}:")
            log.info(f"  Target: {portfolio.get('target_title', 'N/A')[:50]}...")
            log.info(f"  Cover:  {portfolio.get('cover_title', 'N/A')[:50]}...")

            # Fetch target market
            target_id = portfolio.get("target_market_id")
            if target_id:
                try:
                    resp = await client.get(
                        f"https://gamma-api.polymarket.com/markets/{target_id}"
                    )
                    if resp.status_code == 200:
                        market = resp.json()
                        tokens = market.get("clobTokenIds", [])
                        if tokens:
                            log.info(f"  Target YES Token: {tokens[0][:40]}...")
                            log.info(
                                f"  Target NO Token:  {tokens[1][:40] if len(tokens) > 1 else 'N/A'}"
                            )
                except Exception as e:
                    log.warning(f"  Failed to fetch target market: {e}")

            # Fetch cover market
            cover_id = portfolio.get("cover_market_id")
            if cover_id:
                try:
                    resp = await client.get(
                        f"https://gamma-api.polymarket.com/markets/{cover_id}"
                    )
                    if resp.status_code == 200:
                        market = resp.json()
                        tokens = market.get("clobTokenIds", [])
                        if tokens:
                            log.info(f"  Cover YES Token:  {tokens[0][:40]}...")
                except Exception as e:
                    log.warning(f"  Failed to fetch cover market: {e}")


# =============================================================================
# COMPARISON: API vs BLOCKCHAIN
# =============================================================================


def compare_approaches():
    """Compare API and blockchain approaches."""
    log.info("=" * 60)
    log.info("COMPARISON: CLOB API vs DIRECT BLOCKCHAIN")
    log.info("=" * 60)
    log.info(
        """
+------------------+----------------------+------------------------+
| Aspect           | CLOB API (py-clob)   | Direct Blockchain      |
+------------------+----------------------+------------------------+
| Complexity       | Low                  | High                   |
| Setup            | pip install + keys   | web3 + RPC + gas       |
| Order Signing    | Automatic            | Manual EIP-712         |
| Matching         | Off-chain (fast)     | N/A (use API)          |
| Settlement       | Automatic            | Already settled        |
| Gas Costs        | None (operator pays) | You pay for txs        |
| Speed            | ~100ms               | ~2-4s (blocks)         |
| Reliability      | Depends on operator  | Blockchain uptime      |
| Use Case         | Normal trading       | Advanced/research      |
+------------------+----------------------+------------------------+

RECOMMENDED FLOW:
1. Use py-clob-client for all trading
2. Use direct blockchain for:
   - Reading positions/balances
   - Setting allowances
   - Emergency order cancellation
   - Research and monitoring

HYBRID APPROACH:
- Submit orders via CLOB API (faster, no gas)
- Monitor settlement on-chain (verification)
- Cancel on-chain if CLOB is unresponsive
"""
    )


# =============================================================================
# MAIN
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Direct Blockchain Trading")
    parser.add_argument("--reads", action="store_true", help="Demo blockchain reads")
    parser.add_argument(
        "--trades", action="store_true", help="Fetch recent on-chain trades"
    )
    parser.add_argument(
        "--positions", action="store_true", help="Check token positions"
    )
    parser.add_argument(
        "--allowances", action="store_true", help="Explain allowance setting"
    )
    parser.add_argument("--signing", action="store_true", help="Explain order signing")
    parser.add_argument(
        "--compare", action="store_true", help="Compare API vs blockchain"
    )
    parser.add_argument(
        "--portfolios", action="store_true", help="Analyze portfolio tokens"
    )
    parser.add_argument("--monitor", type=str, help="Monitor trades for token ID")
    args = parser.parse_args()

    # Default: show comparison and basic reads
    if not any(vars(args).values()):
        compare_approaches()
        await demo_blockchain_reads()
        return

    if args.compare:
        compare_approaches()

    if args.reads:
        await demo_blockchain_reads()

    if args.trades:
        await fetch_recent_trades()

    if args.positions:
        # Would need token IDs - load from portfolios
        await analyze_portfolio_tokens()

    if args.allowances:
        explain_direct_allowances()

    if args.signing:
        explain_order_signing()

    if args.portfolios:
        await analyze_portfolio_tokens()

    if args.monitor:
        await monitor_market_activity(args.monitor)


if __name__ == "__main__":
    asyncio.run(main())
