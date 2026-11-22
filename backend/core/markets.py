"""Market-level helpers shared by the pipeline, poller and trade executor.

Kept dependency-free so trading code can import it without pulling in the
pipeline steps (which require LLM env vars at import time).
"""

from typing import Any

# Gamma tags every market with the protocol it trades on. "v1" = CTF markets
# (clobTokenIds, CTF Exchange V2, domain "2") — the only kind this bot can
# trade. "v2" = Polymarket Protocol V2 (positionIds, ExchangeV3, domain "3"),
# rolling out from 2026-11-23. Per the migration guide, missing or unknown
# versions are rejected too:
# https://docs.polymarket.com/migrate/polymarket-v2/api-integrations.md
SUPPORTED_MARKET_VERSION = "v1"


def is_supported_version(market: dict[str, Any]) -> bool:
    """True if the market trades on the protocol this bot supports (CTF / v1)."""
    return market.get("version") == SUPPORTED_MARKET_VERSION
