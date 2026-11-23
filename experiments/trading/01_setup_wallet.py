"""
Wallet Setup for Deposit-Wallet Trading (sigtype 3)
===================================================

Shows the current production onboarding: an EOA (the signing key) maps to a
deterministic deposit wallet that is deployed gaslessly on
``SecureClient.create()``. Fund the DEPOSIT WALLET with pUSD — that is your
trading balance. The relayer pays gas, so you never need POL.

Each user mints their own Relayer API key for their EOA at
polymarket.com/settings?tab=api-keys (decision B). Trading approvals go out
in batches of 4: the relayer rejects the SDK's single 17-call batch from
``setup_trading_approvals()`` (Cloudflare 400). Re-running skips approvals
already granted.

USAGE (from the repo root):
    POLY_PK=0x.. POLY_RELAYER_API_KEY=.. POLY_RELAYER_ADDRESS=0x.. \
      uv run --frozen --project backend experiments/trading/01_setup_wallet.py

POLY_PK must be an existing key: the Relayer API key has to be minted for its
address on polymarket.com first, so a freshly generated key can't be used.
"""

import os

from polymarket import SecureClient
from polymarket.auth import RelayerApiKey
from polymarket.calls import erc20_approval_call, erc1155_set_approval_for_all_call

BATCH_SIZE = 4  # the relayer rejected all 17 approvals in one batch


def set_trading_approvals(client: SecureClient) -> int:
    """Send the missing trading approvals in batches; returns how many."""
    missing = client.get_trading_approvals_state().missing
    calls = [
        erc20_approval_call(
            token_address=a.token_address, spender=a.spender, amount=a.amount
        )
        for a in missing.erc20
    ] + [
        erc1155_set_approval_for_all_call(
            token_address=a.token_address, operator=a.operator, approved=True
        )
        for a in missing.erc1155
    ]
    for i in range(0, len(calls), BATCH_SIZE):
        handle = client.execute_transaction(
            calls=calls[i : i + BATCH_SIZE], metadata="Trading setup approvals"
        )
        last = min(i + BATCH_SIZE, len(calls))
        print(f"  approvals {i + 1}-{last}: {handle.wait().transaction_hash}")
    return len(calls)


def main() -> None:
    pk = os.environ.get("POLY_PK")
    if not pk:
        raise SystemExit("Set POLY_PK (the signing key your Relayer API key was minted for)")

    client = SecureClient.create(
        private_key=pk,
        api_key=RelayerApiKey(
            key=os.environ["POLY_RELAYER_API_KEY"],
            address=os.environ["POLY_RELAYER_ADDRESS"],
        ),
    )
    print(f"signer EOA:                 {client.signer}")
    print(f"deposit wallet (fund this): {client.wallet}")

    sent = set_trading_approvals(client)
    print(f"trading approvals: {'set ' + str(sent) if sent else 'already in place'}")


if __name__ == "__main__":
    main()
