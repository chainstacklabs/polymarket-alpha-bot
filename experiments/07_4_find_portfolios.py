"""
Find covering portfolios using algorithmic optimization.

Enumerates position combinations and evaluates coverage across world states.
This step is pure computation - no LLM required.

Why LLM is NOT used:
- Portfolio optimization is pure computation
- Given the resolution matrix and prices, optimal portfolios are deterministic
- LLM would add no value and only slow things down

Input:
- data/07_1_build_clusters/<latest>/clusters.json
- data/07_2_define_world_states/<latest>/world_states.json
- data/07_3_build_resolution_matrix/<latest>/resolution_matrices.json
- data/_live/events.json (for current prices)

Output:
- data/07_4_find_portfolios/<timestamp>/portfolios.json
- data/07_4_find_portfolios/<timestamp>/summary.json

Pipeline: 07_3_build_resolution_matrix -> [07_4_find_portfolios] -> 07_5_merge_opportunities
"""

import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"

# Input directories
INPUT_CLUSTERS_DIR = DATA_DIR / "07_1_build_clusters"
INPUT_WORLD_STATES_DIR = DATA_DIR / "07_2_define_world_states"
INPUT_RESOLUTION_DIR = DATA_DIR / "07_3_build_resolution_matrix"
EVENTS_FILE = LIVE_DIR / "events.json"
INPUT_RUN_FOLDER: str | None = None  # None = use latest

# Output directory
SCRIPT_OUTPUT_DIR = DATA_DIR / "07_4_find_portfolios"

# Portfolio parameters
MIN_EXPECTED_PROFIT = 0.15  # 15% minimum expected profit (conservative)
MIN_COVERAGE_RATE = 0.8  # 80% of states must pay >= cost
MAX_OPPORTUNITIES_PER_CLUSTER = 5  # Top N per cluster
MIN_POSITIONS = 2  # At least 2 positions for meaningful portfolio
MAX_POSITIONS = 6  # Limit complexity

# =============================================================================
# RISK TOLERANCE SCALE
# =============================================================================
# Controls maximum acceptable loss in worst-case scenario as ratio of total cost.
#
# Scale:
#   0.00 = GUARANTEED PROFIT (min_payout >= cost in ALL states) - very rare
#   0.10 = Conservative (max 10% loss in worst case)
#   0.25 = Moderate (max 25% loss in worst case)
#   0.50 = Aggressive (max 50% loss in worst case)
#   1.00 = No limit (can lose entire investment)
#
# Example: If total_cost = $1.00 and MAX_WORST_CASE_LOSS_RATIO = 0.10
#          Then min_payout must be >= $0.90 (lose at most 10%)
#
MAX_WORST_CASE_LOSS_RATIO = 0.25  # Default: moderate risk tolerance

# Confidence penalty - discount expected profit by model uncertainty
# Final profit = expected_profit * (CONFIDENCE_FLOOR + model_confidence * (1 - CONFIDENCE_FLOOR))
# This prevents over-reliance on uncertain model predictions
APPLY_CONFIDENCE_PENALTY = True
CONFIDENCE_FLOOR = (
    0.5  # Minimum confidence multiplier (0.5 = at least 50% of raw profit)
)

# Domain concentration limits
# Prevents over-concentration in single theme (e.g., all political resignations)
MAX_POSITIONS_PER_DOMAIN = 4  # Max positions from same domain in final output

# Price filters
MIN_POSITION_COST = 0.02  # Skip near-zero prices (2%)
MAX_POSITION_COST = 0.98  # Skip near-certain markets (98%)

# Logging
LOG_LEVEL = logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================


@dataclass
class Position:
    """A single position in a portfolio."""

    event_id: str
    event_title: str
    side: str  # "YES" or "NO"
    cost: float  # Price paid for position
    market_price: float  # Current market price (YES price)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_title": self.event_title,
            "side": self.side,
            "cost": round(self.cost, 4),
            "market_price": round(self.market_price, 4),
        }


