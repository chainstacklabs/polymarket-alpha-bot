"""
Wallet Setup for Deposit-Wallet Trading (sigtype 3)
===================================================

Shows the current production onboarding: an EOA (the signing key) maps to a
deterministic deposit wallet that is deployed gaslessly on
``SecureClient.create()``. Fund the DEPOSIT WALLET with pUSD — that is your
trading balance. The relayer pays gas, so you never need POL.

Each user mints their own Relayer API key for their EOA at
polymarket.com/settings?tab=api-keys (decision B). Approvals are best-effort:
the relayer may reject some operators, and split/merge/orders recover
allowances on demand.

USAGE:
    POLY_PK=0x.. POLY_RELAYER_API_KEY=.. POLY_RELAYER_ADDRESS=0x.. \
      uv run --no-project --with polymarket-client --with eth-account \
      --prerelease allow python experiments/trading/01_setup_wallet.py
"""

import os

from eth_account import Account
from polymarket import SecureClient
from polymarket.auth import RelayerApiKey


def main() -> None:
    pk = os.environ.get("POLY_PK")
    if not pk:
        acct = Account.create()
        pk = acct.key.hex()
        print(f"generated EOA: {acct.address}")
        print(f"private key (save securely!): {pk}")

    client = SecureClient.create(
        private_key=pk,
        api_key=RelayerApiKey(
            key=os.environ["POLY_RELAYER_API_KEY"],
            address=os.environ["POLY_RELAYER_ADDRESS"],
        ),
    )
    print(f"signer EOA:                 {client.signer}")
    print(f"deposit wallet (fund this): {client.wallet}")

    try:
        client.setup_trading_approvals()
        print("trading approvals: set")
    except Exception as e:  # noqa: BLE001
        print(f"trading approvals: skipped (allowances self-recover) — {e}")


if __name__ == "__main__":
    main()
