"""
Calculate portfolio metrics and classify hedges.

This module takes validated market pairs and calculates trading numbers:
- Total cost (target price + cover price)
- Coverage probability (chance of getting paid)
- Expected profit (coverage - cost)
- Tier classification for filtering

Coverage formula:
    Coverage = P(target wins) + P(target loses) × P(cover fires | target loses)

Example:
    Buy: "Region NOT captured" @ $0.80 (target)
    Buy: "City captured"       @ $0.15 (cover)
    Total cost: $0.95

    Coverage = 0.80 + 0.20 × 0.98 = 99.6%
    Expected profit = $0.996 - $0.95 = +$0.046

Tier classification:
    - TIER 1 (HIGH):     ≥95% coverage - near-arbitrage
    - TIER 2 (GOOD):     90-95% - strong hedges
    - TIER 3 (MODERATE): 85-90% - decent but noticeable risk
    - TIER 4 (LOW):      <85% - speculative

Note:
    This step is pure calculation - no caching needed.
    Portfolios should be recalculated when prices change.
"""

from loguru import logger

from core.state import PipelineState

# =============================================================================
# CONFIGURATION
# =============================================================================

# Maximum portfolio cost to include (filter outliers)
MAX_COST = 10.00

# Minimum coverage to include (filters out Tier 4 / Low quality)
MIN_COVERAGE = 0.85

# Coverage tier thresholds (coverage_threshold, tier_number, label, description)
TIER_THRESHOLDS = [
    (0.95, 1, "HIGH_COVERAGE", "near-arbitrage"),
    (0.90, 2, "GOOD_COVERAGE", "strong hedge"),
    (0.85, 3, "MODERATE_COVERAGE", "decent hedge"),
    (0.00, 4, "LOW_COVERAGE", "speculative"),
]


# =============================================================================
# METRICS CALCULATION
# =============================================================================


def calculate_coverage_metrics(
    target_price: float,
    cover_probability: float,
    total_cost: float,
) -> dict:
    """
    Calculate coverage and expected value for a portfolio.

    Args:
        target_price: Price of target position (= P(target pays out))
        cover_probability: P(cover fires | target doesn't pay out)
        total_cost: Total cost of both positions

    Returns:
        Dict with coverage, loss_probability, expected_profit
    """
    p_target = target_price
    p_not_target = 1 - target_price

    # Coverage = P(get paid) = P(target wins) + P(target loses) × P(cover fires)
    coverage = p_target + p_not_target * cover_probability

    # Loss probability = P(both fail)
    loss_probability = p_not_target * (1 - cover_probability)

    # Expected payout is just coverage (each payout is $1)
    expected_profit = coverage - total_cost

    return {
        "coverage": round(coverage, 4),
        "loss_probability": round(loss_probability, 4),
        "expected_profit": round(expected_profit, 4),
    }


def classify_tier(coverage: float) -> tuple[int, str]:
    """
    Classify portfolio into tier based on coverage.

    Returns:
        Tuple of (tier_number, tier_label)
    """
    for threshold, tier, label, _ in TIER_THRESHOLDS:
        if coverage >= threshold:
            return tier, label
    return 4, "LOW_COVERAGE"


# =============================================================================
# PORTFOLIO BUILDING
# =============================================================================


