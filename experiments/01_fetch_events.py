"""
01: Fetch Events from Polymarket API.

WHAT IT DOES
    Pulls all active prediction markets from Polymarket's Gamma API.
    Filters to "politics" tag and removes inactive/closed markets.
    This is the data ingestion entry point - everything starts here.

WHY WE NEED THIS
    Polymarket has ~1000 active events with ~4000 markets. We need fresh
    price data to find mispriced hedges. Markets close and prices change
    constantly, so we re-fetch before each pipeline run.

HOW IT WORKS
    1. Query /tags/slug/politics to get the politics tag ID
    2. Paginate through /events?tag_id=X (100 per page)
    3. Filter out inactive events and closed markets client-side
    4. Parse JSON string fields (outcomes, prices) into proper types
    5. Save with timestamp for reproducibility

PIPELINE
    [Polymarket API] → [01_fetch_events] → 02_build_groups

INPUT
    External: https://gamma-api.polymarket.com
        - /tags/slug/{slug} - Get tag metadata
        - /events?tag_id=X  - Get events with nested markets

OUTPUT
    data/01_fetch_events/<timestamp>/
        - events.json   : All active events with nested markets array
        - tag_info.json : Politics tag metadata (id, slug, label)
        - summary.json  : Run stats (counts, config snapshot)

RUNTIME
    ~1-2 minutes (API-bound, 10 paginated requests)

CONFIGURATION
    - TARGET_TAG_SLUG: Tag to filter ("politics")
    - CLOSED: Include closed events (False)
    - PAGE_SIZE: Items per API page (100)
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
TARGET_TAG_SLUG = "politics"
CLOSED = False
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3

DATA_DIR = Path(__file__).parent.parent / "data"
SCRIPT_OUTPUT_DIR = DATA_DIR / "01_fetch_events"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# =============================================================================
# API FUNCTIONS
# =============================================================================


async def fetch_json(
    client: httpx.AsyncClient, endpoint: str, params: dict[str, Any] | None = None
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
            delay = attempt * (
                2
                if getattr(e, "response", None) and e.response.status_code == 429
                else 1
            )
            log.warning(f"Retry {attempt}/{MAX_RETRIES} for {endpoint}: {e}")
            await asyncio.sleep(delay)


async def fetch_all_pages(
    client: httpx.AsyncClient, endpoint: str, base_params: dict[str, Any]
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
        log.info(f"Fetched {len(page)} items from {endpoint} (total: {len(results)})")
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


def process_events(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Process events: filter active, parse JSON fields in markets."""
    json_fields = ["clobTokenIds", "outcomes"]
    processed = []
    markets_before = markets_after = 0

    for event in filter(is_active, events):
        markets = event.get("markets", [])
        markets_before += len(markets)

        active_markets = []
        for m in filter(is_active, markets):
            m = {**m, **{f: parse_json_field(m.get(f)) for f in json_fields if f in m}}
            # Parse outcomePrices: JSON string → list of floats
            if "outcomePrices" in m:
                prices = parse_json_field(m["outcomePrices"])
                m["outcomePrices"] = (
                    [float(p) for p in prices] if isinstance(prices, list) else []
                )
            active_markets.append(m)
        markets_after += len(active_markets)

        processed.append({**event, "markets": active_markets})

    return processed, markets_before, markets_after


def save_json(data: Any, filepath: Path) -> None:
    """Save data to JSON file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    log.info(f"Saved: {filepath}")


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = SCRIPT_OUTPUT_DIR / timestamp

    log.info("Fetching Polymarket Politics events...")

    async with httpx.AsyncClient(
        base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT
    ) as client:
        tag = await fetch_json(client, f"/tags/slug/{TARGET_TAG_SLUG}")
        if not tag:
            raise ValueError(f"Tag '{TARGET_TAG_SLUG}' not found")

        tag_id = tag["id"]
        log.info(f"Found tag: {tag.get('label')} (id={tag_id})")
        save_json(tag, output_dir / "tag_info.json")

        events_raw = await fetch_all_pages(
            client,
            "/events",
            {"tag_id": tag_id, "active": "true", "closed": str(CLOSED).lower()},
        )

        events, markets_before, markets_after = process_events(events_raw)
        log.info(
            f"Processed: {len(events)} events, {markets_after} markets "
            f"(filtered {markets_before - markets_after})"
        )

        if not events:
            log.warning("No active events found")
            return

        save_json(events, output_dir / "events.json")

        save_json(
            {
                "timestamp": timestamp,
                "tag": {
                    "id": tag_id,
                    "slug": tag.get("slug"),
                    "label": tag.get("label"),
                },
                "statistics": {
                    "total_events": len(events),
                    "total_markets": markets_after,
                    "markets_filtered_out": markets_before - markets_after,
                },
                "configuration": {
                    "gamma_api_base_url": GAMMA_API_BASE_URL,
                    "target_tag_slug": TARGET_TAG_SLUG,
                    "closed": CLOSED,
                },
            },
            output_dir / "summary.json",
        )

        log.info(f"Done! Output: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
