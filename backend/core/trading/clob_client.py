"""Shared CLOB client initialization with proxy support."""

import os
from typing import Optional

import httpx
from loguru import logger

from core.wallet.manager import WalletManager


def get_clob_client(wallet: WalletManager) -> Optional[object]:
    """Initialize CLOB client with optional proxy support. Returns None on failure."""
    try:
        from py_clob_client.client import ClobClient
        import py_clob_client.http_helpers.helpers as clob_helpers
    except ImportError:
        logger.error("py-clob-client not installed")
        return None

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        from urllib.parse import urlsplit

        parts = urlsplit(proxy)
        safe = (
            f"{parts.scheme}://{parts.hostname}:{parts.port}"
            if parts.port
            else f"{parts.scheme}://{parts.hostname}"
        )
        logger.info(f"Using proxy: {safe}")
        clob_helpers._http_client = httpx.Client(http2=True, proxy=proxy, timeout=30.0)

    try:
        private_key = wallet.get_unlocked_key()
        address = wallet.address
        if not address:
            logger.error("Wallet address is not set")
            return None
        client = ClobClient(
            "https://clob.polymarket.com",
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
