"""
Price aggregation service for real-time Polymarket prices.

Coordinates token resolution, WebSocket connection, and price aggregation.
Provides backwards-compatible interface with the old PriceCacheService.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from server.clob_websocket import ClobWebSocketClient
from server.token_resolver import token_resolver

# =============================================================================
# CONFIGURATION
# =============================================================================

SUBSCRIPTION_REFRESH_INTERVAL = 5  # Check for portfolio changes every 5s
CALLBACK_BATCH_INTERVAL = 0.1  # Batch callbacks every 100ms
STALE_THRESHOLD_SECONDS = 30
PRICE_QUEUE_MAX_SIZE = 10000  # Prevent memory exhaustion under high load


# =============================================================================
# DATA MODELS (backwards compatible with price_cache.py)
# =============================================================================


@dataclass
class PriceData:
    """Price information for a single event."""

    price: float | None
    title: str
    market_id: str | None


@dataclass
class CacheMetadata:
    """Metadata about the price cache state."""

    last_fetch: datetime | None
    event_count: int
    is_stale: bool


# =============================================================================
# PRICE AGGREGATION SERVICE
# =============================================================================


class PriceAggregationService:
    """
    Aggregates token-level prices into market and event prices.

    Orchestrates:
    - TokenResolver for market ↔ token mapping
    - ClobWebSocketClient for real-time price streaming
    - Price aggregation from token → market → event level
    - Callback notifications for portfolio updates

    Provides backwards-compatible interface with PriceCacheService:
    - get_prices() → event-level prices
    - get_market_prices() → market-level prices
    - register_callback() / unregister_callback()
    """

    def __init__(self) -> None:
        # Price caches
        self._token_prices: dict[str, dict] = {}  # token_id → {bid, ask, timestamp}
        self._market_prices: dict[
            str, dict
        ] = {}  # market_id → {yes, no, event_id, question}

        # Metadata
        self._last_update: datetime | None = None
        self._running = False

        # Components
        self._price_queue: asyncio.Queue = asyncio.Queue(maxsize=PRICE_QUEUE_MAX_SIZE)
        self._ws_client: ClobWebSocketClient | None = None

        # Tasks
        self._event_loop_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._callback_task: asyncio.Task | None = None

        # Callbacks
        self._callbacks: list = []

        # Callback batching
        self._changed_markets: set[str] = set()
        self._callback_lock = asyncio.Lock()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the price aggregation service."""
        if self._running:
            return

        logger.info("Starting PriceAggregationService")
        self._running = True

        # Start token resolver
        await token_resolver.start()
        token_ids = token_resolver.get_token_ids()

        if not token_ids:
            logger.warning("No tokens available for subscription")

        # Initialize market cache from token metadata
        self._initialize_market_cache()

        # Start WebSocket client
        self._ws_client = ClobWebSocketClient(self._price_queue)
        await self._ws_client.start(token_ids)

        # Start processing tasks
        self._event_loop_task = asyncio.create_task(self._price_event_loop())
        self._refresh_task = asyncio.create_task(self._refresh_subscription_loop())
        self._callback_task = asyncio.create_task(self._callback_batch_loop())

        logger.info("PriceAggregationService started")

    async def stop(self) -> None:
        """Stop the price aggregation service."""
        logger.info("Stopping PriceAggregationService")
        self._running = False

        # Cancel tasks
        for task in [self._event_loop_task, self._refresh_task, self._callback_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop components
        if self._ws_client:
            await self._ws_client.stop()
        await token_resolver.stop()

        logger.info("PriceAggregationService stopped")

    # =========================================================================
    # PUBLIC API (backwards compatible with PriceCacheService)
    # =========================================================================

    def get_prices(self) -> dict[str, PriceData]:
        """
        Get event-level prices (backwards compatible).

        Returns prices derived from market cache, grouped by event.
        """
        return self._derive_event_prices()

    def get_prices_dict(self) -> dict[str, dict[str, Any]]:
        """Get event-level prices as JSON-serializable dict."""
        prices = self._derive_event_prices()
        return {
            event_id: {
                "price": data.price,
                "title": data.title,
                "market_id": data.market_id,
            }
            for event_id, data in prices.items()
        }

    def get_metadata(self) -> CacheMetadata:
        """Get metadata about the cache state."""
        now = datetime.now(timezone.utc)

        is_stale = (
            self._last_update is None
            or (now - self._last_update).total_seconds() > STALE_THRESHOLD_SECONDS
        )

        return CacheMetadata(
            last_fetch=self._last_update,
            event_count=len(self._market_prices),
            is_stale=is_stale,
        )

    def get_market_prices(self) -> dict[str, dict]:
        """
        Get market-level prices.

        Returns: {market_id: {yes, no, event_id, question}}
        """
        return self._market_prices.copy()

    def register_callback(self, callback) -> None:
        """
        Register a callback for price updates (idempotent).

        Callback signature: async def callback(market_prices: dict[str, dict]) -> None

        Note: Safe to call multiple times - duplicate registrations are ignored.
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)
            logger.debug(f"Registered callback: {callback.__name__}")
        else:
            logger.debug(f"Callback already registered: {callback.__name__}")

    def unregister_callback(self, callback) -> None:
        """Unregister a price update callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
            logger.debug(f"Unregistered callback: {callback.__name__}")

    # =========================================================================
    # INTERNAL: PRICE EVENT PROCESSING
    # =========================================================================

    def _initialize_market_cache(self) -> None:
        """Initialize market cache from token resolver metadata."""
        all_market_tokens = token_resolver.get_all_market_tokens()

        for market_id, tokens in all_market_tokens.items():
            if market_id in self._market_prices:
                continue

            # Get metadata from first token
            if tokens:
                meta = token_resolver.get_token_metadata(tokens[0])
                if meta:
                    # Initialize with None prices - real prices will come from WebSocket
                    self._market_prices[market_id] = {
                        "yes": None,
                        "no": None,
                        "event_id": meta.get("event_id", market_id),
                        "question": meta.get("question", ""),
                    }

    async def _price_event_loop(self) -> None:
        """Consume price events from queue and update caches."""
        while self._running:
            try:
                # Wait for event with timeout to allow checking _running flag
                try:
                    event = await asyncio.wait_for(self._price_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                await self._process_price_event(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in price event loop: {e}")

    async def _process_price_event(self, event: dict) -> None:
        """Process a single price event and update caches."""
        token_id = event.get("token_id")
        if not token_id:
            return

        # Update token-level cache
        self._token_prices[token_id] = {
            "bid": event.get("bid"),
            "ask": event.get("ask"),
            "timestamp": event.get("timestamp"),
        }

        # Get token metadata
        meta = token_resolver.get_token_metadata(token_id)
        if not meta:
            return

        market_id = meta["market_id"]
        self._aggregate_market_price(market_id)

        # Track for callback batching
        async with self._callback_lock:
            self._changed_markets.add(market_id)

        self._last_update = datetime.now(timezone.utc)

    def _aggregate_market_price(self, market_id: str) -> None:
        """Aggregate token prices into market YES/NO prices."""
        tokens = token_resolver.get_tokens_for_market(market_id)
        if len(tokens) < 2:
            return

        yes_token, no_token = tokens[0], tokens[1]

        # Get current prices
        yes_data = self._token_prices.get(yes_token, {})
        no_data = self._token_prices.get(no_token, {})

        def calculate_price(data: dict) -> float | None:
            """Calculate midpoint price from bid/ask. Returns None if no data."""
            bid = data.get("bid")
            ask = data.get("ask")
            if bid is not None and ask is not None:
                return round((bid + ask) / 2, 4)
            elif bid is not None:
                return round(bid, 4)
            elif ask is not None:
                return round(ask, 4)
            return None  # No price data available

        yes_price = calculate_price(yes_data)
        no_price = calculate_price(no_data)

        # Get metadata
        meta = token_resolver.get_token_metadata(yes_token) or {}

        # Get existing data to preserve non-None values
        existing = self._market_prices.get(market_id, {})

        # Update market cache (preserve existing prices if new is None)
        self._market_prices[market_id] = {
            "yes": yes_price if yes_price is not None else existing.get("yes"),
            "no": no_price if no_price is not None else existing.get("no"),
            "event_id": meta.get("event_id", market_id),
            "question": meta.get("question", ""),
        }

    # =========================================================================
    # INTERNAL: CALLBACK BATCHING
    # =========================================================================

    async def _callback_batch_loop(self) -> None:
        """Batch and trigger callbacks at regular intervals."""
        while self._running:
            try:
                await asyncio.sleep(CALLBACK_BATCH_INTERVAL)

                # Get changed markets atomically
                async with self._callback_lock:
                    if not self._changed_markets:
                        continue
                    changed = self._changed_markets.copy()
                    self._changed_markets.clear()

                # Build update dict with only changed markets
                market_updates = {
                    market_id: self._market_prices[market_id]
                    for market_id in changed
                    if market_id in self._market_prices
                }

                if market_updates:
                    await self._trigger_callbacks(market_updates)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in callback batch loop: {e}")

    async def _trigger_callbacks(self, market_prices: dict[str, dict]) -> None:
        """Trigger all registered callbacks with updated prices."""
        for callback in self._callbacks:
            try:
                await callback(market_prices)
            except Exception as e:
                logger.error(f"Callback {callback.__name__} failed: {e}")

    # =========================================================================
    # INTERNAL: SUBSCRIPTION REFRESH
    # =========================================================================

    async def _refresh_subscription_loop(self) -> None:
        """Periodically check for portfolio changes and resubscribe."""
        while self._running:
            try:
                await asyncio.sleep(SUBSCRIPTION_REFRESH_INTERVAL)

                if token_resolver.should_refresh():
                    logger.info("Portfolio changes detected, refreshing subscription")
                    await token_resolver.refresh()

                    # Initialize new markets in cache
                    self._initialize_market_cache()

                    # Resubscribe with new tokens
                    new_tokens = token_resolver.get_token_ids()
                    if self._ws_client:
                        await self._ws_client.resubscribe(new_tokens)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in refresh loop: {e}")

    # =========================================================================
    # INTERNAL: EVENT PRICE DERIVATION
    # =========================================================================

    def _derive_event_prices(self) -> dict[str, PriceData]:
        """
        Derive event-level prices from market cache.

        Groups markets by event_id, returns highest YES price market per event.
        Maintains backwards compatibility with old get_prices() interface.
        """
        event_prices: dict[str, PriceData] = {}
        events_to_markets: dict[str, list[tuple[str, float, str]]] = {}

        # Group markets by event
        for market_id, market_data in self._market_prices.items():
            event_id = market_data.get("event_id")
            if not event_id:
                continue

            yes_price = market_data.get("yes", 0.5)
            question = market_data.get("question", "")

            if event_id not in events_to_markets:
                events_to_markets[event_id] = []

            events_to_markets[event_id].append((market_id, yes_price, question))

        # For each event, pick market with highest YES price
        for event_id, markets in events_to_markets.items():
            best_market_id, best_price, question = max(markets, key=lambda x: x[1])

            event_prices[event_id] = PriceData(
                price=best_price,
                title=question,
                market_id=best_market_id,
            )

        return event_prices


# =============================================================================
# SINGLETON
# =============================================================================

price_aggregation = PriceAggregationService()
