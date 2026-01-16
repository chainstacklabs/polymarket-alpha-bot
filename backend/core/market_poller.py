"""
Background market polling service for incremental processing.

Polls Polymarket API for new events/markets and triggers incremental
processing when new ones are detected. Integrates with FastAPI lifespan
for start/stop lifecycle.

Polling Strategy:
    - Polls /events endpoint every POLL_INTERVAL_SECONDS (default: 60s)
    - Filters by tag (default: "politics")
    - Tracks seen event IDs to detect new ones
    - Triggers process_new_event() for each new event immediately

Configuration (via environment variables):
    POLL_INTERVAL_SECONDS: Seconds between polls (default: 60)
    POLYMARKET_TAG: Tag to filter events by (default: "politics")
    MARKET_POLLING_ENABLED: Enable/disable polling (default: "true")

Note: This service is independent of the batch pipeline and can run
simultaneously. State access is coordinated via SQLite transactions.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from core.incremental import process_new_event
from core.paths import GAMMA_API_BASE_URL
from core.state import load_state

# =============================================================================
# CONFIGURATION
# =============================================================================

# Polling configuration
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
TAG_SLUG = os.getenv("POLYMARKET_TAG", "politics")
ENABLED = os.getenv("MARKET_POLLING_ENABLED", "true").lower() == "true"

# API settings
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3

# Rate limiting
MIN_POLL_INTERVAL = 30  # Never poll faster than this
MAX_CONSECUTIVE_ERRORS = 5  # Pause polling after this many errors


# =============================================================================
# API HELPERS
# =============================================================================


async def fetch_json(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Fetch JSON from API with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            if attempt == MAX_RETRIES:
                raise
            status = getattr(e, "response", None)
            delay = attempt * (2 if status and status.status_code == 429 else 1)
            logger.warning(f"Retry {attempt}/{MAX_RETRIES} for {endpoint}: {e}")
            await asyncio.sleep(delay)
    return None


def parse_json_field(value: Any) -> Any:
    """Parse JSON string field if needed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def is_active(item: dict[str, Any]) -> bool:
    """Check if event/market is active and not closed."""
    return item.get("active") is True and item.get("closed") is not True


def process_event_markets(event: dict[str, Any]) -> dict[str, Any]:
    """Process an event's markets (parse JSON fields)."""
    json_fields = ["clobTokenIds", "outcomes", "outcomePrices"]
    markets = event.get("markets", [])

    processed_markets = []
    for m in filter(is_active, markets):
        processed = {**m}
        for field in json_fields:
            if field in processed:
                processed[field] = parse_json_field(processed[field])
        processed_markets.append(processed)

    return {**event, "markets": processed_markets}


# =============================================================================
# MARKET POLLING SERVICE
# =============================================================================