@dataclass
class CoverageOpportunity:
    """A portfolio of positions that covers multiple world states."""

    opportunity_id: str
    cluster_id: str
    cluster_theme: str

    # The trade
    positions: list[Position]
    total_cost: float

    # World state analysis
    world_states: list[dict]
    payouts_by_state: dict[str, float]
    resolution_matrix: dict[str, dict[str, str]]

    # Coverage metrics
    coverage_rate: float
    covered_states: list[str]
    uncovered_states: list[str]

    # Economics
    min_payout: float
    max_payout: float
    expected_payout: float
    expected_profit: float
    expected_roi: float  # expected_profit / total_cost

    # Risk
    model_confidence: float
    worst_case_loss_ratio: float  # (cost - min_payout) / cost
    confidence_adjusted_profit: float  # Profit after confidence penalty
    risk_level: str  # "guaranteed", "conservative", "moderate", "aggressive"
    risk_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "opportunity_id": self.opportunity_id,
            "opportunity_type": "coverage",
            "cluster_id": self.cluster_id,
            "cluster_theme": self.cluster_theme,
            "positions": [p.to_dict() for p in self.positions],
            "total_cost": round(self.total_cost, 4),
            "coverage_metrics": {
                "coverage_rate": round(self.coverage_rate, 4),
                "covered_states": self.covered_states,
                "uncovered_states": self.uncovered_states,
            },
            "economics": {
                "min_payout": round(self.min_payout, 4),
                "max_payout": round(self.max_payout, 4),
                "expected_payout": round(self.expected_payout, 4),
                "expected_profit": round(self.expected_profit, 4),
                "expected_roi": round(self.expected_roi, 4),
                "confidence_adjusted_profit": round(self.confidence_adjusted_profit, 4),
            },
            "risk": {
                "level": self.risk_level,
                "worst_case_loss_ratio": round(self.worst_case_loss_ratio, 4),
                "model_confidence": round(self.model_confidence, 4),
            },
            "world_states": self.world_states,
            "payouts_by_state": {
                k: round(v, 4) for k, v in self.payouts_by_state.items()
            },
            "risk_factors": self.risk_factors,
            "strategy_description": self._generate_strategy_description(),
        }

    def _generate_strategy_description(self) -> str:
        """Generate human-readable strategy description."""
        parts = []
        for pos in self.positions:
            parts.append(
                f"Buy {pos.side} on '{pos.event_title}' @ {pos.market_price:.0%}"
            )
        return " + ".join(parts)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def classify_risk_level(worst_case_loss_ratio: float) -> str:
    """
    Classify portfolio risk level based on worst-case loss ratio.

    Returns human-readable risk level.
    """
    if worst_case_loss_ratio <= 0:
        return "guaranteed"  # Cannot lose money
    elif worst_case_loss_ratio <= 0.10:
        return "conservative"  # Max 10% loss
    elif worst_case_loss_ratio <= 0.25:
        return "moderate"  # Max 25% loss
    elif worst_case_loss_ratio <= 0.50:
        return "aggressive"  # Max 50% loss
    else:
        return "speculative"  # Can lose >50%


def apply_confidence_penalty(
    expected_profit: float,
    model_confidence: float,
) -> float:
    """
    Apply confidence penalty to expected profit.

    This discounts the raw expected profit based on model uncertainty.
    A model with 100% confidence keeps full profit, while lower confidence
    reduces the effective profit.

    Formula: adjusted = raw * (floor + confidence * (1 - floor))
    With floor=0.5 and confidence=0.7: adjusted = raw * (0.5 + 0.7 * 0.5) = raw * 0.85
    """
    if not APPLY_CONFIDENCE_PENALTY:
        return expected_profit

    confidence_multiplier = CONFIDENCE_FLOOR + model_confidence * (1 - CONFIDENCE_FLOOR)
    return expected_profit * confidence_multiplier


def find_latest_run_folder(script_dir: Path) -> Path | None:
    """Find the most recent run folder."""
    if not script_dir.exists():
        return None
    run_folders = [f for f in script_dir.iterdir() if f.is_dir()]
    if not run_folders:
        return None
    return max(run_folders, key=lambda f: f.stat().st_mtime)


def get_event_price(event: dict) -> float | None:
    """
    Extract YES price from event data.

    Handles both live prices and nested market structures.
    """
    # Try direct outcomePrices (from markets)
    markets = event.get("markets", [])
    if markets:
        # Use the first market's YES price
        market = markets[0]
        prices = market.get("outcomePrices", [])
        if prices and len(prices) >= 1:
            return float(prices[0])

    # Fallback to event-level price if available
    if "current_price" in event:
        return float(event["current_price"])

    return None


