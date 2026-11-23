"""
07: Stream Real-Time Prices from the Polymarket CLOB WebSocket.

WHAT IT DOES
    Subscribes to the market WebSocket for the tokens in our portfolios and
    logs book snapshots, best bid/ask changes and trades. Mirrors
    backend/server/token_resolver.py + clob_websocket.py.

WHY WE NEED THIS
    Polling REST prices is slow and heavy; one WebSocket pushes best
    bid/ask changes within ~100ms, which keeps portfolio prices live.

HOW IT WORKS
    1. Market IDs from data/_live/portfolios.json (top 200 portfolios);
       if none resolve, the top --sample markets by 24h volume
    2. Gamma /markets/{id} → token IDs by protocol version (v2 positionIds,
       v1 clobTokenIds, other versions skipped); keep markets with 2 IDs
    3. Connect to the market WebSocket, subscribe, PING every 10s
    4. Best price from a book snapshot = max bid / min ask (levels arrive
       worst-to-best, so the first level is NOT the best)

CLOB WEBSOCKET API
    Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
    Subscribe: {"assets_ids": [...], "type": "market"} (max 500 per connection)
    Events: book, price_change, last_trade_price, tick_size_change

INPUT
    data/_live/portfolios.json (optional), Gamma API (public)

OUTPUT
    Logs only

USAGE
    uv run --frozen --project backend experiments/07_websocket_prices.py --duration 30
"""

import asyncio
import json
import logging
import argparse
import signal
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"
PORTFOLIOS_PATH = LIVE_DIR / "portfolios.json"

# WebSocket settings
PING_INTERVAL_SECONDS = 10
MAX_TOKENS_PER_CONNECTION = 500  # Polymarket limit
REQUEST_TIMEOUT = 10.0
MAX_PORTFOLIOS = 200  # same cap as production token_resolver
DEFAULT_SAMPLE_MARKETS = 5
SUPPORTED_MARKET_VERSIONS = ("v1", "v2")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


# =============================================================================
# DATA LOADING
# =============================================================================


def load_market_ids_from_portfolios() -> list[str]:
    """Unique target/cover market IDs from the top portfolios."""
    if not PORTFOLIOS_PATH.exists():
        log.warning(f"Portfolios file not found: {PORTFOLIOS_PATH}")
        return []

    data = json.loads(PORTFOLIOS_PATH.read_text())
    portfolios = data.get("portfolios", []) if isinstance(data, dict) else data

    market_ids = set()
    for p in portfolios[:MAX_PORTFOLIOS]:
        if target_id := p.get("target_market_id"):
            market_ids.add(str(target_id))
        if cover_id := p.get("cover_market_id"):
            market_ids.add(str(cover_id))

    log.info(f"Loaded {len(market_ids)} unique market IDs from portfolios")
    return list(market_ids)


def outcome_token_ids(market: dict) -> list[str]:
    """Trading IDs [YES, NO] chosen by the market's protocol version.

    v2 → positionIds, v1 → clobTokenIds (Gamma may send both; the version
    decides). Missing/unknown versions give []. Copy of core/markets.py.
    """
    version = market.get("version")
    if version not in SUPPORTED_MARKET_VERSIONS:
        return []
    if version == "v2":
        raw = market.get("positionIds") or []
    else:
        raw = market.get("clobTokenIds") or "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [str(i) for i in raw if str(i).isdigit()]


def add_market_tokens(token_map: dict[str, dict], market: dict) -> bool:
    """Map a market's YES/NO token IDs to display metadata. False if unusable."""
    token_ids = outcome_token_ids(market)
    if len(token_ids) < 2:
        log.warning(
            f"Market {market.get('id')} has no usable token IDs (version {market.get('version')!r})"
        )
        return False

    outcomes = market.get("outcomes", ["Yes", "No"])
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    question = market.get("question", "")
    for i, token_id in enumerate(token_ids[:2]):
        token_map[token_id] = {
            "market_id": str(market.get("id")),
            "question": question,
            "side": outcomes[i] if i < len(outcomes) else ("Yes", "No")[i],
        }
    return True


async def fetch_token_map(market_ids: list[str]) -> dict[str, dict]:
    """Resolve market IDs to {token_id: {market_id, question, side}}."""
    token_map: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for market_id in market_ids:
            try:
                resp = await client.get(f"{GAMMA_API_BASE_URL}/markets/{market_id}")
                resp.raise_for_status()
                market = resp.json()
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                log.warning(f"Error fetching market {market_id}: {e}")
                continue
            if market.get("closed") or not market.get("active"):
                continue  # closed markets have no live book
            add_market_tokens(token_map, market)

    log.info(f"Resolved {len(token_map)} token IDs from {len(market_ids)} markets")
    return token_map


