"""
08: Test Strategies for Detecting New Polymarket Markets.

WHAT IT DOES
    Polls the Polymarket Gamma API to detect newly created markets in real-time.
    Tests different strategies for discovering new markets as they appear.

WHY WE NEED THIS
    Current pipeline fetches events once per run. To keep portfolios fresh and
    detect new alpha opportunities, we need continuous background polling for
    new markets. This experiment explores the best approach.

API OPTIONS INVESTIGATED
    1. Gamma REST API (the only viable option)
       - GET /markets - List all markets with filtering
       - GET /events - List all events with filtering
       - Key params: order, ascending, start_date_min, limit
       - No push notifications or webhooks available

    2. CLOB WebSocket - Only for price updates on known tokens
    3. RTDS WebSocket - Only for crypto prices and comments

STRATEGIES TESTED
    A. Poll /markets ordered by createdAt DESC (newest first)
    B. Poll /events with same approach (events contain markets)
    C. Track `new` boolean field on markets
    D. Use start_date_min filter to reduce payload size

USAGE
    uv run python experiments/08_new_market_polling.py
    uv run python experiments/08_new_market_polling.py --interval 30  # Poll every 30s
    uv run python experiments/08_new_market_polling.py --strategy events  # Use events endpoint
    uv run python experiments/08_new_market_polling.py --duration 300  # Run for 5 minutes
    uv run python experiments/08_new_market_polling.py --tag politics  # Filter by tag (default)
    uv run python experiments/08_new_market_polling.py --tag none  # No tag filter (all markets)
"""

import asyncio
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"

# Polling settings
DEFAULT_POLL_INTERVAL_SECONDS = 60  # How often to check for new markets
DEFAULT_TAG = "politics"  # Filter by tag (same as pipeline uses)
PAGE_SIZE = 100  # Max items per request
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3

# Output
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "08_new_market_polling"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class MarketInfo:
    """Compact market info for tracking."""

    id: str
    question: str
    event_id: str | None
    event_title: str | None
    created_at: datetime | None
    is_new: bool  # The `new` field from API
    outcomes: list[str]
    clob_token_ids: list[str]

    def __str__(self) -> str:
        return f"[{self.id}] {self.question[:60]}..."


@dataclass
class PollStats:
    """Statistics for a polling session."""

    polls_completed: int = 0
    new_markets_found: int = 0
    total_markets_seen: int = 0
    api_errors: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def print_summary(self) -> None:
        elapsed = datetime.now(timezone.utc) - self.start_time
        log.info("=" * 70)
        log.info("POLLING SESSION SUMMARY")
        log.info(f"  Duration: {elapsed}")
        log.info(f"  Polls completed: {self.polls_completed}")
        log.info(f"  New markets discovered: {self.new_markets_found}")
        log.info(f"  Total markets tracked: {self.total_markets_seen}")
        log.info(f"  API errors: {self.api_errors}")
        if self.polls_completed > 0:
            avg_per_poll = self.new_markets_found / self.polls_completed
            log.info(f"  Avg new markets per poll: {avg_per_poll:.2f}")
        log.info("=" * 70)


# =============================================================================
# API FUNCTIONS
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
            log.warning(f"Retry {attempt}/{MAX_RETRIES} for {endpoint}: {e}")
            await asyncio.sleep(delay)
    return None


async def fetch_tag_id(client: httpx.AsyncClient, tag_slug: str) -> str | None:
    """Fetch tag ID from slug (e.g., 'politics' -> '123')."""
    tag = await fetch_json(client, f"/tags/slug/{tag_slug}")
    if tag:
        tag_id = tag.get("id")
        label = tag.get("label", tag_slug)
        log.info(f"Resolved tag '{tag_slug}' -> id={tag_id} ({label})")
        return str(tag_id)
    log.warning(f"Tag '{tag_slug}' not found")
    return None


def parse_json_field(value: Any) -> Any:
    """Parse JSON string field if needed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def parse_datetime(value: Any) -> datetime | None:
    """Parse datetime string to datetime object."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # Handle various formats
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def extract_market_info(
    market: dict[str, Any], event: dict[str, Any] | None = None
) -> MarketInfo:
    """Extract relevant fields from a market response."""
    clob_token_ids = parse_json_field(market.get("clobTokenIds", []))
    outcomes = parse_json_field(market.get("outcomes", ["Yes", "No"]))

    return MarketInfo(
        id=str(market.get("id", "")),
        question=market.get("question", "Unknown"),
        event_id=str(market.get("groupItemId", ""))
        if market.get("groupItemId")
        else None,
        event_title=event.get("title") if event else market.get("groupItemTitle"),
        created_at=parse_datetime(market.get("createdAt")),
        is_new=market.get("new", False),
        outcomes=outcomes if isinstance(outcomes, list) else [],
        clob_token_ids=clob_token_ids if isinstance(clob_token_ids, list) else [],
    )


# =============================================================================
# POLLING STRATEGIES
# =============================================================================


