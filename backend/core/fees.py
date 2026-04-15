"""Taker fee math for Polymarket CLOB.

Fees are per-market: each market carries `feesEnabled` (bool) and, when enabled,
a `feeSchedule` dict {exponent, rate, takerOnly, rebateRate}.

Formula (Polymarket fee docs; verified on live markets 2026-04-15):
    fee = notional * rate * price^exponent * (1 - price)^exponent

All observed markets have exponent=1 and takerOnly=true. Code handles the
general case; logs a warning on unusual values.
"""

from typing import Any

from loguru import logger

_WARNED_EXPONENTS: set[int] = set()


def is_fee_bearing(market: dict[str, Any]) -> bool:
    """True if this market charges taker fees."""
    return bool(market.get("feesEnabled"))


def compute_fee(notional: float, price: float, market: dict[str, Any]) -> float:
    """Compute taker fee on a trade of `notional` USDC at `price` per share.

    Returns 0.0 if the market is fee-exempt or feeSchedule is missing.
    """
    if not is_fee_bearing(market):
        return 0.0
    schedule = market.get("feeSchedule")
    if not schedule:
        # Fee-bearing market without schedule attached -- log once per market
        # and fall back to 0. Indicates enrich_fees was not run.
        mid = market.get("id", "?")
        logger.warning(f"Market {mid}: feesEnabled=True but feeSchedule missing; fee=0")
        return 0.0
    rate = float(schedule.get("rate", 0.0))
    exponent = int(schedule.get("exponent", 1))
    if exponent != 1 and exponent not in _WARNED_EXPONENTS:
        logger.warning(
            f"Unexpected feeSchedule.exponent={exponent}; formula still applied"
        )
        _WARNED_EXPONENTS.add(exponent)
    if price <= 0.0 or price >= 1.0:
        return 0.0
    return notional * rate * (price**exponent) * ((1.0 - price) ** exponent)


def fee_rate_display(market: dict[str, Any]) -> str | None:
    """Human-readable fee rate for UI, e.g. '7.2% taker'. None if fee-exempt."""
    if not is_fee_bearing(market):
        return None
    schedule = market.get("feeSchedule") or {}
    rate = schedule.get("rate")
    if rate is None:
        return None
    return f"{rate * 100:.1f}% taker"
