"""
Fetch events from Polymarket API.

Extracted from experiments/01_fetch_events.py for production pipeline.
"""

import asyncio
import json
import os
from typing import Any

import httpx
from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
TARGET_TAG_SLUG = os.getenv("POLYMARKET_TAG", "politics")
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3


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
            logger.warning(f"Retry {attempt}/{MAX_RETRIES} for {endpoint}: {e}")
            await asyncio.sleep(delay)
    return None


async def fetch_all_pages(
    client: httpx.AsyncClient,
    endpoint: str,
    base_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch all pages from a paginated endpoint."""
    results: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {**base_params, "limit": PAGE_SIZE, "offset": offset}
        page = await fetch_json(client, endpoint, params)
        if not page:
            break
        results.extend(page)
        logger.debug(f"Fetched {len(page)} items (total: {len(results)})")
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return results


# =============================================================================
# DATA PROCESSING
# =============================================================================


def is_active(item: dict[str, Any]) -> bool:
    """Check if event/market is active and not closed."""
    return item.get("active") is True and item.get("closed") is not True


def parse_json_field(value: Any) -> Any:
    """Parse JSON string field if needed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def parse_outcome_prices(value: Any) -> list[float]:
    """Parse outcomePrices field to list of floats."""
    parsed = parse_json_field(value)
    if isinstance(parsed, list):
        return [float(p) for p in parsed]
    return []


def process_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process events: filter active, parse JSON fields in markets."""
    json_fields = ["clobTokenIds", "outcomes"]
    processed = []

    for event in filter(is_active, events):
        markets = event.get("markets", [])

        active_markets = []
        for m in filter(is_active, markets):
            # Parse JSON string fields
            m = {**m, **{f: parse_json_field(m.get(f)) for f in json_fields if f in m}}
            # Parse outcomePrices as list of floats
            if "outcomePrices" in m:
                m["outcomePrices"] = parse_outcome_prices(m["outcomePrices"])
            active_markets.append(m)

        processed.append({**event, "markets": active_markets})

    return processed


# =============================================================================
# MAIN FUNCTION
# =============================================================================


async def fetch_events(
    tag_slugs: str = TARGET_TAG_SLUG,
    max_events: int | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch all active events from Polymarket API.

    Args:
        tag_slugs: Comma-separated tags to filter events by (OR logic).
                   E.g., "politics" or "politics,sports,crypto"
        max_events: Optional limit on number of events to return.
                    If None, returns all events.

    Returns:
        List of processed events with active markets
    """
    tags = [t.strip() for t in tag_slugs.split(",") if t.strip()]
    if not tags:
        raise ValueError("No valid tags provided")

    logger.info(f"Fetching Polymarket events (tags: {tags})...")

    async with httpx.AsyncClient(
        base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT
    ) as client:
        all_events: dict[str, dict[str, Any]] = {}  # Dedupe by event ID

        for tag_slug in tags:
            # Get tag ID
            tag = await fetch_json(client, f"/tags/slug/{tag_slug}")
            if not tag:
                logger.warning(f"Tag '{tag_slug}' not found, skipping")
                continue

            tag_id = tag["id"]
            logger.info(f"Found tag: {tag.get('label')} (id={tag_id})")

            # Fetch events for this tag
            events_raw = await fetch_all_pages(
                client,
                "/events",
                {"tag_id": tag_id, "active": "true", "closed": "false"},
            )

            # Add to combined results (dedupe by ID)
            for event in events_raw:
                event_id = event.get("id")
                if event_id and event_id not in all_events:
                    all_events[event_id] = event

        if not all_events:
            raise ValueError(f"No events found for tags: {tags}")

        # Process events and markets
        events = process_events(list(all_events.values()))

        # Apply max_events limit if specified
        if max_events is not None and len(events) > max_events:
            events = events[:max_events]
            logger.info(
                f"Limited to {len(events)} events (max_events={max_events}) from {len(tags)} tag(s)"
            )
        else:
            logger.info(f"Fetched {len(events)} active events from {len(tags)} tag(s)")

        return events


def fetch_events_sync(
    tag_slugs: str = TARGET_TAG_SLUG,
    max_events: int | None = None,
) -> list[dict[str, Any]]:
    """Synchronous wrapper for fetch_events."""
    return asyncio.run(fetch_events(tag_slugs, max_events=max_events))


# =============================================================================
# PRICE EXTRACTION
# =============================================================================


def extract_prices(events: list[dict[str, Any]]) -> dict[str, float]:
    """
    Extract current YES prices for all events.

    Returns:
        Dict mapping event_id to YES price
    """
    prices = {}
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        markets = event.get("markets", [])
        if markets:
            # Use first market's YES price
            market = markets[0]
            outcome_prices = market.get("outcomePrices", [])
            if outcome_prices:
                prices[event_id] = outcome_prices[0]

    return prices
