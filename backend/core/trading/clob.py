"""Shared CLOB market sell — FAK order with slippage protection.

Under ``POLYMARKET_V2_ENABLED`` the V2 SDK is used. The V2 ``MarketOrderArgsV2``
struct drops ``fee_rate_bps``/``nonce``/``taker`` (taker fees are computed
server-side at match time in V2) and adds an optional ``metadata`` field. The
EIP-712 domain version flips from ``"1"`` to ``"2"`` internally in
``py_clob_client_v2`` — we don't pass it.
"""

import time
from typing import Optional

from loguru import logger

from core.feature_flags import v2_enabled


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
        if v2_enabled():
            # V2 struct: no fee_rate_bps / nonce / taker; fees are match-time.
            # `metadata` defaults to BYTES32_ZERO; `builder_code` is set on the
            # client via builder_config (we leave it None per #27 scope).
            from py_clob_client_v2.clob_types import (
                MarketOrderArgsV2 as MarketOrderArgs,
                OrderType,
            )
            from py_clob_client_v2.order_builder.constants import SELL
        else:
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
    """Query order fill status. Returns matched token amount.

    Retries on transient SDK errors (up to 3 attempts with exponential backoff).
    Returns 0.0 on exhausted retries — caller should treat this as unverified
    and rely on balance queries on next position refresh.
    """
    time.sleep(1)  # Brief wait for settlement
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            order = client.get_order(order_id)
            size_matched = float(order.get("size_matched", 0))
            logger.info(
                f"Order {order_id}: size_matched={size_matched}, "
                f"original_size={order.get('original_size')}"
            )
            return size_matched
        except Exception as e:
            last_exc = e
            # Short-circuit likely-permanent failures (auth / missing order)
            # instead of burning retries and flattening to 0.0, which would
            # hide actionable state from the caller.
            err = str(e).lower()
            if any(
                tok in err
                for tok in (
                    "401",
                    "403",
                    "404",
                    "unauthorized",
                    "forbidden",
                    "not found",
                )
            ):
                logger.warning(f"Permanent get_order failure for {order_id}: {e}")
                break
            if attempt < 2:
                logger.debug(
                    f"get_filled_size retry {attempt + 1}/3 for {order_id}: {e}"
                )
                time.sleep(2**attempt)
    logger.warning(f"Could not fetch order status for {order_id}: {last_exc}")
    return 0.0
