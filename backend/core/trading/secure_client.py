"""Deposit-wallet (sigtype-3) client via the official ``polymarket-client`` SDK.

Replaces the legacy sigtype-0 EOA path (`clob_client.py`) for new users gated
by the V2 CLOB allowlist. Each user supplies their own EOA key + a Relayer API
Key minted for that same EOA (decision B). The SDK handles the deposit-wallet
deploy, ERC-1271 (POLY_1271) order signing, and gasless relayer batches for
split/merge/redeem — we just orchestrate it.

Flow proven end-to-end in ``experiments/verify/03_sigtype3_spike.py``.
"""

from typing import Optional

from loguru import logger

from core.wallet.manager import WalletManager


def get_secure_client(wallet: WalletManager) -> Optional[object]:
    """Build an authenticated SecureClient for the unlocked wallet.

    Returns None if the wallet is locked, has no relayer credentials, or the
    SDK fails to authenticate. The returned client acts for the signer's
    deposit wallet (sigtype 3) — SecureClient.create defaults ``wallet`` to the
    signer's derived deposit-wallet address.
    """
    try:
        from polymarket import SecureClient
        from polymarket.auth import RelayerApiKey
    except ImportError:
        logger.error("polymarket-client not installed")
        return None

    try:
        eoa_key = wallet.get_unlocked_key()
        relayer_key = wallet.get_relayer_api_key()
        if not relayer_key:
            logger.error("No relayer API key set — cannot trade via deposit wallet")
            return None
        # Per decision B the relayer key is minted for the user's own EOA, so
        # the relayer address is the EOA address (fall back to it if unset).
        relayer_address = wallet.relayer_address or wallet.address
        if not relayer_address:
            logger.error("Wallet address is not set")
            return None

        return SecureClient.create(
            private_key=eoa_key,
            api_key=RelayerApiKey(key=relayer_key, address=relayer_address),
        )
    except Exception as e:
        logger.error(f"SecureClient init error: {e}")
        return None
