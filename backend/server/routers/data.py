"""Data endpoints for serving pipeline outputs."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from core.paths import LIVE_DIR
from server.price_aggregation import PriceData, price_aggregation

router = APIRouter()


def load_json_file(path: Path) -> Any:
    """Load JSON file, raise 404 if not found."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path.name}")
    return json.loads(path.read_text())


# =============================================================================
# COVERING PORTFOLIOS ENDPOINTS
# =============================================================================


def recalculate_portfolios_with_live_prices(
    portfolios: list[dict],
    live_prices: dict[str, PriceData],
) -> list[dict]:
    """
    Recalculate portfolio metrics with live prices.

    Updates target/cover prices, recalculates coverage and expected profit,
    and re-classifies tiers.

    Args:
        portfolios: Base portfolios from portfolios.json
        live_prices: Current prices from PriceCacheService

    Returns:
        Recalculated portfolios sorted by tier then coverage
    """
    # Tier thresholds for reclassification
    tier_thresholds = [
        (0.95, 1, "HIGH_COVERAGE"),
        (0.90, 2, "GOOD_COVERAGE"),
        (0.85, 3, "MODERATE_COVERAGE"),
    ]
    # Minimum coverage threshold (filters out Tier 4)
    min_coverage = 0.85

    recalculated = []

    for portfolio in portfolios:
        # Make a copy
        updated = json.loads(json.dumps(portfolio))

        target_id = updated.get("target_market_id")
        cover_id = updated.get("cover_market_id")
        target_position = updated.get("target_position", "YES")
        cover_position = updated.get("cover_position", "YES")

        # Get original prices
        original_target_price = updated.get("target_price", 0.5)
        original_cover_price = updated.get("cover_price", 0.5)
        cover_probability = updated.get("cover_probability", 0.9)

        # Get live prices
        target_price_data = live_prices.get(target_id)
        cover_price_data = live_prices.get(cover_id)

        # Update target price based on position
        if target_price_data and target_price_data.price is not None:
            if target_position == "YES":
                new_target_price = target_price_data.price
            else:
                new_target_price = 1 - target_price_data.price
        else:
            new_target_price = original_target_price

        # Update cover price based on position
        if cover_price_data and cover_price_data.price is not None:
            if cover_position == "YES":
                new_cover_price = cover_price_data.price
            else:
                new_cover_price = 1 - cover_price_data.price
        else:
            new_cover_price = original_cover_price

        # Recalculate metrics
        total_cost = new_target_price + new_cover_price
        p_target = new_target_price
        p_not_target = 1 - new_target_price
        coverage = p_target + p_not_target * cover_probability
        expected_profit = coverage - total_cost

        # Skip if coverage dropped below minimum threshold
        if coverage < min_coverage:
            continue

        # Reclassify tier
        tier = 3
        tier_label = "MODERATE_COVERAGE"
        for threshold, t, label in tier_thresholds:
            if coverage >= threshold:
                tier = t
                tier_label = label
                break

        # Update portfolio
        updated["target_price"] = round(new_target_price, 4)
        updated["cover_price"] = round(new_cover_price, 4)
        updated["total_cost"] = round(total_cost, 4)
        updated["profit"] = round(1.0 - total_cost, 4)
        updated["profit_pct"] = (
            round((1.0 - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0
        )
        updated["coverage"] = round(coverage, 4)
        updated["loss_probability"] = round(p_not_target * (1 - cover_probability), 4)
        updated["expected_profit"] = round(expected_profit, 4)
        updated["tier"] = tier
        updated["tier_label"] = tier_label

        recalculated.append(updated)

    # Sort by tier, then coverage descending
    recalculated.sort(key=lambda p: (p["tier"], -p["coverage"]))

    return recalculated


@router.get("/portfolios")
async def get_portfolios(
    limit: int = Query(100, description="Max number of portfolios to return"),
    offset: int = Query(0, description="Number of portfolios to skip"),
    max_tier: int = Query(3, description="Maximum tier to include (1-3, 1=best)"),
    profitable_only: bool = Query(
        False, description="Only return profitable portfolios"
    ),
    live: bool = Query(True, description="Use live data with price recalculation"),
) -> dict[str, Any]:
    """
    Get covering portfolios with live price recalculation.

    Covering portfolios are hedging opportunities where buying two positions
    together provides coverage: if the target loses, the cover pays out.

    Tiers:
    - Tier 1: >=95% coverage (near-arbitrage)
    - Tier 2: >=90% coverage (strong hedge)
    - Tier 3: >=85% coverage (decent hedge)

    Portfolios with <85% coverage are filtered out.
    Use max_tier to filter quality (e.g., max_tier=2 for only Tier 1 and 2).
    Use profitable_only=true to get only portfolios with positive expected profit.
    """

    live_path = LIVE_DIR / "portfolios.json"

    # Return empty data if file doesn't exist (pipeline running after reset)
    if not live_path.exists():
        return {
            "source": "live",
            "count": 0,
            "total_count": 0,
            "by_tier": {},
            "profitable_count": 0,
            "data": {"portfolios": []},
            "meta": {"count": 0, "by_tier": {}, "profitable_count": 0},
        }

    data = load_json_file(live_path)

    # Handle nested format
    if isinstance(data, dict) and "portfolios" in data:
        portfolios = data["portfolios"]
        meta = data.get("_meta", {})
    elif isinstance(data, list):
        portfolios = data
        meta = {}
    else:
        portfolios = []
        meta = {}

    # Recalculate with live prices if requested
    price_metadata = None
    if live:
        live_prices = price_aggregation.get_prices()
        price_metadata = price_aggregation.get_metadata()

        if live_prices:
            portfolios = recalculate_portfolios_with_live_prices(
                portfolios, live_prices
            )

    # Apply tier filter
    if max_tier < 4:
        portfolios = [p for p in portfolios if p.get("tier", 4) <= max_tier]

    # Apply profitable filter
    if profitable_only:
        portfolios = [p for p in portfolios if p.get("expected_profit", 0) > 0.001]

    # Get total before pagination
    total_count = len(portfolios)

    # Apply pagination
    portfolios = portfolios[offset : offset + limit]

    # Count by tier
    tier_counts = {}
    profitable_count = 0
    for p in portfolios:
        tier = p.get("tier", 4)
        tier_counts[f"tier_{tier}"] = tier_counts.get(f"tier_{tier}", 0) + 1
        if p.get("expected_profit", 0) > 0:
            profitable_count += 1

    response = {
        "source": "live" if live else "static",
        "count": len(portfolios),
        "total_count": total_count,
        "by_tier": tier_counts,
        "profitable_count": profitable_count,
        "data": {"portfolios": portfolios},
        "meta": meta,
    }

    if price_metadata:
        response["prices"] = {
            "last_fetch": (
                price_metadata.last_fetch.isoformat()
                if price_metadata.last_fetch
                else None
            ),
            "is_stale": price_metadata.is_stale,
            "event_count": price_metadata.event_count,
        }

    return response
