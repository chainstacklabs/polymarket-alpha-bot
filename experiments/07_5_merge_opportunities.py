"""Step 18: Merge conditional alpha and coverage opportunities into unified output.

Combines two opportunity types into single ranked list:
1. Conditional opportunities - Alpha from event dependencies (Step 13)
2. Coverage opportunities - Alpha from cross-event coverage (Step 17)

Deduplicates overlapping positions to avoid conflicting recommendations.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = DATA_DIR / "07_5_merge_opportunities"

# Input paths - latest runs from previous steps
CONDITIONAL_OPPS_PATH = DATA_DIR / "_live" / "opportunities.json"
COVERAGE_OPPS_DIR = DATA_DIR / "07_4_find_portfolios"

# Scoring weights
CONDITIONAL_ALPHA_WEIGHT = 1.0  # Weight for confidence-adjusted alpha
CONDITIONAL_EV_WEIGHT = 0.1  # Weight for expected value per dollar

COVERAGE_PROFIT_WEIGHT = 1.0  # Weight for expected profit
COVERAGE_CONFIDENCE_WEIGHT = 0.8  # Weight for model confidence
COVERAGE_RATE_WEIGHT = 0.5  # Weight for coverage rate

# Filtering
MIN_SCORE = 0.1  # Minimum score to include opportunity
MAX_OPPORTUNITIES = 50  # Maximum total opportunities in output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================


@dataclass
class PositionKey:
    """Unique identifier for a position (event + side)."""

    event_id: str
    side: str

    def __hash__(self) -> int:
        return hash((self.event_id, self.side))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PositionKey):
            return False
        return self.event_id == other.event_id and self.side == other.side


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================


def compute_conditional_score(opp: dict) -> float:
    """
    Compute score for conditional alpha opportunity.

    Higher score = better opportunity. Considers:
    - Confidence-adjusted alpha signal
    - Expected value per dollar at risk
    """
    alpha = opp.get("confidence_adjusted_alpha", 0)
    ev = opp.get("expected_value", {}).get("per_dollar_at_risk", 0)

    # Normalize EV (cap at 100 to avoid dominance)
    ev_normalized = min(ev, 100) / 100

    score = (CONDITIONAL_ALPHA_WEIGHT * alpha) + (CONDITIONAL_EV_WEIGHT * ev_normalized)
    return round(score, 4)


def compute_coverage_score(opp: dict) -> float:
    """
    Compute score for coverage portfolio opportunity.

    Higher score = better opportunity. Considers:
    - Expected profit (absolute)
    - Model confidence
    - Coverage rate
    """
    economics = opp.get("economics", {})
    expected_profit = economics.get("expected_profit", 0)
    expected_roi = economics.get("expected_roi", 0)
    model_confidence = opp.get("model_confidence", 0.5)
    coverage_rate = opp.get("coverage_metrics", {}).get("coverage_rate", 0)

    # Use ROI if available, otherwise use profit
    profit_metric = expected_roi if expected_roi > 0 else expected_profit

    score = (
        (COVERAGE_PROFIT_WEIGHT * profit_metric)
        + (COVERAGE_CONFIDENCE_WEIGHT * model_confidence)
        + (COVERAGE_RATE_WEIGHT * coverage_rate)
    )
    return round(score, 4)


# =============================================================================
# POSITION EXTRACTION
# =============================================================================


def extract_positions_conditional(opp: dict) -> set[PositionKey]:
    """Extract position keys from conditional opportunity."""
    positions = set()

    # Conditional opportunities have trigger/consequence structure
    strategy = opp.get("strategy", {})
    consequence = opp.get("consequence", {})

    # The actual trade is on the consequence event
    if consequence.get("event_id"):
        target_outcome = strategy.get("target_outcome", "YES")
        positions.add(PositionKey(consequence["event_id"], target_outcome))

    return positions


def extract_positions_coverage(opp: dict) -> set[PositionKey]:
    """Extract position keys from coverage opportunity."""
    positions = set()

    for pos in opp.get("positions", []):
        if pos.get("event_id") and pos.get("side"):
            positions.add(PositionKey(pos["event_id"], pos["side"]))

    return positions


def extract_all_positions(opp: dict) -> set[PositionKey]:
    """Extract all position keys from any opportunity type."""
    opp_type = opp.get("opportunity_type", "conditional")

    if opp_type == "coverage":
        return extract_positions_coverage(opp)
    else:
        return extract_positions_conditional(opp)


# =============================================================================
# LOADING FUNCTIONS
# =============================================================================


def load_conditional_opportunities() -> list[dict]:
    """Load conditional opportunities from existing pipeline output."""
    if not CONDITIONAL_OPPS_PATH.exists():
        logger.warning(f"No conditional opportunities found at {CONDITIONAL_OPPS_PATH}")
        return []

    with open(CONDITIONAL_OPPS_PATH) as f:
        data = json.load(f)

    opps = data.get("opportunities", [])

    # Filter to conditional type only
    conditional = [o for o in opps if o.get("opportunity_type") == "conditional"]

    logger.info(f"Loaded {len(conditional)} conditional opportunities")
    return conditional


def find_latest_coverage_run() -> Path | None:
    """Find the latest coverage portfolio run directory."""
    if not COVERAGE_OPPS_DIR.exists():
        return None

    runs = sorted(COVERAGE_OPPS_DIR.iterdir(), reverse=True)
    for run_dir in runs:
        portfolio_file = run_dir / "portfolios.json"
        if portfolio_file.exists():
            return portfolio_file

    return None


def load_coverage_opportunities() -> list[dict]:
    """Load coverage opportunities from latest portfolio run."""
    portfolio_file = find_latest_coverage_run()

    if not portfolio_file:
        logger.warning(f"No coverage opportunities found in {COVERAGE_OPPS_DIR}")
        return []

    with open(portfolio_file) as f:
        data = json.load(f)

    opps = data.get("opportunities", [])

    logger.info(f"Loaded {len(opps)} coverage opportunities from {portfolio_file}")
    return opps


# =============================================================================
# MERGING
# =============================================================================


def merge_and_deduplicate(
    conditional_opps: list[dict],
    coverage_opps: list[dict],
) -> list[dict]:
    """
    Merge opportunities and remove overlapping positions.

    Priority goes to higher-scored opportunities. If a position is already
    taken by a higher-scored opportunity, the lower-scored one is excluded.
    """
    # Score all opportunities
    scored = []

    for opp in conditional_opps:
        score = compute_conditional_score(opp)
        if score >= MIN_SCORE:
            scored.append({"opportunity": opp, "score": score, "type": "conditional"})

    for opp in coverage_opps:
        score = compute_coverage_score(opp)
        if score >= MIN_SCORE:
            scored.append({"opportunity": opp, "score": score, "type": "coverage"})

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    logger.info(f"Scored {len(scored)} opportunities (min_score={MIN_SCORE})")

    # Deduplicate by position
    seen_positions: set[PositionKey] = set()
    deduped = []
    conflicts = 0

    for item in scored:
        opp = item["opportunity"]
        positions = extract_all_positions(opp)

        # Check for conflicts
        conflict = positions.intersection(seen_positions)
        if conflict:
            conflicts += 1
            conflict_str = ", ".join(f"{p.event_id}:{p.side}" for p in conflict)
            logger.debug(
                f"Skipping {opp.get('opportunity_id', opp.get('signal_id'))} "
                f"due to position conflict: {conflict_str}"
            )
            continue

        # Add to result
        opp_copy = opp.copy()
        opp_copy["_merge_score"] = item["score"]
        opp_copy["_merge_rank"] = len(deduped) + 1
        deduped.append(opp_copy)
        seen_positions.update(positions)

        if len(deduped) >= MAX_OPPORTUNITIES:
            break

    logger.info(
        f"After deduplication: {len(deduped)} opportunities "
        f"({conflicts} removed due to conflicts)"
    )

    return deduped


def compute_summary_stats(merged: list[dict]) -> dict:
    """Compute summary statistics for merged opportunities."""
    conditional_count = sum(
        1 for o in merged if o.get("opportunity_type") == "conditional"
    )
    coverage_count = sum(1 for o in merged if o.get("opportunity_type") == "coverage")

    # Best opportunities by type
    best_conditional = None
    best_coverage = None

    for opp in merged:
        if opp.get("opportunity_type") == "conditional" and best_conditional is None:
            best_conditional = opp.get("signal_id")
        if opp.get("opportunity_type") == "coverage" and best_coverage is None:
            best_coverage = opp.get("opportunity_id")

    # Coverage portfolio stats
    coverage_opps = [o for o in merged if o.get("opportunity_type") == "coverage"]
    total_coverage_cost = sum(o.get("total_cost", 0) for o in coverage_opps)
    avg_coverage_rate = (
        sum(
            o.get("coverage_metrics", {}).get("coverage_rate", 0) for o in coverage_opps
        )
        / len(coverage_opps)
        if coverage_opps
        else 0
    )

    return {
        "total_opportunities": len(merged),
        "conditional_count": conditional_count,
        "coverage_count": coverage_count,
        "best_conditional": best_conditional,
        "best_coverage": best_coverage,
        "coverage_stats": {
            "total_cost_all_portfolios": round(total_coverage_cost, 4),
            "average_coverage_rate": round(avg_coverage_rate, 4),
        },
    }


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Run the opportunity merging step."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Step 18: Merge Opportunities")
    logger.info("=" * 60)

    # Load opportunities from both sources
    conditional_opps = load_conditional_opportunities()
    coverage_opps = load_coverage_opportunities()

    if not conditional_opps and not coverage_opps:
        logger.error("No opportunities found from either source!")
        return

    # Merge and deduplicate
    merged = merge_and_deduplicate(conditional_opps, coverage_opps)

    # Compute stats
    stats = compute_summary_stats(merged)

    # Prepare output
    output = {
        "_meta": {
            "description": "Merged alpha opportunities (conditional + coverage)",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "2.0",
            "sources": {
                "conditional": str(CONDITIONAL_OPPS_PATH),
                "coverage": str(find_latest_coverage_run()),
            },
            "parameters": {
                "min_score": MIN_SCORE,
                "max_opportunities": MAX_OPPORTUNITIES,
                "conditional_alpha_weight": CONDITIONAL_ALPHA_WEIGHT,
                "coverage_profit_weight": COVERAGE_PROFIT_WEIGHT,
            },
            "counts": {
                "total": stats["total_opportunities"],
                "conditional": stats["conditional_count"],
                "coverage": stats["coverage_count"],
            },
        },
        "summary": stats,
        "opportunities": merged,
    }

    # Save output
    output_file = run_dir / "merged_opportunities.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info(f"Saved merged opportunities to {output_file}")

    # Also save summary
    summary_file = run_dir / "summary.json"
    summary = {
        "run_timestamp": timestamp,
        "status": "success",
        "stats": stats,
        "input_counts": {
            "conditional_loaded": len(conditional_opps),
            "coverage_loaded": len(coverage_opps),
        },
        "output_counts": {
            "total_merged": len(merged),
            "conditional": stats["conditional_count"],
            "coverage": stats["coverage_count"],
        },
    }
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved summary to {summary_file}")

    # Print results
    print("\n" + "=" * 60)
    print("MERGED OPPORTUNITIES SUMMARY")
    print("=" * 60)
    print(f"\nTotal opportunities: {stats['total_opportunities']}")
    print(f"  - Conditional: {stats['conditional_count']}")
    print(f"  - Coverage: {stats['coverage_count']}")
    print(f"\nBest conditional: {stats['best_conditional']}")
    print(f"Best coverage: {stats['best_coverage']}")

    if stats["coverage_count"] > 0:
        print("\nCoverage portfolios:")
        print(
            f"  - Total cost: ${stats['coverage_stats']['total_cost_all_portfolios']:.2f}"
        )
        print(
            f"  - Avg coverage rate: {stats['coverage_stats']['average_coverage_rate']:.1%}"
        )

    # Show top 5 opportunities
    print("\n" + "-" * 60)
    print("TOP 5 OPPORTUNITIES (by merge score)")
    print("-" * 60)
    for i, opp in enumerate(merged[:5], 1):
        opp_type = opp.get("opportunity_type", "unknown")
        opp_id = opp.get("opportunity_id", opp.get("signal_id", "?"))
        score = opp.get("_merge_score", 0)

        if opp_type == "coverage":
            cost = opp.get("total_cost", 0)
            profit = opp.get("economics", {}).get("expected_profit", 0)
            coverage = opp.get("coverage_metrics", {}).get("coverage_rate", 0)
            print(
                f"\n{i}. [{opp_type.upper()}] {opp_id}"
                f"\n   Score: {score:.3f} | Cost: ${cost:.2f} | "
                f"Profit: {profit:.1%} | Coverage: {coverage:.0%}"
            )
        else:
            alpha = opp.get("confidence_adjusted_alpha", 0)
            trigger = opp.get("trigger", {}).get("title", "?")[:40]
            print(
                f"\n{i}. [{opp_type.upper()}] {opp_id}"
                f"\n   Score: {score:.3f} | Alpha: {alpha:.3f}"
                f"\n   Trigger: {trigger}..."
            )

    print("\n" + "=" * 60)
    print(f"Output: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
