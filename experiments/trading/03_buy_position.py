"""
Buy a Position via Deposit Wallet (sigtype 3)
=============================================

Standalone demo of the current production buy flow:
    1. Split pUSD into YES + NO (gasless relayer batch)
    2. Sell the unwanted side via a sigtype-3 FAK order (ERC-1271 / POLY_1271)
    3. Result: you hold only the wanted side

The deposit wallet is deployed automatically on ``SecureClient.create()`` and
must already hold pUSD (>= the trade amount). Polymarket enforces a $1 minimum
order size, so the unwanted side is only sold when its value clears $1.

USAGE:
    POLY_PK=0x.. POLY_RELAYER_API_KEY=.. POLY_RELAYER_ADDRESS=0x.. \
    MARKET_ID=.. SIDE=YES AMOUNT=2 \
      uv run --no-project --with polymarket-client --with httpx \
      --prerelease allow python experiments/trading/03_buy_position.py
"""

import json
import os

import httpx
from polymarket import SecureClient
from polymarket.auth import RelayerApiKey

MIN_ORDER_USD = 1.0


def main() -> None:
    market_id = os.environ["MARKET_ID"]
    side = os.environ.get("SIDE", "YES").upper()
    amount = float(os.environ.get("AMOUNT", "2"))

    m = httpx.get(
        f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=30
    ).json()
    tokens = json.loads(m["clobTokenIds"])
    prices = json.loads(m["outcomePrices"])
    condition_id = m["conditionId"]
    yes, no = tokens[0], tokens[1]
    unwanted = no if side == "YES" else yes
    unwanted_price = float(prices[1] if side == "YES" else prices[0])

    client = SecureClient.create(
        private_key=os.environ["POLY_PK"],
        api_key=RelayerApiKey(
            key=os.environ["POLY_RELAYER_API_KEY"],
            address=os.environ["POLY_RELAYER_ADDRESS"],
        ),
    )
    print(f"deposit wallet: {client.wallet}")

    # 1. Split (gasless relayer batch)
    handle = client.split_position(condition_id=condition_id, amount=int(amount * 1e6))
    print(f"split tx: {handle.wait().transaction_hash}")

    # 2. Sell unwanted side (sigtype-3 FAK), respecting the $1 minimum
    unwanted_value = amount * unwanted_price
    if unwanted_value >= MIN_ORDER_USD:
        resp = client.place_market_order(
            token_id=unwanted,
            side="SELL",
            shares=amount,
            min_price=round(unwanted_price * 0.9, 2),
            order_type="FAK",
        )
        ok = getattr(resp, "ok", False)
        print(f"sell unwanted: {'filled ' + resp.order_id if ok else getattr(resp, 'message', resp)}")
    else:
        print(f"sell skipped: unwanted ${unwanted_value:.2f} below ${MIN_ORDER_USD} min")

    print(f"done — holding {side}")


if __name__ == "__main__":
    main()
