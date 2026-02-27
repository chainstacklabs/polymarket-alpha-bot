"""Shared CLOB market sell — FAK order with slippage protection."""

import time
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
    """Sell tokens via CLOB market order. Returns (order_id, filled_size, error).

    Always uses FAK (fill available, cancel rest) — partial fills are acceptable
    when selling unwanted tokens. The price acts as a worst-price cap.

    filled_size is the actual number of tokens matched (0.0 if nothing filled).

    Args:
        client: Initialized ClobClient instance.
        token_id: Token to sell.
        amount: Number of tokens to sell.
        price: Current market price.
        slippage: Slippage percentage (clamped to 10-50%).
    """
    if amount <= 0 or price <= 0:
        msg = f"Invalid sell params: amount={amount}, price={price}"
        logger.error(msg)
        return None, 0.0, msg

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        # Fetch market's tick size for correct price precision
        try:
            tick_size = float(client.get_tick_size(token_id))
        except Exception:
            tick_size = 0.01  # fallback

        sell_price = compute_sell_price(price, slippage, tick_size)

        order = client.create_market_order(
            MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=SELL,
                price=sell_price,
                order_type=OrderType.FAK,
            )
        )
        result = client.post_order(order, OrderType.FAK)

        if result.get("success") is False:
            error_msg = result.get("errorMsg") or "Order rejected by CLOB"
            logger.error(f"CLOB post_order failed: {error_msg}")
            return None, 0.0, error_msg

        order_id = result.get("orderID", str(result)[:40])
        logger.info(
            f"CLOB market sell (price={sell_price}, tick={tick_size}): {order_id}"
        )

        # FAK orders fill immediately — check actual matched size
        filled_size = _get_filled_size(client, order_id)
        if filled_size < amount:
            logger.warning(
                f"FAK partial fill: {filled_size:.4f}/{amount:.4f} for {order_id}"
            )

        return order_id, filled_size, None
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg and (
            "blocked" in error_msg.lower() or "restricted" in error_msg.lower()
        ):
            error_msg = "Trading restricted in your region — enable proxy"
        logger.error(f"CLOB sell error: {error_msg}")
        return None, 0.0, error_msg


def _get_filled_size(client, order_id: str) -> float:
    """Query order fill status. Returns matched token amount."""
    try:
        time.sleep(1)  # Brief wait for settlement
        order = client.get_order(order_id)
        size_matched = float(order.get("size_matched", 0))
        logger.info(
            f"Order {order_id}: size_matched={size_matched}, "
            f"original_size={order.get('original_size')}"
        )
        return size_matched
    except Exception as e:
        logger.warning(f"Could not fetch order status for {order_id}: {e}")
        # Don't assume a fill we can't verify — balance queries will
        # show the real token state on next position refresh
        return 0.0
