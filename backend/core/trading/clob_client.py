"""CLOB V2 client initialization with proxy support.

Polymarket V1 endpoints stopped accepting orders at the 2026-04-28 cutover.
The bot uses ``py_clob_client_v2`` exclusively (pUSD collateral, EIP-712
domain version "2", server-side fee computation at match time).
"""

import os
from typing import Optional

import httpx
from loguru import logger

from core.wallet.manager import WalletManager

CLOB_URL = "https://clob.polymarket.com"


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


def get_clob_client(wallet: WalletManager) -> Optional[object]:
    """Initialize V2 CLOB client. Returns None on failure."""
    try:
        from py_clob_client_v2.client import ClobClient
        import py_clob_client_v2.http_helpers.helpers as clob_helpers
    except ImportError:
        logger.error("py-clob-client-v2 not installed")
        return None

    _apply_proxy(clob_helpers)

    try:
        private_key = wallet.get_unlocked_key()
        address = wallet.address
        if not address:
            logger.error("Wallet address is not set")
            return None
        # V2 keeps `chain_id` (still int 137 for Polygon) and handles EIP-712
        # domain version "2" internally. `builder_config=None` per #27 scope.
        client = ClobClient(
            host=CLOB_URL,
            chain_id=137,
            key=private_key,
            signature_type=0,
            funder=address,
            builder_config=None,
        )
        # `derive_api_key` is idempotent for an existing key; the V2 SDK
        # raises `PolyApiException` when the address has no key yet — narrow
        # the catch so transient network errors fail visibly rather than
        # silently creating orphan keys.
        from py_clob_client_v2.exceptions import PolyApiException

        try:
            creds = client.derive_api_key()
        except PolyApiException:
            creds = client.create_api_key()
        client.set_api_creds(creds)
        return client
    except Exception as e:
        logger.error(f"CLOB API error: {e}")
        return None
