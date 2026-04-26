"""Shared CLOB client initialization with proxy support.

Both V1 (current prod) and V2 (post-cutover) clients live here so we can flip
between them with a single env flag during the 2026-04-28 cutover. The V2
init is parallel and unused by production call sites until issue #36 wires it
in. Set ``POLYMARKET_V2_ENABLED=true`` to opt in.
"""

import os
from typing import Optional

import httpx
from loguru import logger

from core.wallet.manager import WalletManager

# Pre-cutover V2 endpoint; becomes the prod CLOB at 2026-04-28 ~11:00 UTC.
CLOB_V2_URL = "https://clob-v2.polymarket.com"
CLOB_V1_URL = "https://clob.polymarket.com"


def _v2_enabled() -> bool:
    """Read the V2 feature flag. Defaults to False (V1 stays in charge)."""
    return os.environ.get("POLYMARKET_V2_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _apply_proxy(clob_helpers_module) -> None:
    """Wire HTTPS_PROXY/HTTP_PROXY into the clob client's shared http client."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy:
        return
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    safe = (
        f"{parts.scheme}://{parts.hostname}:{parts.port}"
        if parts.port
        else f"{parts.scheme}://{parts.hostname}"
    )
    logger.info(f"Using proxy: {safe}")
    clob_helpers_module._http_client = httpx.Client(
        http2=True, proxy=proxy, timeout=30.0
    )


def _get_clob_client_v1(wallet: WalletManager) -> Optional[object]:
    """Initialize legacy V1 CLOB client (USDC.e collateral)."""
    try:
        from py_clob_client.client import ClobClient
        import py_clob_client.http_helpers.helpers as clob_helpers
    except ImportError:
        logger.error("py-clob-client not installed")
        return None

    _apply_proxy(clob_helpers)

    try:
        private_key = wallet.get_unlocked_key()
        address = wallet.address
        if not address:
            logger.error("Wallet address is not set")
            return None
        client = ClobClient(
            CLOB_V1_URL,
            key=private_key,
            chain_id=137,
            signature_type=0,
            funder=address,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client
    except Exception as e:
        logger.error(f"CLOB API error: {e}")
        return None


def _get_clob_client_v2(wallet: WalletManager) -> Optional[object]:
    """Initialize V2 CLOB client (pUSD collateral, EIP-712 domain version "2").

    Constructed with the V2 options-object signature and ``chain="polygon"``.
    No production call site reaches this yet — it ships behind the
    ``POLYMARKET_V2_ENABLED`` flag for the 2026-04-28 cutover (#36 flips it).

    Manual smoke test for the signing path (no submission)::

        cd backend && POLYMARKET_V2_ENABLED=true uv run python -c "
        from core.wallet.manager import WalletManager
        from core.trading.clob_client import get_clob_client
        w = WalletManager(); w.unlock(input('passphrase: '))
        c = get_clob_client(w)
        print('client:', c)
        # Build (do not post) an order to exercise EIP-712 v2 signing:
        # from py_clob_client_v2.clob_types import OrderArgs
        # args = OrderArgs(price=0.5, size=5, side='BUY', token_id='<id>')
        # print(c.create_order(args))
        "

    The "do not post" rule: stop after ``create_order``; never call
    ``post_order``. We just want to verify the EIP-712 v2 domain signs cleanly
    against ``clob-v2.polymarket.com``.
    """
    try:
        from py_clob_client_v2.client import ClobClient as ClobClientV2
        import py_clob_client_v2.http_helpers.helpers as clob_helpers_v2
    except ImportError:
        logger.error("py-clob-client-v2 not installed (POLYMARKET_V2_ENABLED set)")
        return None

    _apply_proxy(clob_helpers_v2)

    try:
        private_key = wallet.get_unlocked_key()
        address = wallet.address
        if not address:
            logger.error("Wallet address is not set")
            return None
        # V2 takes an options object: chain="polygon", EIP-712 domain version "2".
        # No builder code (#27 leaves it null; revisit later).
        client = ClobClientV2(
            host=CLOB_V2_URL,
            key=private_key,
            chain="polygon",
            signature_type=0,
            funder=address,
            signature_version="2",
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client
    except Exception as e:
        logger.error(f"CLOB V2 API error: {e}")
        return None


def get_clob_client(wallet: WalletManager) -> Optional[object]:
    """Initialize CLOB client. Picks V2 if ``POLYMARKET_V2_ENABLED`` is set.

    Default is V1 — the V2 path is dormant until the 2026-04-28 cutover.
    Returns None on failure (missing dep, missing wallet, API error).
    """
    if _v2_enabled():
        logger.info("POLYMARKET_V2_ENABLED set — using V2 CLOB client")
        return _get_clob_client_v2(wallet)
    return _get_clob_client_v1(wallet)
