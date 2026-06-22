"""Shared CLOB market sell — FAK sigtype-3 order via the polymarket-client SDK.

The deposit-wallet (sigtype-3) client posts orders signed with ERC-1271
(POLY_1271). Taker fees are computed server-side at match time. Polymarket
enforces a $1 minimum order size — callers should not sell dust.
"""

from typing import Optional

from loguru import logger


def _tick_decimals(tick_size: float) -> int:
    """Count decimal places from a tick size value."""
    tick_str = f"{tick_size:.10f}".rstrip("0")
    return len(tick_str.split(".")[1]) if "." in tick_str else 0


def compute_sell_price(price: float, slippage: float, tick_size: float = 0.01) -> float:
    """Compute worst-case sell price with slippage, rounded to tick size."""
    slippage_pct = max(10, min(50, slippage))
    raw = price * (1 - slippage_pct / 100)
    decimals = _tick_decimals(tick_size)
    return round(max(raw, tick_size), decimals)


def sell_via_clob(
    client,
    token_id: str,
    amount: float,
    price: float,
    slippage: float = 10,
) -> tuple[Optional[str], float, Optional[str]]:
    """Sell tokens via a sigtype-3 FAK market order. Returns (order_id, filled_shares, error).

    `client` is a polymarket SecureClient. `min_price` acts as the worst-price
    floor. Partial fills are acceptable when offloading the unwanted side.

    Args:
        client: Authenticated SecureClient (deposit wallet).
        token_id: Token to sell.
        amount: Number of shares to sell.
        price: Current market price.
        slippage: Slippage percentage (clamped to 10-50%).
    """
    if amount <= 0 or price <= 0:
        msg = f"Invalid sell params: amount={amount}, price={price}"
        logger.error(msg)
        return None, 0.0, msg

    try:
        min_price = compute_sell_price(price, slippage)
        resp = client.place_market_order(
            token_id=token_id,
            side="SELL",
            shares=amount,
            min_price=min_price,
            order_type="FAK",
        )

        # RejectedOrder (ok=False) — soft rejection (e.g. fak_not_filled).
        if not getattr(resp, "ok", False):
            msg = getattr(resp, "message", None) or "Order rejected by CLOB"
            logger.error(f"CLOB sell rejected: {msg}")
            return None, 0.0, msg

        # AcceptedOrder — making_amount is the shares we sold (matched).
        order_id = resp.order_id
        filled = float(resp.making_amount)
        logger.info(
            f"CLOB sigtype-3 sell (min_price={min_price}): {order_id} filled={filled}"
        )
        if filled < amount:
            logger.warning(
                f"FAK partial fill: {filled:.4f}/{amount:.4f} for {order_id}"
            )
        return order_id, filled, None
    except Exception as e:
        msg = str(e)
        if "restricted" in msg.lower() or ("403" in msg and "blocked" in msg.lower()):
            msg = "Trading restricted in your region — backend must run from an allowed region"
        logger.error(f"CLOB sell error: {msg}")
        return None, 0.0, msg