async def fetch_sample_token_map(count: int) -> dict[str, dict]:
    """Fallback: tokens of the top active markets by 24h volume."""
    params = {
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": count,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{GAMMA_API_BASE_URL}/markets/keyset", params=params)
        resp.raise_for_status()
        markets = resp.json().get("markets", [])

    token_map: dict[str, dict] = {}
    for market in markets:
        add_market_tokens(token_map, market)
    log.info(f"Sample: {len(token_map)} token IDs from {len(markets)} top-volume markets")
    return token_map


# =============================================================================
# WEBSOCKET CLIENT
# =============================================================================


class PolymarketPriceStream:
    """WebSocket client for streaming Polymarket CLOB prices."""

    def __init__(self, token_map: dict[str, dict]):
        self.token_map = token_map
        self.token_ids = list(token_map.keys())
        self.ws = None
        self._running = False
        self._ping_task = None

        # Stats
        self.events_received = 0
        self.price_changes_received = 0
        self.last_prices: dict[str, dict] = {}  # token_id -> {bid, ask, time}

    async def connect(self) -> None:
        """Connect to WebSocket and subscribe to tokens."""
        if len(self.token_ids) > MAX_TOKENS_PER_CONNECTION:
            log.warning(
                f"Token count ({len(self.token_ids)}) exceeds limit "
                f"({MAX_TOKENS_PER_CONNECTION}). Truncating."
            )
            self.token_ids = self.token_ids[:MAX_TOKENS_PER_CONNECTION]

        log.info(f"Connecting to {CLOB_WS_URL}")
        log.info(f"Subscribing to {len(self.token_ids)} token IDs")

        self._running = True

        try:
            async with websockets.connect(
                CLOB_WS_URL,
                ping_interval=None,  # We handle pings manually
                ping_timeout=None,
            ) as ws:
                self.ws = ws

                # Send subscription message
                subscription = {
                    "assets_ids": self.token_ids,
                    "type": "market",
                }
                await ws.send(json.dumps(subscription))
                log.info("Subscription sent")

                # Start ping task
                self._ping_task = asyncio.create_task(self._ping_loop())

                # Listen for messages
                await self._message_loop()

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"WebSocket connection closed: {e}")
        except Exception as e:
            log.error(f"WebSocket error: {e}")
        finally:
            self._running = False
            if self._ping_task:
                self._ping_task.cancel()

    async def _ping_loop(self) -> None:
        """Send PING messages to keep connection alive."""
        while self._running:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if self.ws and self._running:
                try:
                    await self.ws.send("PING")
                    log.debug("PING sent")
                except Exception as e:
                    log.warning(f"Failed to send PING: {e}")
                    break

    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self.ws:
            self.events_received += 1
            receive_time = datetime.now(timezone.utc)

            # Skip PONG responses
            if message == "PONG":
                continue

            try:
                data = json.loads(message)

                # Handle array of events (initial book snapshots come as array)
                if isinstance(data, list):
                    for item in data:
                        self._process_event(item, receive_time)
                else:
                    self._process_event(data, receive_time)

            except json.JSONDecodeError:
                log.warning(f"Failed to parse message: {message[:100]}")
            except Exception as e:
                log.error(f"Error processing message: {e}")
                log.debug(f"Message was: {message[:500]}")

    def _process_event(self, data: dict, receive_time: datetime) -> None:
        """Process a single event from the WebSocket."""
        event_type = data.get("event_type")

        if event_type == "book":
            self._handle_book(data, receive_time)
        elif event_type == "price_change":
            self._handle_price_change(data, receive_time)
        elif event_type == "last_trade_price":
            self._handle_last_trade(data, receive_time)
        elif event_type == "tick_size_change":
            log.debug(f"Tick size change: {data}")
        else:
            log.debug(f"Unknown event type: {event_type}")

    def _handle_book(self, data: dict, receive_time: datetime) -> None:
        """Handle full order book snapshot."""
        asset_id = data.get("asset_id", "unknown")
        meta = self.token_map.get(asset_id, {})

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        # Handle different formats: [["price", "size"], ...] or [{"price": x}, ...]
        # Levels arrive worst-to-best, so take max bid / min ask, not orders[0].
        def level_prices(orders: list) -> list[float]:
            out = []
            for o in orders:
                if isinstance(o, dict):
                    out.append(float(o.get("price", 0)))
                elif isinstance(o, list) and o:
                    out.append(float(o[0]))
                elif o:
                    out.append(float(o))
            return out

        bid_prices, ask_prices = level_prices(bids), level_prices(asks)
        best_bid = max(bid_prices) if bid_prices else None
        best_ask = min(ask_prices) if ask_prices else None

        self.last_prices[asset_id] = {
            "bid": best_bid,
            "ask": best_ask,
            "time": receive_time,
        }

        question = meta.get("question", "?")[:40]
        side = meta.get("side", "?")
        log.info(
            f"📖 BOOK | {side:3} | bid={best_bid or 'N/A':>6} ask={best_ask or 'N/A':>6} "
            f"| {question}"
        )

    def _handle_price_change(self, data: dict, receive_time: datetime) -> None:
        """Handle incremental price updates."""
        self.price_changes_received += 1

        # Estimate latency from timestamp if available
        ts = data.get("timestamp")
        latency_ms = None
        if ts:
            try:
                ts_int = int(ts) if isinstance(ts, str) else ts
                event_time = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc)
                latency_ms = (receive_time - event_time).total_seconds() * 1000
            except (ValueError, TypeError):
                pass

        for change in data.get("price_changes", []):
            asset_id = change.get("asset_id", "unknown")
            meta = self.token_map.get(asset_id, {})

            best_bid = change.get("best_bid")
            best_ask = change.get("best_ask")

            # Update cache
            prev = self.last_prices.get(asset_id, {})
            self.last_prices[asset_id] = {
                "bid": float(best_bid) if best_bid else prev.get("bid"),
                "ask": float(best_ask) if best_ask else prev.get("ask"),
                "time": receive_time,
            }

            question = meta.get("question", "?")[:40]
            side = meta.get("side", "?")
            latency_str = f"{latency_ms:>5.0f}ms" if latency_ms else "  N/A"

            log.info(
                f"💱 PRICE | {side:3} | bid={best_bid or 'N/A':>6} ask={best_ask or 'N/A':>6} "
                f"| lat={latency_str} | {question}"
            )

    def _handle_last_trade(self, data: dict, receive_time: datetime) -> None:
        """Handle trade execution events."""
        asset_id = data.get("asset_id", "unknown")
        meta = self.token_map.get(asset_id, {})

        price = data.get("price")
        size = data.get("size")
        side = data.get("side", "?")

        question = meta.get("question", "?")[:40]
        outcome = meta.get("side", "?")

        if price is None:
            log.debug(f"Trade event without price: {data}")
            return
        log.info(
            f"🔄 TRADE | {outcome:3} | price={price:>6} size={size or '?':>8} side={side:4} "
            f"| {question}"
        )

    def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False

    def print_summary(self) -> None:
        """Print statistics summary."""
        log.info("=" * 60)
        log.info("WEBSOCKET SESSION SUMMARY")
        log.info(f"  Total events received: {self.events_received}")
        log.info(f"  Price changes: {self.price_changes_received}")
        log.info(f"  Tokens tracked: {len(self.last_prices)}")
        log.info("=" * 60)


