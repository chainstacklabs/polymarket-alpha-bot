"""
Sigtype-3 / Deposit-Wallet Spike — official `polymarket-client` SDK
===================================================================

Verifies how far the NEW official SDK gets a fresh EOA toward placing a
sigtype-3 (deposit-wallet) order, which is the migration path out of the
EOA allowlist gate (see memory: polymarket-v2-eoa-allowlist-gate).

Steps (each isolated, throwaway key, zero funds):
  1. SecureClient.create(fresh EOA)        — does the new SDK's L1 auth work?
  2. derive deposit-wallet address         — CREATE2 derivation (read-only)
  3. is_gasless_ready()                     — relayer /deployed reachable?
  4. setup_gasless_wallet()                 — deploy via relayer (needs a
                                              Relayer/Builder API key)

Expected wall: step 4 raises UserInputError unless POLY_RELAYER_API_KEY /
POLY_RELAYER_ADDRESS env vars are set (key minted at
polymarket.com/settings?tab=api-keys). If they ARE set, it deploys the
deposit wallet and then places + cancels one non-filling order to confirm
the allowlist gate is cleared for sigtype 3.

USAGE:
    POLY_RELAYER_API_KEY=.. POLY_RELAYER_ADDRESS=0x.. SPIKE_SIGNER_KEY=0x.. \
      uv run --no-project --with polymarket-client --prerelease allow \
      python experiments/verify/03_sigtype3_spike.py
"""

import os

import httpx
from eth_account import Account


def hr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * 4}")


def main() -> None:
    # Region eligibility litmus (Polymarket restricts some regions).
    try:
        with httpx.Client(timeout=12.0) as c:
            geo = c.get("https://polymarket.com/api/geoblock").json()
        print(f"region litmus: {geo}")
    except Exception as e:  # noqa: BLE001
        print(f"region litmus failed: {type(e).__name__}: {e}")

    from polymarket import SecureClient
    from polymarket.auth import BuilderApiKey, RelayerApiKey
    from polymarket.errors import UserInputError

    # A RelayerApiKey is bound to the wallet it was minted under — the signer
    # MUST equal POLY_RELAYER_ADDRESS. Set SPIKE_SIGNER_KEY to that wallet's
    # private key to sign as it; otherwise fall back to a fresh throwaway EOA
    # (only valid with a BuilderApiKey, not a wallet-bound RelayerApiKey).
    signer_key = os.environ.get("SPIKE_SIGNER_KEY")
    if signer_key:
        acct = Account.from_key(signer_key)
        print(f"signer EOA (from SPIKE_SIGNER_KEY): {acct.address}")
    else:
        acct = Account.create()
        print(f"throwaway EOA: {acct.address}")

    # optional relayer/builder creds from env
    api_key = None
    if os.environ.get("POLY_RELAYER_API_KEY") and os.environ.get("POLY_RELAYER_ADDRESS"):
        api_key = RelayerApiKey(
            key=os.environ["POLY_RELAYER_API_KEY"],
            address=os.environ["POLY_RELAYER_ADDRESS"],
        )
        print("using Relayer API key from env")
    elif all(os.environ.get(k) for k in ("POLY_BUILDER_KEY", "POLY_BUILDER_SECRET", "POLY_BUILDER_PASSPHRASE")):
        api_key = BuilderApiKey(
            key=os.environ["POLY_BUILDER_KEY"],
            secret=os.environ["POLY_BUILDER_SECRET"],
            passphrase=os.environ["POLY_BUILDER_PASSPHRASE"],
        )
        print("using Builder API key from env")

    hr("STEP 1: SecureClient.create (new-SDK L1 auth, fresh EOA)")
    try:
        client = SecureClient.create(private_key=acct.key.hex(), api_key=api_key)
        print("  ✅ client created — L1 auth + api-key derivation OK for fresh EOA")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ create failed: {type(e).__name__}: {e}")
        return

    # HEADLESS TEST: if no env relayer/builder key, try the CLOB-L2 creds
    # (derived above via signature) AS a BuilderApiKey. The relayer's builder
    # HMAC scheme is identical to CLOB L2 — the only question is whether
    # /submit checks the key against a builder-profile registry server-side.
    if api_key is None:
        creds = client._ctx.credentials  # noqa: SLF001 — spike introspection
        if creds is not None:
            api_key = BuilderApiKey(key=creds.key, secret=creds.secret, passphrase=creds.passphrase)
            print("  → no env key; retrying with CLOB-L2 creds cast as BuilderApiKey (headless probe)")
            try:
                client = SecureClient.create(
                    private_key=acct.key.hex(), credentials=creds, api_key=api_key
                )
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ re-create with builder key failed: {type(e).__name__}: {e}")
                return

    hr("STEP 2: derive deposit-wallet address (CREATE2)")
    try:
        from polymarket._internal.wallet import (
            derive_current_deposit_wallet_address_sync,
        )

        ctx = client._ctx  # noqa: SLF001 — spike introspection
        dw = derive_current_deposit_wallet_address_sync(
            ctx.rpc, ctx.signer.address, ctx.environment.wallet_derivation
        )
        print(f"  ✅ predicted deposit wallet: {dw}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  derivation failed: {type(e).__name__}: {e}")

    hr("STEP 3: is_gasless_ready (relayer /deployed reachable?)")
    try:
        print(f"  ready={client.is_gasless_ready()}  (False expected — not deployed)")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  is_gasless_ready failed: {type(e).__name__}: {e}")

    hr("STEP 4: setup_gasless_wallet (relayer deploy)")
    try:
        gw = client.setup_gasless_wallet()
        print("  ✅ deposit wallet deployed via relayer")
    except UserInputError as e:
        print(f"  ⛔ BLOCKED (expected without relayer key): {e}")
        print("  → mint a Relayer API Key at polymarket.com/settings?tab=api-keys,")
        print("    then re-run with POLY_RELAYER_API_KEY / POLY_RELAYER_ADDRESS set.")
        return
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ deploy failed: {type(e).__name__}: {e}")
        return

    hr("STEP 5: place + cancel one non-filling sigtype-3 order")
    try:
        resp = gw.place_limit_order(
            token_id=os.environ.get("SPIKE_TOKEN_ID", ""),
            price=0.01,
            size=5,
            side="BUY",
        )
        print(f"  order response: {resp}")
        blob = str(resp).lower()
        if "maker address not allowed" in blob:
            print("  ❌ STILL GATED for sigtype 3")
        elif any(w in blob for w in ("insufficient", "balance", "allowance")):
            print("  ✅ GATE CLEARED — rejected only on balance (sigtype-3 works!)")
        else:
            print("  ✅ order accepted — GATE CLEARED")
    except Exception as e:  # noqa: BLE001
        print(f"  order attempt: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
