"""
06: Calculate Portfolio Metrics and Classify Hedges.

WHAT IT DOES
    Takes validated market pairs and calculates the actual trading numbers:
    total cost, coverage probability, and expected profit. Classifies
    hedges into quality tiers for easy filtering.

WHY WE NEED THIS
    We've found pairs that LOGICALLY work as hedges. Now we need to know:
    "Is this hedge worth buying at current prices?" This step adds the
    financial math to turn logical relationships into trading decisions.

HOW A HEDGE WORKS
    You buy TWO positions simultaneously:
    1. TARGET: The bet you want exposure to (e.g., "Region NOT captured")
    2. COVER:  Insurance that pays when target loses (e.g., "City captured")

    Example with real numbers:
        Buy: "Region NOT captured" @ $0.80  (target)
        Buy: "City captured"       @ $0.15  (cover)
        Total cost: $0.95

    OUTCOMES:
        Region NOT captured (80% likely) → Target pays $1.00 ✓
        Region IS captured (20% likely)  → Cover pays $1.00 ✓ (98% of time)
        Region captured, cover fails     → Lose $0.95 ✗ (0.4% = 20% × 2%)

COVERAGE FORMULA
    Coverage = P(target wins) + P(target loses) × P(cover fires | target loses)

    Example: 0.80 + 0.20 × 0.98 = 99.6% chance of getting $1 back

PROFITABILITY WARNING
    High coverage ≠ profitable! Expected value = coverage - cost
        Coverage 99.6%, Cost $1.05 → E[profit] = $0.996 - $1.05 = -$0.054 (LOSS)
        Coverage 99.6%, Cost $0.95 → E[profit] = $0.996 - $0.95 = +$0.046 (PROFIT)

    Use this to find valid hedges, then monitor for price changes.

TIER CLASSIFICATION
    - TIER 1 (HIGH):     ≥95% coverage - near-arbitrage, very reliable
    - TIER 2 (GOOD):     90-95% - strong hedges, occasional failures
    - TIER 3 (MODERATE): 85-90% - decent but noticeable risk
    - TIER 4 (LOW):      <85% - speculative, not recommended

PIPELINE
    05b_validate_candidates → [06_find_portfolios] → (final output)

INPUT
    data/05b_validate_candidates/<timestamp>/
        - validated_candidates.json: LLM-validated market pairs

OUTPUT
    data/06_find_portfolios/<timestamp>/
        - portfolios.json : Final hedges with metrics and tiers
        - summary.json    : Counts by tier, profitability stats

RUNTIME
    <1 second (pure calculation)

CONFIGURATION
    - MAX_COST: Maximum portfolio cost to include (default: $10)
    - TIER_THRESHOLDS: Coverage % boundaries for classification
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "05b_validate_candidates"
OUTPUT_DIR = DATA_DIR / "06_find_portfolios"

INPUT_RUN_FOLDER: str | None = None

# Portfolio threshold (set high to see all valid combinations regardless of price)
MAX_COST = 10.00

# Coverage tier thresholds
TIER_THRESHOLDS = [
    (0.95, 1, "HIGH_COVERAGE", "near-arbitrage"),
    (0.90, 2, "GOOD_COVERAGE", "strong cover"),
    (0.85, 3, "MODERATE_COVERAGE", "decent cover"),
    (0.00, 4, "LOW_COVERAGE", "speculative"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================


def get_latest_folder(base_dir: Path) -> Path | None:
    """Get latest run folder."""
    if not base_dir.exists():
        return None
    folders = [f for f in base_dir.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.stat().st_mtime) if folders else None


# =============================================================================
# PORTFOLIO FINDING
# =============================================================================


def calculate_coverage_metrics(
    target_price: float, cover_prob: float, total_cost: float
) -> dict:
    """
    Calculate coverage and expected value for a portfolio.

    Args:
        target_price: Price of target position (= P(target pays out))
        cover_prob: P(cover fires | target doesn't pay out)
        total_cost: Total cost of both positions

    Coverage = P(paid) = P(target) + P(~target) * P(cover|~target)
    """
    p_target = target_price
    p_not_target = 1 - target_price

    coverage = p_target + p_not_target * cover_prob
    loss_prob = p_not_target * (1 - cover_prob)
    expected_payout = coverage  # Each payout is $1, so E[payout] = P(paid)
    expected_profit = expected_payout - total_cost

    return {
        "coverage": round(coverage, 4),
        "loss_probability": round(loss_prob, 4),
        "expected_profit": round(expected_profit, 4),
    }


def find_portfolios(market_candidates: list[dict]) -> list[dict]:
    """
    Find covering portfolios from market candidates.

    For each target market, try two strategies:
    1. Buy target_YES + NO_cover: covers when target=NO via cover firing
    2. Buy target_NO + YES_cover: covers when target=YES via cover firing
    """
    portfolios = []

    def get_cover_price(cover: dict) -> float:
        """Get price based on cover_position (YES or NO)."""
        pos = cover.get("cover_position", "YES")
        return (
            (cover.get("price_yes", 0) or 0)
            if pos == "YES"
            else (cover.get("price_no", 0) or 0)
        )

    def build_portfolio(
        portfolio_type: str,
        target_pos: str,
        target_price: float,
        cover: dict,
        target_info: dict,
    ) -> dict | None:
        """Build a portfolio dict if cost < MAX_COST."""
        cover_price = get_cover_price(cover)
        total_cost = target_price + cover_price

        if not (0 < total_cost < MAX_COST):
            return None

        cover_prob = cover.get("probability") or cover.get("confidence", 0)
        metrics = calculate_coverage_metrics(target_price, cover_prob, total_cost)

        return {
            "type": portfolio_type,
            "total_cost": round(total_cost, 4),
            "profit": round(1.0 - total_cost, 4),
            "profit_pct": round((1.0 - total_cost) / total_cost * 100, 2),
            "probability": cover_prob,
            **metrics,
            "target": {**target_info, "position": target_pos, "price": target_price},
            "cover": {
                "group_id": cover["source_group_id"],
                "group_title": cover["source_group_title"],
                "market_id": cover["market_id"],
                "question": cover["question"],
                "position": cover.get("cover_position", "YES"),
                "price": cover_price,
                "relationship": cover.get("relationship", ""),
                "relationship_type": cover.get("relationship_type", "causal"),
            },
        }

    for group in market_candidates:
        target_info = {
            "group_id": group["target_group_id"],
            "group_title": group["target_group_title"],
        }

        for market in group.get("markets", []):
            target_info["market_id"] = market["market_id"]
            target_info["question"] = market["question"]

            yes_price = market.get("price_yes", 0) or 0
            no_price = market.get("price_no", 0) or 0

            # Strategy 1: Buy target_YES, cover fires when target=NO
            for cover in market.get("yes_covering_candidates", []):
                if p := build_portfolio(
                    "target_YES_hedged", "YES", yes_price, cover, target_info
                ):
                    portfolios.append(p)

            # Strategy 2: Buy target_NO, cover fires when target=YES
            for cover in market.get("no_covering_candidates", []):
                if p := build_portfolio(
                    "target_NO_hedged", "NO", no_price, cover, target_info
                ):
                    portfolios.append(p)

    portfolios.sort(key=lambda p: (-p["coverage"], -p["expected_profit"]))
    return portfolios


def compute_stats(portfolios: list[dict]) -> dict:
    """Compute portfolio statistics."""
    if not portfolios:
        return {
            "total_portfolios": 0,
            "by_type": {},
            "by_coverage": {},
            "by_expected_profit": {},
            "avg_coverage": 0,
            "avg_expected_profit": 0,
            "avg_cost": 0,
            "best_coverage": 0,
        }

    type_counts = {}
    coverage_buckets = {"95%+": 0, "90-95%": 0, "85-90%": 0, "<85%": 0}
    profit_buckets = {"positive": 0, "zero": 0, "negative": 0}

    for p in portfolios:
        # By type
        ptype = p["type"]
        type_counts[ptype] = type_counts.get(ptype, 0) + 1

        # By coverage (probability of getting paid)
        coverage = p.get("coverage", 0)
        if coverage >= 0.95:
            coverage_buckets["95%+"] += 1
        elif coverage >= 0.90:
            coverage_buckets["90-95%"] += 1
        elif coverage >= 0.85:
            coverage_buckets["85-90%"] += 1
        else:
            coverage_buckets["<85%"] += 1

        # By expected profit
        exp_profit = p.get("expected_profit", 0)
        if exp_profit > 0.001:
            profit_buckets["positive"] += 1
        elif exp_profit < -0.001:
            profit_buckets["negative"] += 1
        else:
            profit_buckets["zero"] += 1

    avg_coverage = sum(p.get("coverage", 0) for p in portfolios) / len(portfolios)
    avg_exp_profit = sum(p.get("expected_profit", 0) for p in portfolios) / len(
        portfolios
    )
    avg_cost = sum(p["total_cost"] for p in portfolios) / len(portfolios)

    return {
        "total_portfolios": len(portfolios),
        "by_type": type_counts,
        "by_coverage": coverage_buckets,
        "by_expected_profit": profit_buckets,
        "avg_coverage": round(avg_coverage, 4),
        "avg_expected_profit": round(avg_exp_profit, 4),
        "avg_cost": round(avg_cost, 4),
        "best_coverage": round(portfolios[0].get("coverage", 0), 4)
        if portfolios
        else 0,
    }


# =============================================================================
# CLASSIFICATION
# =============================================================================


def classify_portfolio(portfolio: dict) -> dict:
    """Add tier classification to portfolio based on coverage."""
    coverage = portfolio.get("coverage", 0)
    for min_cov, tier, label, desc in TIER_THRESHOLDS:
        if coverage >= min_cov:
            return {**portfolio, "tier": tier, "tier_label": label}
    return {**portfolio, "tier": 4, "tier_label": "LOW_COVERAGE"}


def compute_tier_summary(portfolios: list[dict]) -> dict:
    """Compute summary by tier."""
    tiers = {1: [], 2: [], 3: [], 4: []}
    for p in portfolios:
        tiers[p["tier"]].append(p)

    return {
        f"tier_{t}": {
            "count": len(ps),
            "avg_coverage": round(sum(p["coverage"] for p in ps) / len(ps), 4)
            if ps
            else 0,
            "avg_profit": round(sum(p["expected_profit"] for p in ps) / len(ps), 4)
            if ps
            else 0,
        }
        for t, ps in tiers.items()
    }


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("STEP 06: Find & Classify Portfolios")
    logger.info(f"Max cost: ${MAX_COST}")
    logger.info("=" * 70)

    # Load market candidates
    input_folder = (
        INPUT_DIR / INPUT_RUN_FOLDER
        if INPUT_RUN_FOLDER
        else get_latest_folder(INPUT_DIR)
    )
    if not input_folder:
        logger.error("No input folder found")
        return

    input_file = input_folder / "validated_candidates.json"
    if not input_file.exists():
        logger.error(f"File not found: {input_file}")
        return

    logger.info(f"Loading from: {input_file}")
    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    market_candidates = data.get("market_candidates", [])
    logger.info(f"Loaded {len(market_candidates)} groups")

    # Find portfolios
    logger.info("\nSearching for covering portfolios...")
    portfolios = find_portfolios(market_candidates)
    logger.info(f"Found {len(portfolios)} portfolios")

    # Classify by tier
    logger.info("Classifying by coverage tier...")
    portfolios = [classify_portfolio(p) for p in portfolios]
    portfolios.sort(key=lambda p: (p["tier"], -p["coverage"]))

    # Compute stats
    stats = compute_stats(portfolios)
    tier_summary = compute_tier_summary(portfolios)

    # Prepare output
    run_timestamp = input_folder.name
    output_folder = OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    output_data = {
        "_meta": {
            "source": str(input_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "max_cost": MAX_COST,
            "tier_thresholds": {
                f"tier_{t}": f">={c:.0%}" for c, t, _, _ in TIER_THRESHOLDS
            },
            "description": "Covering portfolios with tier classification",
        },
        "portfolios": portfolios,
    }

    output_file = output_folder / "portfolios.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    summary = {
        "script": "06_find_portfolios",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 2),
        "config": {"max_cost": MAX_COST},
        "input": str(input_file),
        "stats": stats,
        "tier_summary": tier_summary,
        "output": str(output_folder),
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print results
    logger.info("\n" + "=" * 70)
    logger.info("RESULTS")
    logger.info("=" * 70)
    logger.info(f"  Total: {stats['total_portfolios']}")
    for tier_name, tier_data in tier_summary.items():
        if tier_data["count"] > 0:
            logger.info(
                f"  {tier_name}: {tier_data['count']} (avg coverage: {tier_data['avg_coverage']:.1%})"
            )
    logger.info("=" * 70)

    # Show top 3
    if portfolios:
        logger.info("\nTOP 3 PORTFOLIOS:")
        for i, p in enumerate(portfolios[:3], 1):
            logger.info(f"\n  #{i} [{p['tier_label']}] Coverage: {p['coverage']:.1%}")
            logger.info(f"      Target: {p['target']['question'][:50]}...")
            logger.info(f"      Cover: {p['cover']['question'][:50]}...")
            logger.info(
                f"      Cost: ${p['total_cost']:.2f} | E[profit]: ${p['expected_profit']:.4f}"
            )

    logger.info(f"\nOutput: {output_file}")


if __name__ == "__main__":
    main()