async def poll_markets_endpoint(
    client: httpx.AsyncClient,
    limit: int = PAGE_SIZE,
    start_date_min: datetime | None = None,
) -> list[MarketInfo]:
    """
    Strategy A: Poll /markets endpoint ordered by createdAt DESC.

    This gives us direct access to the newest markets.
    """
    params: dict[str, Any] = {
        "limit": limit,
        "offset": 0,
        "order": "createdAt",
        "ascending": "false",  # Newest first
        "active": "true",
        "closed": "false",
    }

    if start_date_min:
        params["start_date_min"] = start_date_min.isoformat()

    markets_raw = await fetch_json(client, "/markets", params)
    if not markets_raw:
        return []

    return [extract_market_info(m) for m in markets_raw]


async def poll_events_endpoint(
    client: httpx.AsyncClient,
    limit: int = PAGE_SIZE,
    tag_id: str | None = None,
) -> list[MarketInfo]:
    """
    Strategy B: Poll /events endpoint to get events with their markets.

    Events are containers for related markets.

    Args:
        client: HTTP client
        limit: Max events to fetch
        tag_id: Optional tag ID to filter by (e.g., politics tag)
    """
    params: dict[str, Any] = {
        "limit": limit,
        "offset": 0,
        "order": "startDate",  # Events don't have createdAt, use startDate
        "ascending": "false",
        "active": "true",
        "closed": "false",
    }

    if tag_id:
        params["tag_id"] = tag_id

    events_raw = await fetch_json(client, "/events", params)
    if not events_raw:
        return []

    markets = []
    for event in events_raw:
        event_markets = event.get("markets", [])
        for m in event_markets:
            if m.get("active") and not m.get("closed"):
                markets.append(extract_market_info(m, event))

    return markets


async def poll_new_markets_only(
    client: httpx.AsyncClient,
    limit: int = PAGE_SIZE,
) -> list[MarketInfo]:
    """
    Strategy C: Poll /markets filtering for new=true markets.

    Polymarket marks recently created markets with a `new` boolean.
    """
    params: dict[str, Any] = {
        "limit": limit,
        "offset": 0,
        "order": "createdAt",
        "ascending": "false",
        "active": "true",
        "closed": "false",
        "new": "true",  # Only markets marked as new
    }

    markets_raw = await fetch_json(client, "/markets", params)
    if not markets_raw:
        return []

    return [extract_market_info(m) for m in markets_raw]


# =============================================================================
# MARKET TRACKER
# =============================================================================


