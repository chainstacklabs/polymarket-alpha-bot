"""Process-wide feature flags for the Polymarket V1 -> V2 cutover.

Centralizes env-flag parsing so collateral plumbing, CLOB clients, and any
future V2 paths read the same source of truth. The single helper here is
re-exported from ``core.trading.clob_client`` for backward compatibility.
"""

import os


def v2_enabled() -> bool:
    """Read the ``POLYMARKET_V2_ENABLED`` flag. Defaults to False (V1)."""
    return os.environ.get("POLYMARKET_V2_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