def build_portfolios(
    validated_pairs: list[dict],
    max_cost: float = MAX_COST,
) -> tuple[list[dict], dict]:
    """
    Build portfolios from validated pairs with metrics.

    Args:
        validated_pairs: Pairs from validate step (with _validation metadata)
        max_cost: Maximum portfolio cost to include

    Returns:
        Tuple of (portfolios, summary_stats)
    """
    portfolios = []

    for pair in validated_pairs:
        # Get prices based on positions
        target_price = pair.get("target_price", 0)
        cover_probability = pair.get("cover_probability", 0)

        # Cover price depends on position
        if pair["cover_position"] == "YES":
            cover_price = pair.get("cover_price_yes", 0)
        else:
            cover_price = pair.get("cover_price_no", 0)

        total_cost = target_price + cover_price

        # Skip invalid costs
        if not (0 < total_cost <= max_cost):
            continue

        # Calculate metrics
        metrics = calculate_coverage_metrics(
            target_price, cover_probability, total_cost
        )

        # Skip low coverage portfolios (Tier 4)
        if metrics["coverage"] < MIN_COVERAGE:
            continue

        # Classify tier
        tier, tier_label = classify_tier(metrics["coverage"])

        portfolio = {
            # Identity
            "pair_id": pair["pair_id"],
            # Target info
            "target_group_id": pair["target_group_id"],
            "target_group_title": pair.get("target_group_title", ""),
            "target_group_slug": pair.get("target_group_slug", ""),
            "target_market_id": pair["target_market_id"],
            "target_market_slug": pair.get("target_market_slug", ""),
            "target_question": pair.get("target_question", ""),
            "target_position": pair["target_position"],
            "target_price": target_price,
            "target_bracket": pair.get("target_bracket"),
            "target_resolution": pair.get("target_resolution"),
            # Cover info
            "cover_group_id": pair["cover_group_id"],
            "cover_group_title": pair.get("cover_group_title", ""),
            "cover_group_slug": pair.get("cover_group_slug", ""),
            "cover_market_id": pair["cover_market_id"],
            "cover_market_slug": pair.get("cover_market_slug", ""),
            "cover_question": pair.get("cover_question", ""),
            "cover_position": pair["cover_position"],
            "cover_price": cover_price,
            "cover_bracket": pair.get("cover_bracket"),
            "cover_resolution": pair.get("cover_resolution"),
            "cover_probability": cover_probability,
            # Relationship
            "relationship": pair.get("relationship", ""),
            "relationship_type": pair.get("relationship_type", ""),
            # Metrics
            "total_cost": round(total_cost, 4),
            "profit": round(1.0 - total_cost, 4),
            "profit_pct": round((1.0 - total_cost) / total_cost * 100, 2)
            if total_cost > 0
            else 0,
            **metrics,
            # Tier
            "tier": tier,
            "tier_label": tier_label,
            # Validation metadata
            "viability_score": pair.get("_validation", {}).get("viability_score", 0),
            "validation_analysis": pair.get("_validation", {}).get(
                "brief_analysis", ""
            ),
        }

        portfolios.append(portfolio)

    # Sort by tier, then coverage
    portfolios.sort(key=lambda p: (p["tier"], -p["coverage"]))

    # Compute summary stats
    summary = compute_summary(portfolios)

    logger.info(f"Built {len(portfolios)} portfolios from {len(validated_pairs)} pairs")

    return portfolios, summary


def compute_summary(portfolios: list[dict]) -> dict:
    """Compute summary statistics for portfolios."""
    if not portfolios:
        return {
            "total_portfolios": 0,
            "by_tier": {},
            "by_position": {},
            "avg_coverage": 0,
            "avg_expected_profit": 0,
            "avg_cost": 0,
            "profitable_count": 0,
        }

    # Count by tier
    by_tier = {}
    for p in portfolios:
        tier = p["tier"]
        if tier not in by_tier:
            by_tier[tier] = {"count": 0, "coverages": [], "profits": []}
        by_tier[tier]["count"] += 1
        by_tier[tier]["coverages"].append(p["coverage"])
        by_tier[tier]["profits"].append(p["expected_profit"])

    # Aggregate tier stats
    tier_summary = {}
    for tier, data in by_tier.items():
        tier_summary[f"tier_{tier}"] = {
            "count": data["count"],
            "avg_coverage": round(sum(data["coverages"]) / len(data["coverages"]), 4),
            "avg_profit": round(sum(data["profits"]) / len(data["profits"]), 4),
        }

    # Count by position type
    by_position = {}
    for p in portfolios:
        pos_type = f"target_{p['target_position']}"
        by_position[pos_type] = by_position.get(pos_type, 0) + 1

    # Aggregate stats
    avg_coverage = sum(p["coverage"] for p in portfolios) / len(portfolios)
    avg_profit = sum(p["expected_profit"] for p in portfolios) / len(portfolios)
    avg_cost = sum(p["total_cost"] for p in portfolios) / len(portfolios)
    profitable_count = sum(1 for p in portfolios if p["expected_profit"] > 0.001)

    return {
        "total_portfolios": len(portfolios),
        "by_tier": tier_summary,
        "by_position": by_position,
        "avg_coverage": round(avg_coverage, 4),
        "avg_expected_profit": round(avg_profit, 4),
        "avg_cost": round(avg_cost, 4),
        "profitable_count": profitable_count,
        "best_coverage": portfolios[0]["coverage"] if portfolios else 0,
    }


# =============================================================================
# STATE INTEGRATION
# =============================================================================


