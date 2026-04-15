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

from core.http_retry import fetch_json_with_retry
from core.paths import GAMMA_API_BASE_URL

# =============================================================================
# CONFIGURATION
# =============================================================================
TARGET_TAG_SLUG = os.getenv("POLYMARKET_TAG", "politics")
PAGE_SIZE = 200
REQUEST_TIMEOUT = 30.0


async def fetch_all_pages(
    client: httpx.AsyncClient,
    endpoint: str,
    base_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch all pages from a keyset-paginated endpoint."""
    # Collection key matches the resource name (e.g. /events/keyset -> "events").
    resource_key = endpoint.rstrip("/").split("/")[-2]
    results: list[dict[str, Any]] = []
    after_cursor: str | None = None
    while True:
        params: dict[str, Any] = {**base_params, "limit": PAGE_SIZE}
        if after_cursor:
            params["after_cursor"] = after_cursor
        page = await fetch_json_with_retry(client, endpoint, params)
        if not page:
            break
        items = page.get(resource_key, []) if isinstance(page, dict) else []
        if not items:
            break
        results.extend(items)
        logger.debug(
            f"Fetched {len(items)} items from {endpoint} (total: {len(results)})"
        )
        next_cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not next_cursor:
            break
        after_cursor = next_cursor
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
# FEE ENRICHMENT
# =============================================================================


async def enrich_fees(
    client: httpx.AsyncClient, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach feeSchedule to every market with feesEnabled=true.

    Mutates market dicts in place for efficiency; returns the same events list.
    No-op for fee-exempt markets (zero calls when target tag is fee-exempt).
    """
    from core.fees import is_fee_bearing

    # Collect fee-bearing market IDs across all events
    fee_bearing: dict[str, dict[str, Any]] = {}
    for event in events:
        for market in event.get("markets", []):
            if is_fee_bearing(market) and "feeSchedule" not in market:
                mid = market.get("id")
                if mid:
                    fee_bearing[str(mid)] = market

    if not fee_bearing:
        return events

    logger.info(f"Enriching {len(fee_bearing)} fee-bearing market(s) with feeSchedule")

    # Batch in groups of 50 to keep URL lengths reasonable
    ids = list(fee_bearing.keys())
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        params = [("id", mid) for mid in batch]
        try:
            payload = await fetch_json_with_retry(
                client, "/markets/keyset", params=params
            )
            for m in payload.get("markets", []):
                mid = str(m.get("id", ""))
                if mid in fee_bearing and "feeSchedule" in m:
                    fee_bearing[mid]["feeSchedule"] = m["feeSchedule"]
        except Exception as e:
            logger.warning(f"Fee enrichment batch failed (ids={batch[:3]}...): {e}")

    enriched = sum(1 for m in fee_bearing.values() if "feeSchedule" in m)
    logger.info(f"Fee enrichment: {enriched}/{len(fee_bearing)} markets enriched")
    return events


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
            tag = await fetch_json_with_retry(client, f"/tags/slug/{tag_slug}")
            if not tag:
                logger.warning(f"Tag '{tag_slug}' not found, skipping")
                continue

            tag_id = tag["id"]
            logger.info(f"Found tag: {tag.get('label')} (id={tag_id})")

            # Fetch events for this tag
            events_raw = await fetch_all_pages(
                client,
                "/events/keyset",
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

        # Attach feeSchedule to fee-bearing markets
        events = await enrich_fees(client, events)

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
