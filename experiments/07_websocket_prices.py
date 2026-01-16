"""
07: Test Polymarket CLOB WebSocket for Real-Time Price Streaming.

WHAT IT DOES
    Connects to Polymarket's CLOB WebSocket API to receive real-time price
    updates instead of polling the REST API every 10 seconds.

WHY WE NEED THIS
    Current price_cache.py polls Gamma API every 10 seconds. WebSocket provides
    near-instant price updates when market prices change, enabling:
    - Faster alpha detection (prices update within ~100ms vs 10s)
    - Lower API load (one persistent connection vs repeated requests)
    - Real-time portfolio value tracking

HOW IT WORKS
    1. Read portfolios.json to get market IDs we care about
    2. Fetch clobTokenIds from Gamma API for each market
    3. Connect to wss://ws-subscriptions-clob.polymarket.com/ws/market
    4. Subscribe to token IDs and listen for price_change events
    5. Log price updates with latency measurements

CLOB WEBSOCKET API
    Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
    Subscribe: {"assets_ids": ["token_id1", "token_id2"], "type": "market"}
    Events:
        - book: Full order book snapshot (bids/asks)
        - price_change: Incremental best bid/ask updates
        - last_trade_price: Trade executions
    Keepalive: Send "PING" every 10 seconds

USAGE
    uv run python experiments/07_websocket_prices.py
    uv run python experiments/07_websocket_prices.py --duration 60  # Run for 60s
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"
PORTFOLIOS_PATH = LIVE_DIR / "portfolios.json"

# WebSocket settings
PING_INTERVAL_SECONDS = 10
MAX_TOKENS_PER_CONNECTION = 500  # Polymarket limit
REQUEST_TIMEOUT = 10.0

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# DATA LOADING
# =============================================================================


def load_market_ids_from_portfolios() -> list[str]:
    """Load unique market IDs from portfolios.json."""
    if not PORTFOLIOS_PATH.exists():
        log.warning(f"Portfolios file not found: {PORTFOLIOS_PATH}")
        return []

    data = json.loads(PORTFOLIOS_PATH.read_text())
    portfolios = data.get("portfolios", []) if isinstance(data, dict) else data

    market_ids = set()
    for p in portfolios:
        if target_id := p.get("target_market_id"):
            market_ids.add(str(target_id))
        if cover_id := p.get("cover_market_id"):
            market_ids.add(str(cover_id))

    log.info(f"Loaded {len(market_ids)} unique market IDs from portfolios")
    return list(market_ids)


async def fetch_token_ids_for_markets(market_ids: list[str]) -> dict[str, dict]:
    """
    Fetch clobTokenIds from Gamma API for each market.

    Returns: {token_id: {"market_id": str, "question": str, "side": "YES"|"NO"}}
    """
    token_map: dict[str, dict] = {}

    # Group markets by event to batch requests
    # For now, fetch each market individually (can optimize later)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for market_id in market_ids:
            try:
                resp = await client.get(f"{GAMMA_API_BASE_URL}/markets/{market_id}")
                if resp.status_code != 200:
                    log.warning(
                        f"Failed to fetch market {market_id}: {resp.status_code}"
                    )
                    continue

                market = resp.json()
                clob_token_ids = market.get("clobTokenIds", "[]")

                # Parse JSON string if needed
                if isinstance(clob_token_ids, str):
                    clob_token_ids = json.loads(clob_token_ids)

                question = market.get("question", "Unknown")
                outcomes = market.get("outcomes", ["Yes", "No"])
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)

                # Map each token ID to its metadata
                for i, token_id in enumerate(clob_token_ids):
                    side = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
                    token_map[token_id] = {
                        "market_id": market_id,
                        "question": question,
                        "side": side,
                    }

            except Exception as e:
                log.error(f"Error fetching market {market_id}: {e}")

    log.info(f"Resolved {len(token_map)} token IDs from {len(market_ids)} markets")
    return token_map


# =============================================================================
# WEBSOCKET CLIENT
# =============================================================================


class PolymarketPriceStream:
    """WebSocket client for streaming Polymarket CLOB prices."""

    def __init__(self, token_map: dict[str, dict]):
        self.token_map = token_map
        self.token_ids = list(token_map.keys())
        self.ws = None
        self._running = False
        self._ping_task = None

        # Stats
        self.events_received = 0
        self.price_changes_received = 0
        self.last_prices: dict[str, dict] = {}  # token_id -> {bid, ask, time}

    async def connect(self) -> None:
        """Connect to WebSocket and subscribe to tokens."""
        if len(self.token_ids) > MAX_TOKENS_PER_CONNECTION:
            log.warning(
                f"Token count ({len(self.token_ids)}) exceeds limit "
                f"({MAX_TOKENS_PER_CONNECTION}). Truncating."
            )
            self.token_ids = self.token_ids[:MAX_TOKENS_PER_CONNECTION]

        log.info(f"Connecting to {CLOB_WS_URL}")
        log.info(f"Subscribing to {len(self.token_ids)} token IDs")

        self._running = True

        try:
            async with websockets.connect(
                CLOB_WS_URL,
                ping_interval=None,  # We handle pings manually
                ping_timeout=None,
            ) as ws:
                self.ws = ws

                # Send subscription message
                subscription = {
                    "assets_ids": self.token_ids,
                    "type": "market",
                }
                await ws.send(json.dumps(subscription))
                log.info("Subscription sent")

                # Start ping task
                self._ping_task = asyncio.create_task(self._ping_loop())

                # Listen for messages
                await self._message_loop()

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"WebSocket connection closed: {e}")
        except Exception as e:
            log.error(f"WebSocket error: {e}")
        finally:
            self._running = False
            if self._ping_task:
                self._ping_task.cancel()

    async def _ping_loop(self) -> None:
        """Send PING messages to keep connection alive."""
        while self._running:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if self.ws and self._running:
                try:
                    await self.ws.send("PING")
                    log.debug("PING sent")
                except Exception as e:
                    log.warning(f"Failed to send PING: {e}")
                    break

    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self.ws:
            self.events_received += 1
            receive_time = datetime.now(timezone.utc)

            # Skip PONG responses
            if message == "PONG":
                continue

            try:
                data = json.loads(message)

                # Handle array of events (initial book snapshots come as array)
                if isinstance(data, list):
                    for item in data:
                        self._process_event(item, receive_time)
                else:
                    self._process_event(data, receive_time)

            except json.JSONDecodeError:
                log.warning(f"Failed to parse message: {message[:100]}")
            except Exception as e:
                log.error(f"Error processing message: {e}")
                log.debug(f"Message was: {message[:500]}")

    def _process_event(self, data: dict, receive_time: datetime) -> None:
        """Process a single event from the WebSocket."""
        event_type = data.get("event_type")

        if event_type == "book":
            self._handle_book(data, receive_time)
        elif event_type == "price_change":
            self._handle_price_change(data, receive_time)
        elif event_type == "last_trade_price":
            self._handle_last_trade(data, receive_time)
        elif event_type == "tick_size_change":
            log.debug(f"Tick size change: {data}")
        else:
            log.debug(f"Unknown event type: {event_type}")

    def _handle_book(self, data: dict, receive_time: datetime) -> None:
        """Handle full order book snapshot."""
        asset_id = data.get("asset_id", "unknown")
        meta = self.token_map.get(asset_id, {})

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        # Handle different formats: [["price", "size"], ...] or [{"price": x}, ...]
        def extract_price(orders: list) -> float | None:
            if not orders:
                return None
            first = orders[0]
            if isinstance(first, list):
                return float(first[0]) if first else None
            elif isinstance(first, dict):
                return float(first.get("price", 0))
            else:
                return float(first) if first else None

        best_bid = extract_price(bids)
        best_ask = extract_price(asks)

        self.last_prices[asset_id] = {
            "bid": best_bid,
            "ask": best_ask,
            "time": receive_time,
        }

        question = meta.get("question", "?")[:40]
        side = meta.get("side", "?")
        log.info(
            f"📖 BOOK | {side:3} | bid={best_bid or 'N/A':>6} ask={best_ask or 'N/A':>6} | {question}"
        )

    def _handle_price_change(self, data: dict, receive_time: datetime) -> None:
        """Handle incremental price updates."""
        self.price_changes_received += 1

        # Estimate latency from timestamp if available
        ts = data.get("timestamp")
        latency_ms = None
        if ts:
            try:
                ts_int = int(ts) if isinstance(ts, str) else ts
                event_time = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc)
                latency_ms = (receive_time - event_time).total_seconds() * 1000
            except (ValueError, TypeError):
                pass

        for change in data.get("price_changes", []):
            asset_id = change.get("asset_id", "unknown")
            meta = self.token_map.get(asset_id, {})

            best_bid = change.get("best_bid")
            best_ask = change.get("best_ask")

            # Update cache
            prev = self.last_prices.get(asset_id, {})
            self.last_prices[asset_id] = {
                "bid": float(best_bid) if best_bid else prev.get("bid"),
                "ask": float(best_ask) if best_ask else prev.get("ask"),
                "time": receive_time,
            }

            question = meta.get("question", "?")[:40]
            side = meta.get("side", "?")
            latency_str = f"{latency_ms:>5.0f}ms" if latency_ms else "  N/A"

            log.info(
                f"💱 PRICE | {side:3} | bid={best_bid or 'N/A':>6} ask={best_ask or 'N/A':>6} "
                f"| lat={latency_str} | {question}"
            )

    def _handle_last_trade(self, data: dict, receive_time: datetime) -> None:
        """Handle trade execution events."""
        asset_id = data.get("asset_id", "unknown")
        meta = self.token_map.get(asset_id, {})

        price = data.get("price")
        size = data.get("size")
        side = data.get("side", "?")

        question = meta.get("question", "?")[:40]
        outcome = meta.get("side", "?")

        log.info(
            f"🔄 TRADE | {outcome:3} | price={price:>6} size={size:>8} side={side:4} | {question}"
        )

    def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False

    def print_summary(self) -> None:
        """Print statistics summary."""
        log.info("=" * 60)
        log.info("WEBSOCKET SESSION SUMMARY")
        log.info(f"  Total events received: {self.events_received}")
        log.info(f"  Price changes: {self.price_changes_received}")
        log.info(f"  Tokens tracked: {len(self.last_prices)}")
        log.info("=" * 60)


# =============================================================================
# MAIN
# =============================================================================


async def main(duration: int | None = None) -> None:
    """
    Main entry point.

    Args:
        duration: Optional duration in seconds. None = run until interrupted.
    """
    log.info("=" * 60)
    log.info("POLYMARKET CLOB WEBSOCKET PRICE STREAMING TEST")
    log.info("=" * 60)

    # Load market IDs from portfolios
    market_ids = load_market_ids_from_portfolios()
    if not market_ids:
        # Use some example markets for testing
        log.warning("No portfolios found. Using example market IDs.")
        market_ids = ["610381", "610380"]  # Ukraine election markets

    # Fetch token IDs from Gamma API
    token_map = await fetch_token_ids_for_markets(market_ids)
    if not token_map:
        log.error("No token IDs resolved. Cannot proceed.")
        return

    # Create and run WebSocket client
    client = PolymarketPriceStream(token_map)

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler():
        log.info("\nShutdown signal received...")
        client.stop()
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run with optional timeout
    try:
        if duration:
            log.info(f"Running for {duration} seconds...")
            await asyncio.wait_for(client.connect(), timeout=duration)
        else:
            log.info("Running until interrupted (Ctrl+C to stop)...")
            await client.connect()
    except asyncio.TimeoutError:
        log.info(f"Duration limit ({duration}s) reached.")
        client.stop()
    except asyncio.CancelledError:
        pass

    client.print_summary()


if __name__ == "__main__":
    # Parse optional --duration argument
    duration = None
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv):
            if arg == "--duration" and i + 1 < len(sys.argv):
                duration = int(sys.argv[i + 1])

    asyncio.run(main(duration))
