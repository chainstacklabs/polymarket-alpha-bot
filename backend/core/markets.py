"""Market-level helpers shared by the pipeline, poller, trading and positions.

Kept dependency-free so trading code can import it without pulling in the
pipeline steps (which require LLM env vars at import time).
"""

import json
from typing import Any

# Gamma tags every market with the protocol it trades on:
# - "v1": CTF markets. Trading IDs = `clobTokenIds` (JSON-encoded string),
#   balances on the CTF, orders on the CTF / NegRisk exchanges (domain "2").
# - "v2": Polymarket Protocol V2 (from 2026-11-23). Trading IDs =
#   `positionIds` (list), balances on the PositionManager, orders on
#   ExchangeV3 (domain "3"), split/merge/redeem via the Router.
# The polymarket-client SDK routes orders and split/merge by itself; this bot
# only has to pick the right IDs and balance ledger. Missing or unknown
# versions are rejected, per the migration guide:
# https://docs.polymarket.com/migrate/polymarket-v2/api-integrations.md
SUPPORTED_MARKET_VERSIONS = ("v1", "v2")


def market_version(market: dict[str, Any]) -> str | None:
    version = market.get("version")
    return version if version in SUPPORTED_MARKET_VERSIONS else None


def is_supported_version(market: dict[str, Any]) -> bool:
    """True if the market trades on a protocol this bot supports."""
    return market_version(market) is not None


def outcome_token_ids(market: dict[str, Any]) -> list[str]:
    """Trading IDs [YES, NO] for a Gamma market, chosen by its `version`.

    Gamma also sends `clobTokenIds` on v2 markets; the field is chosen by
    version even when both are present. Unsupported versions give [].
    """
    version = market_version(market)
    if version == "v2":
        raw: Any = market.get("positionIds") or []
    elif version == "v1":
        raw = market.get("clobTokenIds") or "[]"
    else:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [str(i) for i in raw if str(i).isdigit()]