class MarketPollingService:
    """
    Background service that polls for new markets.

    Lifecycle:
        - start(): Begin background polling task
        - stop(): Gracefully stop polling

    Detection:
        - Maintains set of seen event IDs
        - New event = event ID not in seen set
        - On startup, loads existing event IDs from state

    Processing:
        - Calls process_new_event() for each new event
        - Processes immediately (not batched)
        - Errors are logged but don't stop polling
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._seen_event_ids: set[str] = set()
        self._tag_id: str | None = None
        self._consecutive_errors = 0
        self._stats = {
            "polls_completed": 0,
            "new_events_found": 0,
            "processing_errors": 0,
            "start_time": None,
        }

    async def start(self) -> None:
        """Start the background polling task."""
        if not ENABLED:
            logger.info("Market polling disabled (MARKET_POLLING_ENABLED=false)")
            return

        if self._task is not None:
            logger.warning("Market polling already running")
            return

        logger.info(
            f"Starting market polling service "
            f"(interval={POLL_INTERVAL_SECONDS}s, tag={TAG_SLUG})"
        )

        # Load existing event IDs from state to avoid reprocessing
        await self._load_existing_event_ids()

        self._stats["start_time"] = datetime.now(timezone.utc).isoformat()
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background polling task."""
        if self._task is None:
            return

        logger.info("Stopping market polling service...")
        self._shutdown_event.set()

        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Polling task did not stop gracefully, cancelling...")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._task = None
        self._log_stats()

    async def _load_existing_event_ids(self) -> None:
        """Load existing group IDs from state to avoid reprocessing."""
        try:
            state = load_state()
            existing = state.get_processed_group_ids()
            self._seen_event_ids = existing
            state.close()
            logger.info(f"Loaded {len(existing)} existing event IDs from state")
        except Exception as e:
            logger.error(f"Failed to load existing event IDs: {e}")
            self._seen_event_ids = set()

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        async with httpx.AsyncClient(
            base_url=GAMMA_API_BASE_URL,
            timeout=REQUEST_TIMEOUT,
        ) as client:
            # Resolve tag ID once
            if TAG_SLUG:
                try:
                    tag = await fetch_json(client, f"/tags/slug/{TAG_SLUG}")
                    if tag:
                        self._tag_id = str(tag.get("id"))
                        logger.info(f"Resolved tag '{TAG_SLUG}' -> id={self._tag_id}")
                    else:
                        logger.warning(
                            f"Tag '{TAG_SLUG}' not found, polling all events"
                        )
                except Exception as e:
                    logger.error(f"Failed to resolve tag: {e}")

            while not self._shutdown_event.is_set():
                try:
                    await self._poll_once(client)
                    self._consecutive_errors = 0

                except Exception as e:
                    self._consecutive_errors += 1
                    logger.error(f"Polling error ({self._consecutive_errors}): {e}")

                    if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.error(
                            f"Too many consecutive errors ({MAX_CONSECUTIVE_ERRORS}), "
                            "pausing for extended interval"
                        )
                        await self._wait_for_interval(POLL_INTERVAL_SECONDS * 5)
                        self._consecutive_errors = 0
                        continue

                # Wait for next poll interval
                await self._wait_for_interval(POLL_INTERVAL_SECONDS)

    async def _wait_for_interval(self, seconds: int) -> None:
        """Wait for interval or until shutdown."""
        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=max(seconds, MIN_POLL_INTERVAL),
            )
        except asyncio.TimeoutError:
            pass  # Normal timeout, continue polling

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        """Execute a single poll cycle."""
        # Build params
        params: dict[str, Any] = {
            "limit": PAGE_SIZE,
            "offset": 0,
            "active": "true",
            "closed": "false",
        }
        if self._tag_id:
            params["tag_id"] = self._tag_id

        # Fetch events
        events_raw = await fetch_json(client, "/events", params)
        if not events_raw:
            return

        self._stats["polls_completed"] += 1

        # Filter to active events and detect new ones
        # Note: We DON'T mark as seen until successful processing
        candidate_events = []
        for event in events_raw:
            if not is_active(event):
                continue

            event_id = str(event.get("id", ""))
            if event_id and event_id not in self._seen_event_ids:
                processed = process_event_markets(event)
                # Only consider events with active markets
                if processed.get("markets"):
                    candidate_events.append((event_id, processed))

        if not candidate_events:
            logger.debug(
                f"Poll complete: 0 new events (seen {len(self._seen_event_ids)})"
            )
            return

        # Log new events
        logger.info(f"Detected {len(candidate_events)} new event(s)")
        for _, event in candidate_events[:3]:
            logger.info(f"  - {event.get('title', 'Unknown')[:60]}...")
        if len(candidate_events) > 3:
            logger.info(f"  ... and {len(candidate_events) - 3} more")

        # Process each new event immediately
        # Only mark as seen after successful processing
        for event_id, event in candidate_events:
            success = await self._process_event(event)
            if success:
                self._seen_event_ids.add(event_id)
                self._stats["new_events_found"] += 1

        # Memory leak prevention: cap the seen set size
        # Keep synced with database state periodically
        if len(self._seen_event_ids) > 10000:
            logger.info("Pruning seen_event_ids set to prevent memory growth")
            await self._load_existing_event_ids()

    async def _process_event(self, event: dict) -> bool:
        """
        Process a single new event.

        Returns:
            True if processing succeeded (event should be marked as seen)
            False if processing failed or was skipped
        """
        event_id = event.get("id", "unknown")
        event_title = event.get("title", "Unknown")[:50]

        try:
            logger.info(f"Processing new event: {event_title}...")
            result = await process_new_event(event)

            if result.get("skipped"):
                logger.info(f"Event {event_id} skipped: {result.get('reason')}")
                # Don't mark as seen - might have markets later
                return False
            else:
                logger.info(
                    f"Event {event_id} processed: "
                    f"case={result.get('case')}, "
                    f"pairs={result.get('validated_pairs', 0)}, "
                    f"portfolios={result.get('portfolios', 0)}"
                )
                return True

        except Exception as e:
            self._stats["processing_errors"] += 1
            logger.error(f"Failed to process event {event_id}: {e}")
            return False

    def _log_stats(self) -> None:
        """Log polling statistics."""
        logger.info("=" * 60)
        logger.info("MARKET POLLING STATISTICS")
        logger.info(f"  Started: {self._stats['start_time']}")
        logger.info(f"  Polls completed: {self._stats['polls_completed']}")
        logger.info(f"  New events found: {self._stats['new_events_found']}")
        logger.info(f"  Processing errors: {self._stats['processing_errors']}")
        logger.info(f"  Total events tracked: {len(self._seen_event_ids)}")
        logger.info("=" * 60)

    def get_stats(self) -> dict:
        """Get current statistics."""
        return {
            **self._stats,
            "seen_event_count": len(self._seen_event_ids),
            "is_running": self._task is not None and not self._task.done(),
            "consecutive_errors": self._consecutive_errors,
        }


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

# Global singleton for FastAPI lifespan integration
market_poller = MarketPollingService()
