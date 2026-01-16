"""WebSocket endpoint for live price updates."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.price_aggregation import price_aggregation

router = APIRouter()


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

    Broadcasts cached prices every 10 seconds.
    Prices are fetched by the shared PriceCacheService background task.
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

        # Send tracking info
        metadata = price_aggregation.get_metadata()
        await websocket.send_json(
            {
                "type": "tracking",
                "event_count": metadata.event_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Broadcast cached prices every 10 seconds
        while True:
            prices = price_aggregation.get_prices_dict()
            metadata = price_aggregation.get_metadata()

            await websocket.send_json(
                {
                    "type": "price_update",
                    "timestamp": (
                        metadata.last_fetch.isoformat()
                        if metadata.last_fetch
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    "prices": prices,
                    "event_count": len(prices),
                    "is_stale": metadata.is_stale,
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
    prices = price_aggregation.get_prices_dict()
    metadata = price_aggregation.get_metadata()

    # Apply limit
    if limit and limit < len(prices):
        prices = dict(list(prices.items())[:limit])

    return {
        "timestamp": (
            metadata.last_fetch.isoformat()
            if metadata.last_fetch
            else datetime.now(timezone.utc).isoformat()
        ),
        "event_count": len(prices),
        "prices": prices,
        "is_stale": metadata.is_stale,
    }