def calculate_payout(
    positions: list[Position],
    state_resolutions: dict[str, str],
) -> float:
    """
    Calculate portfolio payout under a specific world state.

    Each position pays $1 if it wins, $0 if it loses.
    UNC resolutions are treated as 50% expected value.
    """
    payout = 0.0
    for pos in positions:
        resolution = state_resolutions.get(pos.event_id, "UNC")
        if resolution == pos.side:
            payout += 1.0  # Win
        elif resolution == "UNC":
            payout += 0.5  # Expected value
        # else: 0 (lose)
    return payout


def evaluate_portfolio(
    positions: list[Position],
    world_states: list[dict],
    resolution_matrix: dict[str, dict[str, str]],
) -> dict:
    """
    Evaluate a portfolio's performance across all world states.

    Returns metrics dict.
    """
    total_cost = sum(p.cost for p in positions)

    # Calculate payout in each state
    payouts_by_state = {}
    for state in world_states:
        state_id = state["id"]
        state_resolutions = resolution_matrix.get(state_id, {})
        payout = calculate_payout(positions, state_resolutions)
        payouts_by_state[state_id] = payout

    # Compute metrics
    min_payout = min(payouts_by_state.values()) if payouts_by_state else 0.0
    max_payout = max(payouts_by_state.values()) if payouts_by_state else 0.0

    # Expected payout (weighted by state probabilities)
    expected_payout = sum(
        payouts_by_state[s["id"]] * s["probability"]
        for s in world_states
        if s["id"] in payouts_by_state
    )

    expected_profit = expected_payout - total_cost

    # Coverage analysis (states where payout >= cost, i.e., no loss)
    covered = [
        s["id"] for s in world_states if payouts_by_state.get(s["id"], 0) >= total_cost
    ]
    uncovered = [
        s["id"] for s in world_states if payouts_by_state.get(s["id"], 0) < total_cost
    ]
    coverage_rate = len(covered) / len(world_states) if world_states else 0.0

    # Risk metrics
    worst_case_loss = max(0, total_cost - min_payout)
    worst_case_loss_ratio = worst_case_loss / total_cost if total_cost > 0 else 0.0

    return {
        "total_cost": total_cost,
        "payouts_by_state": payouts_by_state,
        "min_payout": min_payout,
        "max_payout": max_payout,
        "expected_payout": expected_payout,
        "expected_profit": expected_profit,
        "coverage_rate": coverage_rate,
        "covered_states": covered,
        "uncovered_states": uncovered,
        "worst_case_loss_ratio": worst_case_loss_ratio,
    }