def build_and_save_portfolios(
    validated_pairs: list[dict],
    state: PipelineState,
    max_cost: float = MAX_COST,
) -> tuple[list[dict], dict]:
    """
    Build portfolios and save to state.

    Args:
        validated_pairs: Pairs from validate step
        state: Pipeline state for storage
        max_cost: Maximum portfolio cost

    Returns:
        Tuple of (portfolios, summary)
    """
    portfolios, summary = build_portfolios(validated_pairs, max_cost)

    # Save to state
    state.save_portfolios(portfolios)

    return portfolios, summary


def update_portfolio_prices(
    state: PipelineState,
    price_updates: dict[str, dict],
) -> tuple[list[dict], dict]:
    """
    Update portfolio metrics with new prices.

    This should be called when prices change to recalculate
    coverage and expected profit.

    Args:
        state: Pipeline state with existing portfolios
        price_updates: Dict mapping market_id to {price_yes, price_no}

    Returns:
        Tuple of (updated_portfolios, summary)
    """
    existing = state.get_portfolios()

    if not existing:
        logger.warning("No existing portfolios to update")
        return [], {"updated": 0}

    updated = []
    changes = 0

    for portfolio in existing:
        target_id = portfolio["target_market_id"]
        cover_id = portfolio["cover_market_id"]

        # Get updated prices
        target_update = price_updates.get(target_id, {})
        cover_update = price_updates.get(cover_id, {})

        # Update target price
        if portfolio["target_position"] == "YES":
            new_target_price = target_update.get("price_yes", portfolio["target_price"])
        else:
            new_target_price = target_update.get("price_no", portfolio["target_price"])

        # Update cover price
        if portfolio["cover_position"] == "YES":
            new_cover_price = cover_update.get("price_yes", portfolio["cover_price"])
        else:
            new_cover_price = cover_update.get("price_no", portfolio["cover_price"])

        # Check if prices changed
        price_changed = (
            abs(new_target_price - portfolio["target_price"]) > 0.001
            or abs(new_cover_price - portfolio["cover_price"]) > 0.001
        )

        if price_changed:
            changes += 1

            # Recalculate metrics
            total_cost = new_target_price + new_cover_price

            cover_prob = portfolio["cover_probability"]
            metrics = calculate_coverage_metrics(
                new_target_price, cover_prob, total_cost
            )

            # Skip if coverage dropped below minimum (Tier 4)
            if metrics["coverage"] < MIN_COVERAGE:
                continue

            tier, tier_label = classify_tier(metrics["coverage"])

            portfolio = {
                **portfolio,
                "target_price": new_target_price,
                "cover_price": new_cover_price,
                "total_cost": round(total_cost, 4),
                "profit": round(1.0 - total_cost, 4),
                "profit_pct": round((1.0 - total_cost) / total_cost * 100, 2)
                if total_cost > 0
                else 0,
                **metrics,
                "tier": tier,
                "tier_label": tier_label,
            }

        updated.append(portfolio)

    # Re-sort and save
    updated.sort(key=lambda p: (p["tier"], -p["coverage"]))
    state.save_portfolios(updated)

    summary = {
        "total_portfolios": len(updated),
        "prices_updated": changes,
        "unchanged": len(updated) - changes,
    }

    logger.info(f"Updated {changes}/{len(updated)} portfolio prices")

    return updated, summary


# =============================================================================
# QUERY HELPERS
# =============================================================================


def filter_portfolios_by_tier(
    portfolios: list[dict],
    max_tier: int = 2,
) -> list[dict]:
    """
    Filter portfolios by maximum tier.

    Args:
        portfolios: List of portfolios
        max_tier: Maximum tier to include (1 = best only)

    Returns:
        Filtered list
    """
    return [p for p in portfolios if p["tier"] <= max_tier]


def filter_profitable_portfolios(
    portfolios: list[dict],
    min_profit: float = 0.0,
) -> list[dict]:
    """
    Filter to only profitable portfolios.

    Args:
        portfolios: List of portfolios
        min_profit: Minimum expected profit

    Returns:
        Filtered list
    """
    return [p for p in portfolios if p["expected_profit"] > min_profit]


def get_top_portfolios(
    portfolios: list[dict],
    n: int = 10,
    profitable_only: bool = False,
) -> list[dict]:
    """
    Get top N portfolios by coverage.

    Args:
        portfolios: List of portfolios
        n: Number to return
        profitable_only: If True, only include profitable ones

    Returns:
        Top N portfolios
    """
    filtered = portfolios
    if profitable_only:
        filtered = [p for p in portfolios if p["expected_profit"] > 0.001]

    return sorted(filtered, key=lambda p: -p["coverage"])[:n]
