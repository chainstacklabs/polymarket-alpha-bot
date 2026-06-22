"""
EOA Allowlist Probe — Polymarket V2 CLOB
========================================

VERIFIES: as of 2026-05, py-clob-client-v2 issues #51/#53/#54/#56/#61/#74 report
that the V2 CLOB API rejects orders from EOAs that have no prior CLOB history
with HTTP 400 'maker address not allowed, please use the deposit wallet flow'.
The on-chain CTF Exchange V2 still validates SignatureType.EOA permissionlessly;
the gate is enforced server-side at clob.polymarket.com.

WHAT IT DOES:
    Probes Polymarket's CLOB by posting a deliberately-non-filling limit BUY
    (price 0.01 × size 5 = $0.05 max exposure) and capturing the response.

    --fresh    (default) Generates a throwaway key with Account.create() — zero
               funding, zero secrets. Expects HTTP 400 with the "maker address
               not allowed" string. Confirms the gate exists for our SDK.

    --operator Probes the existing experiment wallet at
               experiments/trading/.wallet.local.json. Tells us whether our
               operator address is on Polymarket's grandfathered allowlist.

ABSENCE OF "maker address not allowed" + accepted order  → wallet is grandfathered
PRESENCE of "maker address not allowed"                   → wallet is gated
"insufficient balance/collateral"                         → grandfathered (no pUSD)

USAGE:
    cd backend && uv run python ../experiments/verify/01_eoa_allowlist_probe.py
    cd backend && uv run python ../experiments/verify/01_eoa_allowlist_probe.py --operator
    cd backend && uv run python ../experiments/verify/01_eoa_allowlist_probe.py --fresh --operator
"""

import argparse
import json
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from eth_account import Account

PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

OPERATOR_WALLET_PATH = PROJECT_ROOT / "experiments" / "trading" / ".wallet.local.json"
GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
LOG_DIR = Path(__file__).parent / ".probe_logs"


def pick_liquid_token_id() -> tuple[str, dict]:
    """Fetch a high-volume active market and return its first CLOB token id."""
    r = httpx.get(
        f"{GAMMA_URL}/markets/keyset",
        params={
            "closed": "false",
            "active": "true",
            "order": "volume24hr",
            "ascending": "false",
            "limit": "20",
        },
        timeout=15.0,
    )
    r.raise_for_status()
    payload = r.json()
    markets = payload.get("markets") or payload.get("data") or []
    for m in markets:
        if not m.get("enableOrderBook"):
            continue
        ids_raw = m.get("clobTokenIds")
        if not ids_raw:
            continue
        try:
            ids = json.loads(ids_raw) if isinstance(ids_raw, str) else ids_raw
        except json.JSONDecodeError:
            continue
        if ids and ids[0]:
            return ids[0], {
                "slug": m.get("slug"),
                "question": m.get("question"),
                "tick_size": m.get("orderPriceMinTickSize"),
            }
    raise RuntimeError("No CLOB-enabled liquid market found")


def _apply_proxy() -> None:
    """Route the SDK's shared http client through HTTPS_PROXY/HTTP_PROXY.

    Mirrors core.trading.clob_client._apply_proxy — the env var alone isn't
    enough because the SDK holds a module-level client. Needed to reach the
    geoblocked CLOB from a blocked region via an allowed-region proxy.
    """
    import os

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy:
        return
    import py_clob_client_v2.http_helpers.helpers as clob_helpers

    clob_helpers._http_client = httpx.Client(http2=True, proxy=proxy, timeout=30.0)
    print(f"  (routing CLOB via proxy {proxy})")


def init_client(private_key: str, address: str):
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.exceptions import PolyApiException

    _apply_proxy()

    client = ClobClient(
        host=CLOB_URL,
        chain_id=137,
        key=private_key,
        signature_type=0,
        funder=address,
        builder_config=None,
    )
    try:
        creds = client.derive_api_key()
        derive_outcome = "derive_api_key OK"
    except PolyApiException as e:
        try:
            creds = client.create_api_key()
            derive_outcome = f"derive failed ({e}); create_api_key OK"
        except PolyApiException as e2:
            return None, f"derive AND create_api_key failed: derive={e} create={e2}"
    client.set_api_creds(creds)
    return client, derive_outcome


