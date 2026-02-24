"""Shared CLOB market sell — FAK order with slippage protection."""

import time
from typing import Optional

from loguru import logger


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
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        # Clamp slippage to 10-50%
        slippage_pct = max(10, min(50, slippage))
        sell_price = round(max(price * (1 - slippage_pct / 100), 0.01), 2)

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
        order_id = result.get("orderID", str(result)[:40])
        logger.info(f"CLOB market sell (slippage {slippage_pct}%): {order_id}")

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
