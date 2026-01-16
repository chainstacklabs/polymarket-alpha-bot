"""
CLOB WebSocket client for real-time price streaming from Polymarket.

Connects to the Polymarket CLOB WebSocket API and pushes price events
to an async queue for processing by the aggregation service.
"""

import asyncio
import json
from datetime import datetime, timezone

import websockets
from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL_SECONDS = 10
RECONNECT_BASE_SECONDS = 2
RECONNECT_MAX_SECONDS = 60
MAX_TOKENS_PER_CONNECTION = 500


# =============================================================================
# CLOB WEBSOCKET CLIENT
# =============================================================================


class ClobWebSocketClient:
    """
    WebSocket client for Polymarket CLOB price streaming.

    Handles:
    - Connection with auto-reconnection (exponential backoff)
    - Token ID subscription
    - PING/PONG keepalive
    - Event parsing and queue dispatch
    """

    def __init__(self, price_queue: asyncio.Queue) -> None:
        """
        Initialize the WebSocket client.

        Args:
            price_queue: Queue to push price events to.
        """
        self._price_queue = price_queue
        self._token_ids: list[str] = []
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._connect_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._reconnect_attempts = 0
        self._resubscribe_event: asyncio.Event | None = None

    async def start(self, token_ids: list[str]) -> None:
        """
        Start the WebSocket connection.

        Args:
            token_ids: List of CLOB token IDs to subscribe to.
        """
        if self._running:
            return

        self._token_ids = token_ids[:MAX_TOKENS_PER_CONNECTION]
        if len(token_ids) > MAX_TOKENS_PER_CONNECTION:
            logger.warning(
                f"Token count ({len(token_ids)}) exceeds limit "
                f"({MAX_TOKENS_PER_CONNECTION}), truncating"
            )

        logger.info(f"Starting ClobWebSocketClient with {len(self._token_ids)} tokens")
        self._running = True
        self._resubscribe_event = asyncio.Event()
        self._connect_task = asyncio.create_task(self._connect_loop())

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        logger.info("Stopping ClobWebSocketClient")
        self._running = False

        # Cancel tasks
        for task in [self._connect_task, self._ping_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        logger.info("ClobWebSocketClient stopped")

    async def resubscribe(self, token_ids: list[str]) -> None:
        """
        Update subscription with new token IDs.

        Closes current connection and reconnects with new tokens.
        """
        self._token_ids = token_ids[:MAX_TOKENS_PER_CONNECTION]
        logger.info(f"Resubscribing to {len(self._token_ids)} tokens")

        # Wake up connect loop if it's waiting for tokens
        if self._resubscribe_event:
            self._resubscribe_event.set()

        # Close current connection to trigger reconnect
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _connect_loop(self) -> None:
        """Main connection loop with auto-reconnection."""
        while self._running:
            if not self._token_ids:
                logger.warning("No tokens to subscribe to, waiting...")
                # Wait for resubscribe event or timeout after 5s
                if self._resubscribe_event:
                    self._resubscribe_event.clear()
                    try:
                        await asyncio.wait_for(
                            self._resubscribe_event.wait(), timeout=5.0
                        )
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(5)
                continue

            try:
                logger.info(f"Connecting to {CLOB_WS_URL}")

                async with websockets.connect(
                    CLOB_WS_URL,
                    ping_interval=None,  # We handle pings manually
                    ping_timeout=None,
                ) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0

                    # Send subscription
                    subscription = {
                        "assets_ids": self._token_ids,
                        "type": "market",
                    }
                    await ws.send(json.dumps(subscription))
                    logger.info(
                        f"WebSocket connected, subscribed to {len(self._token_ids)} tokens"
                    )

                    # Start ping task
                    self._ping_task = asyncio.create_task(self._ping_loop())

                    try:
                        # Listen for messages
                        await self._message_loop()
                    finally:
                        if self._ping_task and not self._ping_task.done():
                            self._ping_task.cancel()

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            # Reconnect with exponential backoff
            if self._running:
                backoff = min(
                    RECONNECT_BASE_SECONDS * (2**self._reconnect_attempts),
                    RECONNECT_MAX_SECONDS,
                )
                self._reconnect_attempts += 1
                logger.info(
                    f"Reconnecting in {backoff}s (attempt {self._reconnect_attempts})..."
                )
                await asyncio.sleep(backoff)

        logger.info("WebSocket connect loop stopped")

    async def _ping_loop(self) -> None:
        """Send PING keepalive messages."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(PING_INTERVAL_SECONDS)
                if self._ws and self._running:
                    await self._ws.send("PING")
                    logger.debug("PING sent")
            except Exception as e:
                logger.warning(f"Ping failed: {e}")
                break

    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self._ws:
            if message == "PONG":
                logger.debug("PONG received")
                continue

            try:
                data = json.loads(message)

                # Handle array of events (initial book snapshots)
                if isinstance(data, list):
                    for item in data:
                        await self._process_event(item)
                else:
                    await self._process_event(data)

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON message: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _process_event(self, data: dict) -> None:
        """Dispatch event to specific handler based on event_type."""
        event_type = data.get("event_type")

        if event_type == "price_change":
            await self._handle_price_change(data)
        elif event_type == "book":
            # Initial book snapshot - extract prices
            await self._handle_book(data)
        elif event_type == "last_trade_price":
            # Trade events - skip per user preference
            pass
        elif event_type == "tick_size_change":
            logger.debug(f"Tick size change: {data}")
        else:
            logger.debug(f"Unknown event type: {event_type}")

    async def _handle_price_change(self, data: dict) -> None:
        """
        Handle price_change events and push to queue.

        Event format:
        {
            "event_type": "price_change",
            "timestamp": 1234567890,
            "price_changes": [
                {"asset_id": "token_id", "best_bid": "0.52", "best_ask": "0.53"}
            ]
        }
        """
        receive_time = datetime.now(timezone.utc)

        for change in data.get("price_changes", []):
            token_id = change.get("asset_id")
            if not token_id:
                continue

            best_bid = change.get("best_bid")
            best_ask = change.get("best_ask")

            if not best_bid and not best_ask:
                continue

            try:
                self._price_queue.put_nowait(
                    {
                        "type": "price_change",
                        "token_id": token_id,
                        "bid": float(best_bid) if best_bid else None,
                        "ask": float(best_ask) if best_ask else None,
                        "timestamp": receive_time,
                    }
                )
            except asyncio.QueueFull:
                logger.warning(
                    f"Price queue full, dropping price_change for {token_id}"
                )

    async def _handle_book(self, data: dict) -> None:
        """
        Handle order book snapshots and extract best bid/ask.

        Event format:
        {
            "event_type": "book",
            "asset_id": "token_id",
            "bids": [[price, size], ...],
            "asks": [[price, size], ...]
        }
        """
        receive_time = datetime.now(timezone.utc)
        token_id = data.get("asset_id")
        if not token_id:
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        def extract_best_price(orders: list) -> float | None:
            if not orders:
                return None
            first = orders[0]
            if isinstance(first, list):
                return float(first[0]) if first else None
            elif isinstance(first, dict):
                return float(first.get("price", 0))
            return float(first) if first else None

        best_bid = extract_best_price(bids)
        best_ask = extract_best_price(asks)

        if best_bid is not None or best_ask is not None:
            try:
                self._price_queue.put_nowait(
                    {
                        "type": "book",
                        "token_id": token_id,
                        "bid": best_bid,
                        "ask": best_ask,
                        "timestamp": receive_time,
                    }
                )
            except asyncio.QueueFull:
                logger.warning(
                    f"Price queue full, dropping book snapshot for {token_id}"
                )