def probe(label: str, private_key: str, address: str, token_id: str, market: dict) -> dict:
    print(f"\n=== Probing: {label} ({address}) ===")
    client, auth_status = init_client(private_key, address)
    if client is None:
        return {"label": label, "address": address, "auth_status": auth_status, "verdict": "AUTH_FAILURE"}
    print(f"  auth: {auth_status}")

    from py_clob_client_v2.clob_types import OrderArgs, OrderType

    tick = float(market.get("tick_size") or 0.01)
    price = max(tick, 0.01)

    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=5,
        side="BUY",
    )

    result_dict = None
    exception_repr = None
    response_body = None
    try:
        signed = client.create_order(order_args)
        result_dict = client.post_order(signed, OrderType.GTC)
    except Exception as e:
        exception_repr = f"{type(e).__name__}: {e}"
        for attr in ("response_body", "body", "error_msg", "errorMsg", "msg"):
            v = getattr(e, attr, None)
            if v:
                response_body = str(v)
                break

    print(f"  post_order result: {result_dict}")
    if exception_repr:
        print(f"  post_order EXCEPTION: {exception_repr}")
    if response_body:
        print(f"  response body: {response_body}")

    verdict = classify(result_dict, exception_repr, response_body)
    print(f"  ==> VERDICT: {verdict}")

    if result_dict and result_dict.get("success") and result_dict.get("orderID"):
        order_id = result_dict["orderID"]
        print(f"  cancelling {order_id} ...")
        try:
            cancel_resp = client.cancel(order_id=order_id)
            print(f"  cancel: {cancel_resp}")
        except Exception as e:
            print(f"  cancel failed: {e}")

    return {
        "label": label,
        "address": address,
        "auth_status": auth_status,
        "post_result": result_dict,
        "exception": exception_repr,
        "response_body": response_body,
        "verdict": verdict,
    }


def classify(result: dict | None, exc: str | None, body: str | None) -> str:
    blob = " ".join(filter(None, [
        json.dumps(result) if result else "",
        exc or "",
        body or "",
    ])).lower()

    if "maker address not allowed" in blob or "deposit wallet flow" in blob:
        return "GATED — EOA blocked by CLOB allowlist"
    if "insufficient" in blob or "balance" in blob or "allowance" in blob or "collateral" in blob:
        return "PASSED GATE (rejected on balance/allowance — wallet is on allowlist)"
    if result and result.get("success") and result.get("orderID"):
        return "PASSED GATE (order accepted — wallet is on allowlist)"
    if exc and "403" in exc:
        return "BLOCKED at network layer (geo or proxy) — inconclusive"
    return "INCONCLUSIVE — inspect response above"


def load_operator() -> tuple[str, str]:
    if not OPERATOR_WALLET_PATH.exists():
        raise FileNotFoundError(f"Operator wallet not found at {OPERATOR_WALLET_PATH}")
    d = json.loads(OPERATOR_WALLET_PATH.read_text())
    pk = d.get("private_key")
    addr = d.get("address")
    if not pk or not addr:
        raise ValueError("Operator wallet file missing private_key or address")
    return pk, addr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="Run fresh-EOA probe (default)")
    parser.add_argument("--operator", action="store_true", help="Also probe operator wallet")
    args = parser.parse_args()
    if not args.fresh and not args.operator:
        args.fresh = True

    LOG_DIR.mkdir(exist_ok=True)
    print(f"Fetching liquid market token from Gamma...")
    token_id, market = pick_liquid_token_id()
    print(f"  market: {market.get('slug')} — {market.get('question')!r}")
    print(f"  token_id: {token_id}")
    print(f"  tick_size: {market.get('tick_size')}")

    results = []

    if args.fresh:
        acct = Account.create()
        results.append(probe(
            "FRESH EOA (throwaway)",
            acct.key.hex(),
            acct.address,
            token_id,
            market,
        ))

    if args.operator:
        try:
            pk, addr = load_operator()
            results.append(probe(
                "OPERATOR (experiment wallet)",
                pk,
                addr,
                token_id,
                market,
            ))
        except (FileNotFoundError, ValueError) as e:
            print(f"\n[skipped operator probe: {e}]")

    log_path = LOG_DIR / f"probe_{int(time.time())}.json"
    log_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {log_path}")

    print("\n=== Summary ===")
    for r in results:
        print(f"  {r['label']:32s} {r['verdict']}")


if __name__ == "__main__":
    main()
