"""Taker fee math for Polymarket CLOB.

Fees are per-market: each market carries `feesEnabled` (bool) and, when enabled,
a `feeSchedule` dict {exponent, rate, takerOnly, rebateRate}.

Formula (Polymarket fee docs; verified on live markets 2026-04-15):
    fee = notional * rate * price^exponent * (1 - price)^exponent

All observed markets have exponent=1 and takerOnly=true. Code handles the
general case; logs a warning on unusual values.

V1 vs V2 (issue #36):
    - V1 sets `fee_rate_bps` on the signed order; the value is enforced at
      settlement against the per-market schedule.
    - V2 drops `fee_rate_bps` from the order struct entirely. Taker fees are
      computed server-side at match time via ``getClobMarketInfo()``. We never
      pass fees through `MarketOrderArgsV2` / `OrderArgsV2`.
    - Either way, `compute_fee()` is **display-only** for our bot: pre-trade
      cost projection and unrealized-P&L exit-fee estimates. Realized P&L is
      derived from balance deltas, which already net any actual fees paid.
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


# Public alias matching the issue #36 vocabulary. `compute_fee` is the existing
# name used across the codebase (cost estimates, exit-fee projections). Both
# names refer to the same display-only computation.
display_fee = compute_fee


def fee_rate_display(market: dict[str, Any]) -> str | None:
    """Human-readable fee rate for UI, e.g. '7.2% taker'. None if fee-exempt."""
    if not is_fee_bearing(market):
        return None
    schedule = market.get("feeSchedule") or {}
    rate = schedule.get("rate")
    if rate is None:
        return None
    return f"{rate * 100:.1f}% taker"
