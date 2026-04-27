"""Process-wide feature flags for the Polymarket V1 -> V2 cutover.

Centralizes env-flag parsing so collateral plumbing, CLOB clients, and any
future V2 paths read the same source of truth. The single helper here is
re-exported from ``core.trading.clob_client`` for backward compatibility.
"""

import os


def v2_enabled() -> bool:
    """Read the ``POLYMARKET_V2_ENABLED`` flag.

    Defaults to **True** post-2026-04-28 cutover — Polymarket V1 endpoints
    stopped accepting orders. Users on the legacy V1 stack must explicitly
    set ``POLYMARKET_V2_ENABLED=false``. The V1 code path will be removed in
    v2.0 (~2026-05-05); ``v1-final`` tag is the permanent legacy reference.
    """
    return os.environ.get("POLYMARKET_V2_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