def find_covering_portfolios(
    cluster: dict,
    world_states: list[dict],
    resolution_matrix: dict[str, dict[str, str]],
    prices: dict[str, float],
    cluster_theme: str,
    model_confidence: float,
) -> list[CoverageOpportunity]:
    """
    Enumerate position combinations and find profitable portfolios.

    Complexity: O(3^k) where k = number of events (max 8 → 6,561 combinations)
    """
    cluster_id = cluster.get("cluster_id", "unknown")
    events = cluster.get("events", [])

    if not events or not world_states:
        return []

    # Filter events with valid prices
    valid_events = []
    for event in events:
        event_id = event.get("id")
        price = prices.get(event_id)
        if price is not None and MIN_POSITION_COST <= price <= MAX_POSITION_COST:
            valid_events.append((event, price))

    if len(valid_events) < MIN_POSITIONS:
        logger.debug(f"Cluster {cluster_id}: not enough events with valid prices")
        return []

    opportunities = []
    opp_counter = 0

    # Enumerate all position combinations
    # For each event: YES, NO, or SKIP
    n_events = len(valid_events)
    total_combos = 3**n_events
    logger.debug(f"Cluster {cluster_id}: evaluating {total_combos} combinations")

    for combo in itertools.product(["YES", "NO", "SKIP"], repeat=n_events):
        # Build positions (skip SKIPs)
        positions = []
        for i, side in enumerate(combo):
            if side == "SKIP":
                continue
            event, yes_price = valid_events[i]
            cost = yes_price if side == "YES" else (1 - yes_price)
            positions.append(
                Position(
                    event_id=event["id"],
                    event_title=event["title"],
                    side=side,
                    cost=cost,
                    market_price=yes_price,
                )
            )

        # Check position count constraints
        if len(positions) < MIN_POSITIONS or len(positions) > MAX_POSITIONS:
            continue

        # Evaluate portfolio
        metrics = evaluate_portfolio(positions, world_states, resolution_matrix)

        # Filter by risk tolerance (worst-case loss limit)
        if metrics["worst_case_loss_ratio"] > MAX_WORST_CASE_LOSS_RATIO:
            continue

        # Apply confidence penalty to expected profit
        confidence_adjusted_profit = apply_confidence_penalty(
            metrics["expected_profit"],
            model_confidence,
        )

        # Filter by minimum expected profit (after confidence penalty)
        if confidence_adjusted_profit < MIN_EXPECTED_PROFIT:
            continue

        # Filter by minimum coverage rate
        if metrics["coverage_rate"] < MIN_COVERAGE_RATE:
            continue

        # Calculate ROI (based on confidence-adjusted profit)
        roi = (
            confidence_adjusted_profit / metrics["total_cost"]
            if metrics["total_cost"] > 0
            else 0
        )

        # Classify risk level
        risk_level = classify_risk_level(metrics["worst_case_loss_ratio"])

        # Build opportunity
        opp_counter += 1
        opportunity = CoverageOpportunity(
            opportunity_id=f"cov_{cluster_id}_{opp_counter:03d}",
            cluster_id=cluster_id,
            cluster_theme=cluster_theme,
            positions=positions,
            total_cost=metrics["total_cost"],
            world_states=world_states,
            payouts_by_state=metrics["payouts_by_state"],
            resolution_matrix=resolution_matrix,
            coverage_rate=metrics["coverage_rate"],
            covered_states=metrics["covered_states"],
            uncovered_states=metrics["uncovered_states"],
            min_payout=metrics["min_payout"],
            max_payout=metrics["max_payout"],
            expected_payout=metrics["expected_payout"],
            expected_profit=metrics["expected_profit"],
            expected_roi=roi,
            model_confidence=model_confidence,
            worst_case_loss_ratio=metrics["worst_case_loss_ratio"],
            confidence_adjusted_profit=confidence_adjusted_profit,
            risk_level=risk_level,
        )
        opportunities.append(opportunity)

    # Sort by confidence-adjusted profit (descending)
    opportunities.sort(key=lambda x: x.confidence_adjusted_profit, reverse=True)

    # Return top N
    return opportunities[:MAX_OPPORTUNITIES_PER_CLUSTER]


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("Starting 07_4_find_portfolios")

    # =========================================================================
    # Load clusters
    # =========================================================================

    if INPUT_RUN_FOLDER:
        clusters_folder = INPUT_CLUSTERS_DIR / INPUT_RUN_FOLDER
    else:
        clusters_folder = find_latest_run_folder(INPUT_CLUSTERS_DIR)

    if not clusters_folder or not clusters_folder.exists():
        raise FileNotFoundError(f"Clusters folder not found: {clusters_folder}")

    clusters_file = clusters_folder / "clusters.json"
    with open(clusters_file, encoding="utf-8") as f:
        clusters_data = json.load(f)

    clusters = clusters_data.get("clusters", [])
    logger.info(f"Loaded {len(clusters)} clusters")

    # =========================================================================
    # Load world states
    # =========================================================================

    if INPUT_RUN_FOLDER:
        world_states_folder = INPUT_WORLD_STATES_DIR / INPUT_RUN_FOLDER
    else:
        world_states_folder = find_latest_run_folder(INPUT_WORLD_STATES_DIR)

    if not world_states_folder or not world_states_folder.exists():
        raise FileNotFoundError(f"World states folder not found: {world_states_folder}")

    world_states_file = world_states_folder / "world_states.json"
    with open(world_states_file, encoding="utf-8") as f:
        world_states_data = json.load(f)

    cluster_world_states = world_states_data.get("cluster_world_states", [])
    world_states_by_cluster = {cws["cluster_id"]: cws for cws in cluster_world_states}
    logger.info(f"Loaded world states for {len(cluster_world_states)} clusters")

    # =========================================================================
    # Load resolution matrices
    # =========================================================================

    if INPUT_RUN_FOLDER:
        resolution_folder = INPUT_RESOLUTION_DIR / INPUT_RUN_FOLDER
    else:
        resolution_folder = find_latest_run_folder(INPUT_RESOLUTION_DIR)

    if not resolution_folder or not resolution_folder.exists():
        raise FileNotFoundError(f"Resolution folder not found: {resolution_folder}")

    resolution_file = resolution_folder / "resolution_matrices.json"
    with open(resolution_file, encoding="utf-8") as f:
        resolution_data = json.load(f)

    resolution_matrices = resolution_data.get("resolution_matrices", [])
    matrices_by_cluster = {rm["cluster_id"]: rm for rm in resolution_matrices}
    logger.info(f"Loaded resolution matrices for {len(resolution_matrices)} clusters")

    # =========================================================================
    # Load event prices
    # =========================================================================

    if not EVENTS_FILE.exists():
        raise FileNotFoundError(f"Events file not found: {EVENTS_FILE}")

    with open(EVENTS_FILE, encoding="utf-8") as f:
        events_data = json.load(f)

    events_list = events_data.get("events", [])
    prices = {}
    for event in events_list:
        event_id = event.get("id")
        price = get_event_price(event)
        if price is not None:
            prices[event_id] = price

    logger.info(f"Loaded prices for {len(prices)} events")

    # =========================================================================
    # Find portfolios for each cluster
    # =========================================================================

    all_opportunities: list[CoverageOpportunity] = []
    clusters_with_opportunities = 0

    for i, cluster in enumerate(clusters):
        cluster_id = cluster.get("cluster_id", f"cluster_{i}")
        logger.info(f"Processing cluster {i + 1}/{len(clusters)}: {cluster_id}")

        # Get world states for this cluster
        cws = world_states_by_cluster.get(cluster_id)
        if not cws:
            logger.warning(f"  No world states found for {cluster_id}")
            continue

        # Get resolution matrix for this cluster
        rm = matrices_by_cluster.get(cluster_id)
        if not rm:
            logger.warning(f"  No resolution matrix found for {cluster_id}")
            continue

        world_states = cws.get("world_states", [])
        resolution_matrix = rm.get("matrix", {})
        model_confidence = rm.get("confidence", 0.7)
        cluster_theme = cws.get("cluster_theme", cluster.get("domain", "unknown"))

        # Find portfolios
        opportunities = find_covering_portfolios(
            cluster=cluster,
            world_states=world_states,
            resolution_matrix=resolution_matrix,
            prices=prices,
            cluster_theme=cluster_theme,
            model_confidence=model_confidence,
        )

        if opportunities:
            clusters_with_opportunities += 1
            all_opportunities.extend(opportunities)
            logger.info(f"  Found {len(opportunities)} portfolios")
            # Log best opportunity
            best = opportunities[0]
            logger.info(
                f"  Best: {best.expected_profit:.1%} profit, "
                f"{best.coverage_rate:.0%} coverage, "
                f"{len(best.positions)} positions"
            )
        else:
            logger.info("  No profitable portfolios found")

    # Sort all opportunities by expected profit
    all_opportunities.sort(key=lambda x: x.expected_profit, reverse=True)

    # =========================================================================
    # Save outputs
    # =========================================================================

    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder = SCRIPT_OUTPUT_DIR / timestamp
    output_folder.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output folder: {output_folder}")

    # Save portfolios.json
    portfolios_output = {
        "_meta": {
            "description": "Coverage portfolio opportunities",
            "created_at": start_time.isoformat(),
            "source_clusters": str(clusters_folder),
            "source_world_states": str(world_states_folder),
            "source_resolution": str(resolution_folder),
            "parameters": {
                "min_expected_profit": MIN_EXPECTED_PROFIT,
                "min_coverage_rate": MIN_COVERAGE_RATE,
                "max_opportunities_per_cluster": MAX_OPPORTUNITIES_PER_CLUSTER,
                "min_positions": MIN_POSITIONS,
                "max_positions": MAX_POSITIONS,
                "max_worst_case_loss_ratio": MAX_WORST_CASE_LOSS_RATIO,
                "apply_confidence_penalty": APPLY_CONFIDENCE_PENALTY,
                "confidence_floor": CONFIDENCE_FLOOR,
            },
        },
        "opportunities": [opp.to_dict() for opp in all_opportunities],
    }

    with open(output_folder / "portfolios.json", "w", encoding="utf-8") as f:
        json.dump(portfolios_output, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(all_opportunities)} portfolios")

    # Save summary.json
    end_time = datetime.now(timezone.utc)

    summary = {
        "run_info": {
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "output_folder": str(output_folder),
        },
        "results": {
            "total_clusters": len(clusters),
            "clusters_with_opportunities": clusters_with_opportunities,
            "total_opportunities": len(all_opportunities),
            "avg_opportunities_per_cluster": (
                len(all_opportunities) / clusters_with_opportunities
                if clusters_with_opportunities > 0
                else 0
            ),
        },
        "opportunity_stats": {
            "best_expected_profit": (
                max(o.expected_profit for o in all_opportunities)
                if all_opportunities
                else 0
            ),
            "best_confidence_adjusted_profit": (
                max(o.confidence_adjusted_profit for o in all_opportunities)
                if all_opportunities
                else 0
            ),
            "avg_expected_profit": (
                sum(o.expected_profit for o in all_opportunities)
                / len(all_opportunities)
                if all_opportunities
                else 0
            ),
            "avg_coverage_rate": (
                sum(o.coverage_rate for o in all_opportunities) / len(all_opportunities)
                if all_opportunities
                else 0
            ),
            "avg_positions": (
                sum(len(o.positions) for o in all_opportunities)
                / len(all_opportunities)
                if all_opportunities
                else 0
            ),
            "avg_worst_case_loss_ratio": (
                sum(o.worst_case_loss_ratio for o in all_opportunities)
                / len(all_opportunities)
                if all_opportunities
                else 0
            ),
        },
        "risk_breakdown": {
            "guaranteed": sum(
                1 for o in all_opportunities if o.risk_level == "guaranteed"
            ),
            "conservative": sum(
                1 for o in all_opportunities if o.risk_level == "conservative"
            ),
            "moderate": sum(1 for o in all_opportunities if o.risk_level == "moderate"),
            "aggressive": sum(
                1 for o in all_opportunities if o.risk_level == "aggressive"
            ),
            "speculative": sum(
                1 for o in all_opportunities if o.risk_level == "speculative"
            ),
        },
        "parameters": {
            "min_expected_profit": MIN_EXPECTED_PROFIT,
            "min_coverage_rate": MIN_COVERAGE_RATE,
            "max_opportunities_per_cluster": MAX_OPPORTUNITIES_PER_CLUSTER,
            "max_worst_case_loss_ratio": MAX_WORST_CASE_LOSS_RATIO,
            "apply_confidence_penalty": APPLY_CONFIDENCE_PENALTY,
        },
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Saved summary.json")

    # Log final summary
    logger.info("=" * 60)
    logger.info("PORTFOLIO OPTIMIZATION COMPLETE")
    logger.info(f"  Total opportunities: {len(all_opportunities)}")
    logger.info(
        f"  Clusters with opportunities: {clusters_with_opportunities}/{len(clusters)}"
    )
    if all_opportunities:
        best = all_opportunities[0]
        logger.info(f"  Best opportunity: {best.opportunity_id}")
        logger.info(f"    Raw profit: {best.expected_profit:.1%}")
        logger.info(f"    Adjusted profit: {best.confidence_adjusted_profit:.1%}")
        logger.info(f"    Coverage: {best.coverage_rate:.0%}")
        logger.info(
            f"    Risk level: {best.risk_level} (max loss: {best.worst_case_loss_ratio:.0%})"
        )
        logger.info(f"    Positions: {len(best.positions)}")
        # Log risk breakdown
        guaranteed = sum(1 for o in all_opportunities if o.risk_level == "guaranteed")
        conservative = sum(
            1 for o in all_opportunities if o.risk_level == "conservative"
        )
        moderate = sum(1 for o in all_opportunities if o.risk_level == "moderate")
        logger.info(
            f"  Risk breakdown: {guaranteed} guaranteed, {conservative} conservative, {moderate} moderate"
        )
    logger.info(f"  Duration: {summary['run_info']['duration_seconds']:.2f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
