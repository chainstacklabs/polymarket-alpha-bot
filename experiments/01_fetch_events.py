"""
01: Fetch Events from Polymarket API.

WHAT IT DOES
    Pulls all active events (with nested markets) for one or more tags from
    the Gamma API, keeps active markets on a supported protocol, and saves
    them. Entry point of the pipeline; mirrors backend/core/steps/fetch.py.

WHY WE NEED THIS
    Every later step works on a snapshot of live markets and prices.
    Markets open, close and reprice constantly, so each run re-fetches.

HOW IT WORKS
    1. /tags/slug/{slug} → tag ID (per tag; tags are OR-ed, events deduped)
    2. Paginate /events/keyset?tag_id=X (limit=200, follow next_cursor)
    3. Keep active, non-closed events and markets
    4. Keep markets with version "v1" (CTF) or "v2" (Protocol V2); drop
       missing/unknown versions
    5. Parse JSON-string fields (clobTokenIds, outcomes, outcomePrices)

    Fees: /events/keyset omits feeSchedule. Production adds it for
    fee-bearing markets via /markets/keyset?id=... (enrich_fees); this demo
    skips that because grouping does not need it.

INPUT
    https://gamma-api.polymarket.com (public, no auth)

OUTPUT
    data/01_fetch_events/<run_timestamp>/
        - events.json   : Active events with nested markets
        - tag_info.json : Resolved tag metadata
        - summary.json  : Counts and config

USAGE
    uv run --frozen --project backend experiments/01_fetch_events.py
    uv run --frozen --project backend experiments/01_fetch_events.py --tags politics,crypto
    uv run --frozen --project backend experiments/01_fetch_events.py --limit 50
"""

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_TAGS = "politics"
PAGE_SIZE = 200
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 3
SUPPORTED_MARKET_VERSIONS = ("v1", "v2")

DATA_DIR = Path(__file__).parent.parent / "data"
SCRIPT_OUTPUT_DIR = DATA_DIR / "01_fetch_events"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# =============================================================================
# API FUNCTIONS
# =============================================================================


async def fetch_json(
    client: httpx.AsyncClient, endpoint: str, params: dict[str, Any] | None = None
) -> Any:
    """GET JSON with a small retry loop (longer back-off on 429)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            if attempt == MAX_RETRIES:
                raise
            response = getattr(e, "response", None)
            delay = attempt * (2 if response is not None and response.status_code == 429 else 1)
            log.warning(f"Retry {attempt}/{MAX_RETRIES} for {endpoint}: {e}")
            await asyncio.sleep(delay)


async def fetch_all_pages(
    client: httpx.AsyncClient, endpoint: str, base_params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Fetch all pages from a keyset-paginated endpoint."""
    # Collection key matches the resource name (/events/keyset -> "events").
    resource_key = endpoint.rstrip("/").split("/")[-2]
    results: list[dict[str, Any]] = []
    after_cursor: str | None = None
    while True:
        params: dict[str, Any] = {**base_params, "limit": PAGE_SIZE}
        if after_cursor:
            params["after_cursor"] = after_cursor
        page = await fetch_json(client, endpoint, params)
        if not page:
            break
        items = page.get(resource_key, []) if isinstance(page, dict) else []
        if not items:
            break
        results.extend(items)
        log.info(f"Fetched {len(items)} items from {endpoint} (total: {len(results)})")
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


def process_events(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Filter active events/markets on a supported protocol; parse JSON fields."""
    json_fields = ["clobTokenIds", "outcomes"]
    processed = []
    markets_before = markets_after = 0

    for event in filter(is_active, events):
        markets = event.get("markets", [])
        markets_before += len(markets)

        active_markets = []
        for m in filter(is_active, markets):
            if m.get("version") not in SUPPORTED_MARKET_VERSIONS:
                continue
            m = {**m, **{f: parse_json_field(m.get(f)) for f in json_fields if f in m}}
            if "outcomePrices" in m:
                prices = parse_json_field(m["outcomePrices"])
                m["outcomePrices"] = [float(p) for p in prices] if isinstance(prices, list) else []
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


async def main(tag_slugs: str, limit: int | None) -> None:
    tags = [t.strip() for t in re.split(r"[,;]", tag_slugs) if t.strip()]
    if not tags:
        raise ValueError("No valid tags provided")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = SCRIPT_OUTPUT_DIR / timestamp
    log.info(f"Fetching Polymarket events (tags: {tags})...")

    async with httpx.AsyncClient(base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        tag_infos: list[dict[str, Any]] = []
        all_events: dict[str, dict[str, Any]] = {}  # dedupe by event ID across tags

        for slug in tags:
            tag = await fetch_json(client, f"/tags/slug/{slug}")
            if not tag:
                log.warning(f"Tag '{slug}' not found, skipping")
                continue
            log.info(f"Found tag: {tag.get('label')} (id={tag['id']})")
            tag_infos.append({k: tag.get(k) for k in ("id", "slug", "label")})

            events_raw = await fetch_all_pages(
                client,
                "/events/keyset",
                {"tag_id": tag["id"], "active": "true", "closed": "false"},
            )
            for event in events_raw:
                event_id = event.get("id")
                if event_id and event_id not in all_events:
                    all_events[event_id] = event

    events, markets_before, markets_after = process_events(list(all_events.values()))
    log.info(
        f"Processed: {len(events)} events, {markets_after} markets "
        f"(filtered {markets_before - markets_after} inactive/unsupported)"
    )
    if limit is not None and len(events) > limit:
        events = events[:limit]
        log.info(f"Limited to {limit} events (--limit)")

    if not events:
        log.warning("No active events found")
        return

    save_json(events, output_dir / "events.json")
    save_json(tag_infos, output_dir / "tag_info.json")
    save_json(
        {
            "run_timestamp": timestamp,
            "tags": tag_infos,
            "statistics": {
                "total_events": len(events),
                "total_markets": sum(len(e["markets"]) for e in events),
            },
            "configuration": {
                "gamma_api_base_url": GAMMA_API_BASE_URL,
                "tags": tags,
                "limit": limit,
                "supported_versions": list(SUPPORTED_MARKET_VERSIONS),
            },
        },
        output_dir / "summary.json",
    )
    log.info(f"Done! Output: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch active Polymarket events")
    parser.add_argument("--tags", default=DEFAULT_TAGS, help="Tag slugs, comma-separated")
    parser.add_argument("--limit", type=int, default=None, help="Keep only the first N events")
    args = parser.parse_args()
    asyncio.run(main(args.tags, args.limit))
