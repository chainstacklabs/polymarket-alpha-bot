"""
Buy a Position via Deposit Wallet (sigtype 3)
=============================================

Standalone demo of the production buy flow (backend/core/trading/executor.py):
    1. Split pUSD into YES + NO (gasless relayer batch)
    2. Sell the unwanted side with a FAK order and a price floor
    3. Result: you hold the wanted side

The deposit wallet must already hold pUSD (>= AMOUNT). Polymarket's minimums
apply to the sale: the market's `orderMinSize` shares (usually 5) and $1.
Without --yes it only prints the plan.

USAGE (from the repo root):
    POLY_PK=0x.. POLY_RELAYER_API_KEY=.. POLY_RELAYER_ADDRESS=0x.. \
    MARKET_ID=.. SIDE=YES AMOUNT=5 \
      uv run --frozen --project backend experiments/trading/03_buy_position.py [--yes]
"""

import json
import math
import os
import sys

import httpx
from polymarket import SecureClient
from polymarket.auth import RelayerApiKey

MIN_ORDER_USD = 1.0
DEFAULT_MIN_SHARES = 5.0
SLIPPAGE = 0.10  # sell no lower than 10% under the current price


def sell_floor(price: float, tick: float = 0.01) -> float:
    """Worst accepted price: SLIPPAGE under `price`, rounded up to a tick."""
    # round() first so float noise (0.45 / 0.01 = 45.000000001) doesn't add a tick
    ticks = math.ceil(round(price * (1 - SLIPPAGE) / tick, 6))
    return round(max(ticks, 1) * tick, 4)


def main() -> None:
    market_id = os.environ["MARKET_ID"]
    side = os.environ.get("SIDE", "YES").upper()
    amount = float(os.environ.get("AMOUNT", "5"))
    execute = "--yes" in sys.argv

    m = httpx.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=30).json()
    # Trading IDs by protocol: v1 clobTokenIds (JSON string), v2 positionIds
    # (list). The SDK routes split + order signing for both.
    if m.get("version") == "v2":
        tokens = m["positionIds"]
    elif m.get("version") == "v1":
        tokens = json.loads(m["clobTokenIds"])
    else:
        raise SystemExit(f"Market {market_id}: unsupported protocol {m.get('version')!r}")
    prices = json.loads(m["outcomePrices"])
    yes, no = tokens[0], tokens[1]
    unwanted = no if side == "YES" else yes
    unwanted_price = float(prices[1] if side == "YES" else prices[0])
    min_shares = float(m.get("orderMinSize") or DEFAULT_MIN_SHARES)
    floor = sell_floor(unwanted_price)

    # A split of $N mints N shares of each side; sell the unwanted N.
    sell = amount >= min_shares and amount * floor >= MIN_ORDER_USD
    print(f"market:  {m.get('question')} ({m.get('version')})")
    print(f"split:   ${amount:g} pUSD -> {amount:g} YES + {amount:g} NO")
    if sell:
        print(f"sell:    {amount:g} {'NO' if side == 'YES' else 'YES'} at >= {floor} (now {unwanted_price})")
    else:
        print(f"sell:    skipped (below {min_shares:g} shares or ${MIN_ORDER_USD:g}); you keep both sides")
    if not execute:
        print("\nPreview only. Re-run with --yes to spend.")
        return

    client = SecureClient.create(
        private_key=os.environ["POLY_PK"],
        api_key=RelayerApiKey(
            key=os.environ["POLY_RELAYER_API_KEY"],
            address=os.environ["POLY_RELAYER_ADDRESS"],
        ),
    )
    print(f"\ndeposit wallet: {client.wallet}")

    handle = client.split_position(condition_id=m["conditionId"], amount=int(amount * 1e6))
    print(f"split tx: {handle.wait().transaction_hash}")

    if sell:
        resp = client.place_market_order(
            asset_id=unwanted, side="SELL", shares=amount, min_price=floor, order_type="FAK"
        )
        if getattr(resp, "ok", False):
            # For a SELL: making_amount = shares sold, taking_amount = pUSD received
            print(
                f"sold {float(resp.making_amount):g} of {amount:g} for "
                f"${float(resp.taking_amount):.2f} (order {resp.order_id})"
            )
        else:
            print(f"sell rejected: {getattr(resp, 'message', resp)} (you hold both sides)")

    print(f"done: holding {side}")


if __name__ == "__main__":
    main()
