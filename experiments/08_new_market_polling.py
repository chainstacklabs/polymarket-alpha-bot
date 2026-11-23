"""
08: Detect New Polymarket Events by Polling.

WHAT IT DOES
    Polls the Gamma API for the newest events in a tag and logs the ones
    not seen before. Mirrors backend/core/market_poller.py, minus the
    processing (production runs process_new_event() on each new event).

WHY WE NEED THIS
    The batch pipeline fetches once per run. Polling keeps portfolios fresh
    by picking up new events between runs. Gamma has no push feed for new
    markets (the CLOB WebSocket only covers known tokens), so we poll.

HOW IT WORKS
    1. Resolve the tag slug to an ID once (/tags/slug/{slug})
    2. Every --interval seconds (min 30, as in production) GET
       /events/keyset?tag_id=X&active=true&closed=false
       &order=startDate&ascending=false&limit=200 (newest first; oldest-first
       would almost never show new events)
    3. Keep active events with at least one active market on a supported
       protocol (version v1 or v2)
    4. New = event ID not in the seen set. The first poll only fills the
       seen set (production loads it from state). Production marks an event
       seen only after it was processed; this demo marks it right away

INPUT
    https://gamma-api.polymarket.com (public, no auth)

OUTPUT
    Logs; new events (if any) to data/08_new_market_polling/<timestamp>/new_events.json

USAGE
    uv run --frozen --project backend experiments/08_new_market_polling.py --duration 300
    uv run --frozen --project backend experiments/08_new_market_polling.py --tag crypto
"""

import argparse
import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_TAG = "politics"
DEFAULT_POLL_INTERVAL_SECONDS = 60
MIN_POLL_INTERVAL = 30  # never poll faster than this
PAGE_SIZE = 200
REQUEST_TIMEOUT = 30.0
SUPPORTED_MARKET_VERSIONS = ("v1", "v2")

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "08_new_market_polling"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================


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
    """Keep active markets on a supported protocol; parse JSON fields."""
    json_fields = ["clobTokenIds", "outcomes", "outcomePrices"]
    markets = []
    for m in filter(is_active, event.get("markets", [])):
        if m.get("version") not in SUPPORTED_MARKET_VERSIONS:
            log.debug(f"Skipping market {m.get('id')}: protocol {m.get('version')!r}")
            continue
        markets.append({**m, **{f: parse_json_field(m[f]) for f in json_fields if f in m}})
    return {**event, "markets": markets}


async def fetch_newest_events(client: httpx.AsyncClient, tag_id: str | None) -> list[dict]:
    """One page of the newest active events (same params as production)."""
    params: dict[str, Any] = {
        "limit": PAGE_SIZE,
        "active": "true",
        "closed": "false",
        "order": "startDate",
        "ascending": "false",
    }
    if tag_id:
        params["tag_id"] = tag_id
    resp = await client.get("/events/keyset", params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("events", []) if isinstance(data, dict) else data or []


def find_new_events(events_raw: list[dict], seen: set[str]) -> list[dict]:
    """Active, unseen events that still have usable markets."""
    new_events = []
    for event in filter(is_active, events_raw):
        event_id = str(event.get("id", ""))
        if event_id and event_id not in seen:
            processed = process_event_markets(event)
            if processed["markets"]:
                new_events.append(processed)
    return new_events


# =============================================================================
# POLLING LOOP
# =============================================================================


async def poll(tag: str | None, interval: int, duration: int | None) -> list[dict]:
    """Poll until Ctrl+C or --duration; return the new events found."""
    interval = max(interval, MIN_POLL_INTERVAL)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    if duration:
        loop.call_later(duration, stop.set)

    seen: set[str] = set()
    discovered: list[dict] = []
    polls = 0
    log.info(
        f"Polling tag={tag or 'all'} every {interval}s "
        f"({f'{duration}s' if duration else 'until Ctrl+C'})"
    )

    async with httpx.AsyncClient(base_url=GAMMA_API_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        tag_id = None
        if tag:
            resp = await client.get(f"/tags/slug/{tag}")
            if resp.status_code == 200 and resp.json():
                tag_id = str(resp.json().get("id"))
                log.info(f"Resolved tag '{tag}' -> id={tag_id}")
            else:
                log.warning(f"Tag '{tag}' not found, polling all events")

        while not stop.is_set():
            try:
                new_events = find_new_events(await fetch_newest_events(client, tag_id), seen)
                polls += 1
                seen.update(str(e["id"]) for e in new_events)
                if polls == 1:
                    log.info(f"Baseline: {len(new_events)} events marked as seen")
                elif new_events:
                    discovered.extend(new_events)
                    log.info(f"Detected {len(new_events)} new event(s)")
                    for e in new_events[:5]:
                        log.info(
                            f"  - [{e['id']}] {e.get('title', '')[:60]} "
                            f"({len(e['markets'])} markets, start {e.get('startDate')})"
                        )
                else:
                    log.info(f"Poll {polls}: no new events (seen {len(seen)})")
            except httpx.HTTPError as e:
                log.error(f"Polling error: {e}")

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    log.info(f"Done: {polls} polls, {len(discovered)} new events, {len(seen)} seen")
    return discovered


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Gamma for new Polymarket events")
    parser.add_argument("--tag", default=DEFAULT_TAG, help="Tag slug, or 'none' for all")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--duration", type=int, help="Stop after N seconds")
    args = parser.parse_args()

    tag = None if args.tag.lower() == "none" else args.tag
    discovered = asyncio.run(poll(tag, args.interval, args.duration))

    if discovered:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / timestamp / "new_events.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(discovered, indent=2, ensure_ascii=False))
        log.info(f"Saved {len(discovered)} new events to {path}")


if __name__ == "__main__":
    main()