class NewMarketTracker:
    """Tracks seen markets and detects new ones."""

    def __init__(self):
        self.seen_market_ids: set[str] = set()
        self.market_history: list[MarketInfo] = []
        self.stats = PollStats()

    def process_markets(self, markets: list[MarketInfo]) -> list[MarketInfo]:
        """
        Process a batch of markets and return only the new ones.

        Returns markets we haven't seen before in this session.
        """
        new_markets = []
        for market in markets:
            if market.id not in self.seen_market_ids:
                self.seen_market_ids.add(market.id)
                self.market_history.append(market)
                new_markets.append(market)

        self.stats.total_markets_seen = len(self.seen_market_ids)
        self.stats.new_markets_found += len(new_markets)

        return new_markets

    def save_history(self, output_path: Path) -> None:
        """Save discovered markets to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "total_markets": len(self.market_history),
            "markets": [
                {
                    "id": m.id,
                    "question": m.question,
                    "event_id": m.event_id,
                    "event_title": m.event_title,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "is_new_flag": m.is_new,
                    "outcomes": m.outcomes,
                    "clob_token_ids": m.clob_token_ids,
                }
                for m in self.market_history
            ],
        }

        output_path.write_text(json.dumps(data, indent=2))
        log.info(f"Saved {len(self.market_history)} markets to {output_path}")


# =============================================================================
# POLLING LOOP
# =============================================================================


async def run_polling_loop(
    strategy: str = "markets",
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    duration: int | None = None,
    tag: str | None = DEFAULT_TAG,
) -> NewMarketTracker:
    """
    Run continuous polling loop to detect new markets.

    Args:
        strategy: "markets", "events", or "new_only"
        interval: Seconds between polls
        duration: Optional max duration in seconds
        tag: Tag slug to filter by (e.g., "politics"). Use None for all markets.
    """
    tracker = NewMarketTracker()
    shutdown_event = asyncio.Event()

    # Handle graceful shutdown
    def signal_handler():
        log.info("\nShutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    log.info("=" * 70)
    log.info("POLYMARKET NEW MARKET POLLING")
    log.info(f"  Strategy: {strategy}")
    log.info(f"  Tag filter: {tag or 'none (all markets)'}")
    log.info(f"  Poll interval: {interval}s")
    log.info(f"  Duration: {duration}s" if duration else "  Duration: unlimited")
    log.info("=" * 70)

    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT
    ) as client:
        # Resolve tag_id if tag is specified
        tag_id = None
        if tag:
            tag_id = await fetch_tag_id(client, tag)
            if not tag_id:
                log.error(f"Could not resolve tag '{tag}'. Aborting.")
                return tracker

        while not shutdown_event.is_set():
            try:
                # Check duration limit
                if duration:
                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                    if elapsed >= duration:
                        log.info(f"Duration limit ({duration}s) reached.")
                        break

                # Execute polling strategy
                poll_start = datetime.now(timezone.utc)

                if strategy == "markets":
                    markets = await poll_markets_endpoint(client)
                elif strategy == "events":
                    markets = await poll_events_endpoint(client, tag_id=tag_id)
                elif strategy == "new_only":
                    markets = await poll_new_markets_only(client)
                else:
                    log.error(f"Unknown strategy: {strategy}")
                    break

                poll_duration = (
                    datetime.now(timezone.utc) - poll_start
                ).total_seconds()
                tracker.stats.polls_completed += 1

                # Process and detect new markets
                new_markets = tracker.process_markets(markets)

                # Log results
                if new_markets:
                    log.info(
                        f"🆕 Found {len(new_markets)} new market(s) (poll took {poll_duration:.1f}s)"
                    )
                    for market in new_markets[:5]:  # Show first 5
                        age = ""
                        if market.created_at:
                            age_delta = datetime.now(timezone.utc) - market.created_at
                            age = (
                                f" (created {age_delta.total_seconds() / 60:.0f}m ago)"
                            )
                        log.info(f"   • [{market.id}] {market.question[:55]}...{age}")
                    if len(new_markets) > 5:
                        log.info(f"   ... and {len(new_markets) - 5} more")
                else:
                    log.debug(f"No new markets (poll took {poll_duration:.1f}s)")

                # Wait for next poll
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue polling

            except httpx.HTTPError as e:
                tracker.stats.api_errors += 1
                log.error(f"API error: {e}")
                await asyncio.sleep(min(interval, 10))  # Brief pause before retry
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                await asyncio.sleep(5)

    return tracker


# =============================================================================
# COMPARISON MODE
# =============================================================================


async def compare_strategies() -> None:
    """
    Compare all three strategies in a single run.

    Useful for understanding which approach returns the most useful data.
    """
    log.info("=" * 70)
    log.info("STRATEGY COMPARISON MODE")
    log.info("=" * 70)

    async with httpx.AsyncClient(
        base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT
    ) as client:
        strategies = [
            ("markets", poll_markets_endpoint),
            ("events", poll_events_endpoint),
            ("new_only", poll_new_markets_only),
        ]

        for name, poll_fn in strategies:
            log.info(f"\n--- Strategy: {name} ---")
            start = datetime.now(timezone.utc)

            try:
                markets = await poll_fn(client)
                duration = (datetime.now(timezone.utc) - start).total_seconds()

                log.info(f"  Returned: {len(markets)} markets in {duration:.2f}s")

                if markets:
                    # Show newest markets
                    sorted_markets = sorted(
                        [m for m in markets if m.created_at],
                        key=lambda m: m.created_at,
                        reverse=True,
                    )[:3]

                    log.info("  Newest markets:")
                    for m in sorted_markets:
                        age = ""
                        if m.created_at:
                            age_delta = datetime.now(timezone.utc) - m.created_at
                            if age_delta.days > 0:
                                age = f"{age_delta.days}d ago"
                            elif age_delta.seconds > 3600:
                                age = f"{age_delta.seconds // 3600}h ago"
                            else:
                                age = f"{age_delta.seconds // 60}m ago"
                        new_flag = " [NEW]" if m.is_new else ""
                        log.info(f"    • {m.question[:50]}... ({age}){new_flag}")

                    # Count markets with new=true
                    new_count = sum(1 for m in markets if m.is_new)
                    log.info(f"  Markets with new=true: {new_count}")

            except Exception as e:
                log.error(f"  Error: {e}")


# =============================================================================
# MAIN
# =============================================================================


async def main(
    strategy: str = "markets",
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    duration: int | None = None,
    compare: bool = False,
    tag: str | None = DEFAULT_TAG,
) -> None:
    """Main entry point."""
    if compare:
        await compare_strategies()
        return

    tracker = await run_polling_loop(
        strategy=strategy,
        interval=interval,
        duration=duration,
        tag=tag,
    )

    # Print summary
    tracker.stats.print_summary()

    # Save results
    if tracker.market_history:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"markets_{timestamp}.json"
        tracker.save_history(output_path)


if __name__ == "__main__":
    # Parse arguments
    strategy = "markets"
    interval = DEFAULT_POLL_INTERVAL_SECONDS
    duration = None
    compare = False
    tag: str | None = DEFAULT_TAG

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif args[i] == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
            i += 2
        elif args[i] == "--duration" and i + 1 < len(args):
            duration = int(args[i + 1])
            i += 2
        elif args[i] == "--tag" and i + 1 < len(args):
            tag = args[i + 1] if args[i + 1].lower() != "none" else None
            i += 2
        elif args[i] == "--compare":
            compare = True
            i += 1
        else:
            i += 1

    asyncio.run(
        main(
            strategy=strategy,
            interval=interval,
            duration=duration,
            compare=compare,
            tag=tag,
        )
    )
