"""WebSocket endpoint for live price updates."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Polymarket API
GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 10.0

# Data directory
DATA_DIR = Path(__file__).parent.parent.parent / "data"


async def fetch_event_prices(event_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch current prices for events from Polymarket API.

    Returns dict mapping event_id to price info.
    """
    prices: dict[str, dict[str, Any]] = {}

    if not event_ids:
        return prices

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        # Fetch in batches of 50
        for i in range(0, len(event_ids), 50):
            batch = event_ids[i : i + 50]
            try:
                # Fetch each event individually (API may not support batch)
                for event_id in batch:
                    try:
                        resp = await client.get(
                            f"{GAMMA_API_BASE_URL}/events/{event_id}"
                        )
                        if resp.status_code == 200:
                            event = resp.json()
                            markets = event.get("markets", [])
                            if markets:
                                # Get first market's Yes price
                                market = markets[0]
                                outcome_prices = market.get("outcomePrices", [])
                                if isinstance(outcome_prices, str):
                                    outcome_prices = json.loads(outcome_prices)
                                yes_price = (
                                    float(outcome_prices[0]) if outcome_prices else None
                                )

                                prices[event_id] = {
                                    "price": yes_price,
                                    "title": event.get("title"),
                                    "market_id": market.get("id"),
                                }
                    except (
                        httpx.RequestError,
                        json.JSONDecodeError,
                        IndexError,
                        KeyError,
                    ):
                        continue
            except httpx.RequestError:
                continue

    return prices


def get_active_event_ids() -> list[str]:
    """Get event IDs from the latest opportunities run."""
    opportunities_dir = DATA_DIR / "06_3_export_opportunities"
    if not opportunities_dir.exists():
        return []

    # Find latest run
    runs = sorted(
        [d for d in opportunities_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        reverse=True,
    )
    if not runs:
        return []

    # Load opportunities and extract event IDs
    opportunities_file = runs[0] / "opportunities.json"
    if not opportunities_file.exists():
        return []

    try:
        data = json.loads(opportunities_file.read_text())
        # Handle both formats: flat list or nested {"opportunities": [...]}
        if isinstance(data, dict) and "opportunities" in data:
            opportunities = data["opportunities"]
        elif isinstance(data, list):
            opportunities = data
        else:
            return []

        event_ids = set()
        for opp in opportunities[:100]:  # Limit to top 100
            if isinstance(opp, dict):
                if "trigger" in opp and "event_id" in opp["trigger"]:
                    event_ids.add(str(opp["trigger"]["event_id"]))
                if "consequence" in opp and "event_id" in opp["consequence"]:
                    event_ids.add(str(opp["consequence"]["event_id"]))
        return list(event_ids)
    except (json.JSONDecodeError, KeyError):
        return []


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws")
async def price_websocket(websocket: WebSocket):
    """WebSocket endpoint for live price updates.

    Sends price updates every 10 seconds for events in the opportunities list.
    """
    await manager.connect(websocket)

    try:
        # Send initial connection message
        await websocket.send_json(
            {
                "type": "connected",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Get event IDs to track
        event_ids = get_active_event_ids()

        await websocket.send_json(
            {
                "type": "tracking",
                "event_count": len(event_ids),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Send price updates every 10 seconds
        while True:
            if event_ids:
                prices = await fetch_event_prices(event_ids)

                await websocket.send_json(
                    {
                        "type": "price_update",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "prices": prices,
                        "event_count": len(prices),
                    }
                )

            await asyncio.sleep(10)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@router.get("/current")
async def get_current_prices(
    limit: int = 20,
) -> dict[str, Any]:
    """Get current prices for tracked events (REST endpoint)."""
    event_ids = get_active_event_ids()[:limit]
    prices = await fetch_event_prices(event_ids)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_count": len(prices),
        "prices": prices,
    }
