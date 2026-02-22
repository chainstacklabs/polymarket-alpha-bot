"""Shared CLOB market sell — FAK order with slippage protection."""

from typing import Optional

from loguru import logger


def sell_via_clob(
    client,
    token_id: str,
    amount: float,
    price: float,
    slippage: float = 10,
) -> tuple[Optional[str], bool, Optional[str]]:
    """Sell tokens via CLOB market order. Returns (order_id, filled, error).

    Always uses FAK (fill available, cancel rest) — partial fills are acceptable
    when selling unwanted tokens. The price acts as a worst-price cap.

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
        return order_id, True, None
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg and (
            "blocked" in error_msg.lower() or "restricted" in error_msg.lower()
        ):
            error_msg = "Trading restricted in your region — enable proxy"
        logger.error(f"CLOB sell error: {error_msg}")
        return None, False, error_msg
