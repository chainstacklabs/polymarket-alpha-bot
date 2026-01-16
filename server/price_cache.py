"""
Shared price cache service for Polymarket events.

Single background task fetches prices every 10 seconds.
Both WebSocket and REST endpoints read from this shared cache.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 10.0
STALE_THRESHOLD_SECONDS = 30
FETCH_INTERVAL_SECONDS = 10

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"


# =============================================================================
# DATA MODELS
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
# PRICE CACHE SERVICE
# =============================================================================


class PriceCacheService:
    """
    Singleton service for fetching and caching Polymarket prices.

    Runs a background task that fetches prices every 10 seconds.
    All consumers (WebSocket, REST) read from the shared cache.
    """

    def __init__(self, fetch_interval: int = FETCH_INTERVAL_SECONDS):
        self.fetch_interval = fetch_interval
        self._cache: dict[str, PriceData] = {}
        self._market_cache: dict[str, dict] = {}  # market_id → {yes, no, event_id}
        self._last_fetch: datetime | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._callbacks: list = []  # Price update callbacks

    def get_prices(self) -> dict[str, PriceData]:
        """Get cached prices (thread-safe read)."""
        return self._cache.copy()

    def get_prices_dict(self) -> dict[str, dict[str, Any]]:
        """Get cached prices as JSON-serializable dict."""
        return {
            event_id: {
                "price": data.price,
                "title": data.title,
                "market_id": data.market_id,
            }
            for event_id, data in self._cache.items()
        }

    def get_metadata(self) -> CacheMetadata:
        """Get metadata about the cache state."""
        now = datetime.now(timezone.utc)

        is_stale = (
            self._last_fetch is None
            or (now - self._last_fetch).total_seconds() > STALE_THRESHOLD_SECONDS
        )

        return CacheMetadata(
            last_fetch=self._last_fetch,
            event_count=len(self._cache),
            is_stale=is_stale,
        )

    def get_market_prices(self) -> dict[str, dict]:
        """Get cached prices indexed by market_id."""
        return self._market_cache.copy()

    def register_callback(self, callback) -> None:
        """Register a callback to be called when prices update.

        Callback signature: async def callback(market_prices: dict[str, dict]) -> None
        """
        self._callbacks.append(callback)

    def unregister_callback(self, callback) -> None:
        """Unregister a price update callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def start(self) -> None:
        """Start the background price fetch task."""
        if self._running:
            return

        logger.info(f"Starting PriceCacheService (interval: {self.fetch_interval}s)")
        self._running = True
        self._task = asyncio.create_task(self._fetch_loop())

    async def stop(self) -> None:
        """Stop the background price fetch task."""
        logger.info("Stopping PriceCacheService")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("PriceCacheService stopped")

    async def _fetch_loop(self) -> None:
        """Background task that fetches prices periodically."""
        while self._running:
            try:
                event_ids = self._get_active_event_ids()

                if event_ids:
                    logger.debug(f"Fetching prices for {len(event_ids)} events")
                    prices, market_prices = await self._fetch_prices_with_markets(
                        event_ids
                    )
                    self._cache = prices
                    self._market_cache = market_prices
                    self._last_fetch = datetime.now(timezone.utc)
                    logger.info(
                        f"Price cache updated: {len(prices)} events, "
                        f"{len(market_prices)} markets"
                    )

                    # Trigger callbacks with market prices
                    for callback in self._callbacks:
                        try:
                            await callback(market_prices)
                        except Exception as e:
                            logger.error(f"Price callback error: {e}")
                else:
                    logger.warning("No active event IDs found for price tracking")

            except Exception as e:
                logger.error(f"Error in price fetch loop: {e}")
                # Keep existing cache (stale data) on error

            await asyncio.sleep(self.fetch_interval)

    def _get_active_event_ids(self) -> list[str]:
        """
        Get event IDs from portfolios and opportunities.

        Reads from data/_live/portfolios.json and opportunities.json.
        """
        event_ids = set()

        # Try portfolios.json first (for portfolio price monitoring)
        portfolios_path = LIVE_DIR / "portfolios.json"
        if portfolios_path.exists():
            event_ids.update(self._extract_event_ids_from_portfolios(portfolios_path))

        # Also try opportunities.json
        live_path = LIVE_DIR / "opportunities.json"
        if live_path.exists():
            event_ids.update(self._extract_event_ids_from_file(live_path))

        if event_ids:
            return list(event_ids)

        # Fall back to historical runs
        opportunities_dir = DATA_DIR / "06_3_export_opportunities"
        if not opportunities_dir.exists():
            return []

        runs = sorted(
            [
                d
                for d in opportunities_dir.iterdir()
                if d.is_dir() and d.name[0].isdigit()
            ],
            reverse=True,
        )
        if not runs:
            return []

        opportunities_file = runs[0] / "opportunities.json"
        if not opportunities_file.exists():
            return []

        return self._extract_event_ids_from_file(opportunities_file)

    def _extract_event_ids_from_file(self, path: Path) -> list[str]:
        """Extract event IDs from an opportunities JSON file."""
        try:
            data = json.loads(path.read_text())

            # Handle both formats: flat list or nested {"opportunities": [...]}
            if isinstance(data, dict) and "opportunities" in data:
                opportunities = data["opportunities"]
            elif isinstance(data, list):
                opportunities = data
            else:
                return []

            # Extract unique event IDs from trigger and consequence
            event_ids = set()
            for opp in opportunities[:100]:  # Limit to top 100
                if isinstance(opp, dict):
                    if trigger := opp.get("trigger"):
                        if event_id := trigger.get("event_id"):
                            event_ids.add(str(event_id))
                    if consequence := opp.get("consequence"):
                        if event_id := consequence.get("event_id"):
                            event_ids.add(str(event_id))

            return list(event_ids)

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error reading opportunities from {path}: {e}")
            return []

    def _extract_event_ids_from_portfolios(self, path: Path) -> list[str]:
        """Extract event IDs from a portfolios JSON file."""
        try:
            data = json.loads(path.read_text())

            # Handle nested format
            if isinstance(data, dict) and "portfolios" in data:
                portfolios = data["portfolios"]
            elif isinstance(data, list):
                portfolios = data
            else:
                return []

            # Extract unique group IDs (which are event IDs in Polymarket)
            event_ids = set()
            for p in portfolios[:200]:  # Limit to top 200 portfolios
                if isinstance(p, dict):
                    if group_id := p.get("target_group_id"):
                        event_ids.add(str(group_id))
                    if group_id := p.get("cover_group_id"):
                        event_ids.add(str(group_id))

            return list(event_ids)

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error reading portfolios from {path}: {e}")
            return []

    async def _fetch_prices_with_markets(
        self, event_ids: list[str]
    ) -> tuple[dict[str, PriceData], dict[str, dict]]:
        """Fetch prices from Polymarket API, returning both event and market prices.

        Returns:
            Tuple of (event_prices, market_prices) where:
            - event_prices: {event_id: PriceData}
            - market_prices: {market_id: {yes: float, no: float, event_id: str}}
        """
        event_prices: dict[str, PriceData] = {}
        market_prices: dict[str, dict] = {}

        if not event_ids:
            return event_prices, market_prices

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for i in range(0, len(event_ids), 50):
                batch = event_ids[i : i + 50]

                for event_id in batch:
                    try:
                        resp = await client.get(
                            f"{GAMMA_API_BASE_URL}/events/{event_id}"
                        )

                        if resp.status_code == 200:
                            event = resp.json()
                            markets = event.get("markets", [])

                            if markets:
                                # Process ALL markets for market_prices
                                for market in markets:
                                    market_id = market.get("id")
                                    if not market_id:
                                        continue

                                    outcome_prices = market.get("outcomePrices", [])
                                    if isinstance(outcome_prices, str):
                                        outcome_prices = json.loads(outcome_prices)

                                    if outcome_prices:
                                        yes_price = float(outcome_prices[0])
                                        no_price = (
                                            float(outcome_prices[1])
                                            if len(outcome_prices) > 1
                                            else 1 - yes_price
                                        )
                                        market_prices[market_id] = {
                                            "yes": yes_price,
                                            "no": no_price,
                                            "event_id": event_id,
                                            "question": market.get("question", ""),
                                        }

                                # For event_prices, use highest YES price market
                                active_markets = [
                                    m
                                    for m in markets
                                    if m.get("acceptingOrders") and not m.get("closed")
                                ] or markets

                                def get_yes_price(m: dict) -> float:
                                    prices = m.get("outcomePrices", [])
                                    if isinstance(prices, str):
                                        prices = json.loads(prices)
                                    return float(prices[0]) if prices else 0.0

                                best_market = max(active_markets, key=get_yes_price)
                                event_prices[event_id] = PriceData(
                                    price=get_yes_price(best_market),
                                    title=event.get("title", ""),
                                    market_id=best_market.get("id"),
                                )

                    except (
                        httpx.RequestError,
                        json.JSONDecodeError,
                        IndexError,
                        KeyError,
                        ValueError,
                    ) as e:
                        logger.debug(f"Error fetching price for {event_id}: {e}")
                        continue

        return event_prices, market_prices


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

price_cache = PriceCacheService()
