"""
Token ID resolution and caching for Polymarket CLOB API.

Handles the mapping between market IDs and CLOB token IDs.
Token IDs are required for subscribing to the CLOB WebSocket.
"""

import json

import httpx
from loguru import logger

from core.paths import LIVE_DIR

# =============================================================================
# CONFIGURATION
# =============================================================================

GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 10.0


# =============================================================================
# TOKEN RESOLVER
# =============================================================================


class TokenResolver:
    """
    Resolves market IDs to CLOB token IDs.

    The Polymarket CLOB WebSocket requires token IDs (clobTokenIds) for
    subscription, not market IDs. This class maintains the bidirectional
    mapping and handles refresh when portfolios.json changes.
    """

    def __init__(self) -> None:
        # Token ID → metadata
        self._token_map: dict[str, dict] = {}

        # Market ID → [yes_token_id, no_token_id]
        self._market_to_tokens: dict[str, list[str]] = {}

        # File modification time for change detection
        self._portfolios_mtime: float | None = None

        self._running = False

    async def start(self) -> None:
        """Initialize token mapping on startup."""
        if self._running:
            return

        logger.info("Starting TokenResolver")
        self._running = True
        await self.refresh()

    async def stop(self) -> None:
        """Stop the resolver (no-op, stateless)."""
        logger.info("Stopping TokenResolver")
        self._running = False

    def get_token_ids(self) -> list[str]:
        """Get all known token IDs for WebSocket subscription."""
        return list(self._token_map.keys())

    def get_token_metadata(self, token_id: str) -> dict | None:
        """
        Get metadata for a token ID.

        Returns:
            {market_id, question, side, event_id} or None if unknown.
        """
        return self._token_map.get(token_id)

    def get_market_id(self, token_id: str) -> str | None:
        """Get market ID for a token (quick lookup)."""
        meta = self._token_map.get(token_id)
        return meta["market_id"] if meta else None

    def get_tokens_for_market(self, market_id: str) -> list[str]:
        """Get [yes_token, no_token] for a market."""
        return self._market_to_tokens.get(market_id, [])

    def get_all_market_tokens(self) -> dict[str, list[str]]:
        """Get all market_id → [yes_token, no_token] mappings (copy)."""
        return self._market_to_tokens.copy()

    def should_refresh(self) -> bool:
        """Check if token mapping needs refresh based on portfolios.json mtime."""
        portfolios_path = LIVE_DIR / "portfolios.json"

        # First load or no tokens
        if not self._token_map:
            return True

        # File doesn't exist
        if not portfolios_path.exists():
            return False

        # File modified
        try:
            current_mtime = portfolios_path.stat().st_mtime
            if self._portfolios_mtime is None or current_mtime > self._portfolios_mtime:
                logger.debug("portfolios.json modified, refresh needed")
                return True
        except OSError:
            pass

        return False

    async def refresh(self) -> None:
        """Reload market IDs and refetch token mapping from Gamma API."""
        market_ids = await self._load_market_ids_from_portfolios()

        if not market_ids:
            logger.warning("No market IDs found in portfolios.json")
            return

        await self._fetch_token_map(market_ids)

    async def _load_market_ids_from_portfolios(self) -> list[str]:
        """Load unique market IDs from portfolios.json."""
        portfolios_path = LIVE_DIR / "portfolios.json"

        if not portfolios_path.exists():
            logger.warning(f"portfolios.json not found at {portfolios_path}")
            return []

        try:
            # Cache file mtime
            self._portfolios_mtime = portfolios_path.stat().st_mtime

            data = json.loads(portfolios_path.read_text())
            portfolios = data.get("portfolios", []) if isinstance(data, dict) else data

            market_ids = set()
            for p in portfolios[:200]:  # Limit to top 200 portfolios
                if target := p.get("target_market_id"):
                    market_ids.add(str(target))
                if cover := p.get("cover_market_id"):
                    market_ids.add(str(cover))

            logger.info(f"Loaded {len(market_ids)} market IDs from portfolios.json")
            return list(market_ids)

        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error loading portfolios.json: {e}")
            return []

    async def _fetch_token_map(self, market_ids: list[str]) -> None:
        """Fetch clobTokenIds from Gamma API for all markets."""
        new_token_map: dict[str, dict] = {}
        new_market_to_tokens: dict[str, list[str]] = {}

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for market_id in market_ids:
                try:
                    resp = await client.get(f"{GAMMA_API_BASE_URL}/markets/{market_id}")

                    if resp.status_code != 200:
                        logger.warning(
                            f"Failed to fetch market {market_id}: {resp.status_code}"
                        )
                        continue

                    market = resp.json()
                    clob_token_ids = market.get("clobTokenIds", "[]")

                    # Parse JSON string if needed
                    if isinstance(clob_token_ids, str):
                        clob_token_ids = json.loads(clob_token_ids)

                    if not clob_token_ids or len(clob_token_ids) < 2:
                        logger.warning(
                            f"Market {market_id} has invalid clobTokenIds: {clob_token_ids}"
                        )
                        continue

                    # Get outcomes (YES/NO sides)
                    outcomes = market.get("outcomes", ["Yes", "No"])
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)

                    question = market.get("question", "")
                    event_id = str(
                        market.get("groupItemId") or market.get("groupId") or market_id
                    )

                    # Map tokens
                    yes_token = clob_token_ids[0]
                    no_token = clob_token_ids[1]

                    new_token_map[yes_token] = {
                        "market_id": market_id,
                        "question": question,
                        "side": outcomes[0] if outcomes else "Yes",
                        "event_id": event_id,
                    }
                    new_token_map[no_token] = {
                        "market_id": market_id,
                        "question": question,
                        "side": outcomes[1] if len(outcomes) > 1 else "No",
                        "event_id": event_id,
                    }

                    new_market_to_tokens[market_id] = [yes_token, no_token]

                except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Error fetching market {market_id}: {e}")
                    continue

        # Update maps
        self._token_map = new_token_map
        self._market_to_tokens = new_market_to_tokens

        logger.info(
            f"Token mapping updated: {len(new_token_map)} tokens "
            f"({len(new_market_to_tokens)} markets)"
        )


# =============================================================================
# SINGLETON
# =============================================================================

token_resolver = TokenResolver()