# =============================================================================
# MAIN
# =============================================================================


async def main(duration: int | None, sample: int) -> None:
    """Resolve tokens, then stream until Ctrl+C or --duration seconds."""
    token_map = await fetch_token_map(load_market_ids_from_portfolios())
    if not token_map:
        log.warning("No live portfolio tokens; using top-volume markets instead")
        token_map = await fetch_sample_token_map(sample)
    if not token_map:
        log.error("No token IDs resolved. Cannot proceed.")
        return

    client = PolymarketPriceStream(token_map)
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, main_task.cancel)

    try:
        if duration:
            log.info(f"Running for {duration} seconds...")
            await asyncio.wait_for(client.connect(), timeout=duration)
        else:
            log.info("Running until interrupted (Ctrl+C to stop)...")
            await client.connect()
    except asyncio.TimeoutError:
        log.info(f"Duration limit ({duration}s) reached.")
        client.stop()
    except asyncio.CancelledError:
        log.info("Shutdown signal received.")
        client.stop()

    client.print_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream CLOB prices over WebSocket")
    parser.add_argument("--duration", type=int, help="Stop after N seconds")
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_MARKETS,
        help="Top-volume markets to use when portfolios give no live tokens",
    )
    args = parser.parse_args()
    asyncio.run(main(args.duration, args.sample))
