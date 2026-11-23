"""Shared CLOB market sell — FAK sigtype-3 order via the polymarket-client SDK.

The deposit-wallet (sigtype-3) client posts orders signed with ERC-1271
(POLY_1271). Taker fees are computed server-side at match time. Polymarket
enforces a $1 minimum order size — callers should not sell dust.
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from core.fees import taker_fee


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


@dataclass
class ClobSell:
    """Outcome of a FAK sell. Amounts are what the exchange reported for the fill."""

    order_id: Optional[str] = None
    filled: float = 0.0  # shares sold (making_amount)
    proceeds: float = 0.0  # pUSD received (taking_amount)
    error: Optional[str] = None


def _book_price(client, token_id: str, amount: float, fallback: float) -> float:
    """Worst price the order book pays for `amount` shares now.

    The mid price can sit far above the best bid on wide books, which puts a
    FAK floor above every bid. Estimated as FOK: a book too thin for the
    whole amount raises (a FAK estimate would return its lowest bid and drop
    the floor to the bottom), and then `fallback` is used, as it is for an
    unreadable book.
    """
    try:
        book = float(
            client.estimate_market_price(
                asset_id=token_id, side="SELL", shares=amount, order_type="FOK"
            )
        )
    except Exception as e:
        logger.warning(f"Order-book estimate failed for {token_id}: {e}")
        return fallback
    return book if book > 0 else fallback


def sell_via_clob(
    client,
    token_id: str,
    amount: float,
    price: float,
    slippage: float = 10,
    fee_market: Optional[dict] = None,
) -> ClobSell:
    """Sell tokens via a sigtype-3 FAK market order.

    `client` is a polymarket SecureClient. `min_price` acts as the worst-price
    floor. Partial fills are acceptable when offloading the unwanted side.

    Args:
        client: Authenticated SecureClient (deposit wallet).
        token_id: Token to sell.
        amount: Number of shares to sell.
        price: Current market price; fallback when the order book can't
            be read.
        slippage: Slippage percentage (clamped to 10-50%), applied under
            the order book's price for `amount` shares.
        fee_market: Gamma market (or fee stub) with `feesEnabled` /
            `feeSchedule`; the taker fee is taken off the proceeds. The
            order response reports the amount before fees.
    """
    if amount <= 0 or price <= 0:
        msg = f"Invalid sell params: amount={amount}, price={price}"
        logger.error(msg)
        return ClobSell(error=msg)

    try:
        min_price = compute_sell_price(
            _book_price(client, token_id, amount, price), slippage
        )
        resp = client.place_market_order(
            asset_id=token_id,
            side="SELL",
            shares=amount,
            min_price=min_price,
            order_type="FAK",
        )

        # RejectedOrder (ok=False) — soft rejection (e.g. fak_not_filled).
        if not getattr(resp, "ok", False):
            msg = getattr(resp, "message", None) or "Order rejected by CLOB"
            logger.error(f"CLOB sell rejected: {msg}")
            return ClobSell(error=msg)

        # AcceptedOrder — for a SELL, making_amount is the shares sold and
        # taking_amount the pUSD received.
        order_id = resp.order_id
        filled = float(resp.making_amount)
        gross = float(resp.taking_amount)
        fee = taker_fee(filled, gross / filled, fee_market or {}) if filled else 0.0
        proceeds = round(gross - fee, 6)
        logger.info(
            f"CLOB sigtype-3 sell (min_price={min_price}): {order_id} "
            f"filled={filled} proceeds={proceeds} (fee {fee:.4f})"
        )
        if filled < amount:
            logger.warning(
                f"FAK partial fill: {filled:.4f}/{amount:.4f} for {order_id}"
            )
        return ClobSell(order_id=order_id, filled=filled, proceeds=proceeds)
    except Exception as e:
        msg = str(e)
        if "restricted" in msg.lower() or ("403" in msg and "blocked" in msg.lower()):
            msg = "Trading restricted in your region — backend must run from an allowed region"
        logger.error(f"CLOB sell error: {msg}")
        return ClobSell(error=msg)
