"""
Cross-market arbitrage detection.

Finds exhaustive sets of outcomes from DIFFERENT Polymarket events where
the sum of prices for covering all outcomes ≠ 100%.

Key insight: Each position (YES or NO share) covers a specific outcome.
The LLM determines which combination of YES/NO positions forms an exhaustive set.

Example:
    Market A: "Russia wins war" - YES covers "Russia wins"
    Market B: "Ukraine wins war" - YES covers "Ukraine wins"
    Market C: "War continues past 2025" - NO covers "Peace/stalemate by 2025"

    If these 3 outcomes are exhaustive and sum of prices < 100%, arbitrage exists.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
from loguru import logger

from core.models import get_llm_client

if TYPE_CHECKING:
    pass

# =============================================================================
# CONFIGURATION
# =============================================================================

MIN_PROFIT_THRESHOLD = 0.01  # 1% minimum guaranteed profit
MIN_SET_SIZE = 2
MAX_SET_SIZE = 5
MIN_CONFIDENCE = 0.65
SIMILARITY_THRESHOLD = 0.60  # Cosine similarity for clustering candidates
LLM_BATCH_SIZE = 5  # Verify up to 5 candidate sets per LLM call
POLYMARKET_BASE_URL = "https://polymarket.com/event"


# =============================================================================
# DATA STRUCTURES
# =============================================================================


@dataclass
class ArbitragePosition:
    """A single position in an arbitrage set."""

    event_id: str
    title: str
    slug: str | None
    position: Literal["YES", "NO"]
    price: float  # Price of the specific share (YES or NO)
    outcome_covered: str  # Human description of what outcome this covers

    @property
    def market_url(self) -> str:
        identifier = self.slug if self.slug else self.event_id
        return f"{POLYMARKET_BASE_URL}/{identifier}"

    @property
    def price_display(self) -> str:
        return f"{int(self.price * 100)}%"

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "slug": self.slug,
            "position": self.position,
            "price": round(self.price, 4),
            "price_display": f"{int(self.price * 100)}%",
            "outcome_covered": self.outcome_covered,
            "market_url": self.market_url,
        }


@dataclass
class ExhaustiveArbitrageSet:
    """A verified exhaustive set of positions covering all outcomes."""

    positions: list[ArbitragePosition]
    confidence: float
    reasoning: str

    @property
    def total_cost(self) -> float:
        return sum(p.price for p in self.positions)

    @property
    def profit(self) -> float:
        # Exactly one position pays $1
        return 1.0 - self.total_cost

    @property
    def is_profitable(self) -> bool:
        return self.profit >= MIN_PROFIT_THRESHOLD

    def to_opportunity_dict(self, rank: int) -> dict:
        return {
            "signal_id": f"arb_{rank:04d}",
            "opportunity_type": "arbitrage",
            "positions": [p.to_dict() for p in self.positions],
            "total_cost": round(self.total_cost, 4),
            "total_cost_display": f"{int(self.total_cost * 100)}%",
            "profit": round(self.profit, 4),
            "profit_display": f"+{round(self.profit * 100, 1)}%",
            "num_markets": len(self.positions),
            "confidence": round(self.confidence, 4),
            "confidence_adjusted_profit": round(self.profit * self.confidence, 4),
            "reasoning": self.reasoning,
            "strategy": {
                "description": self._build_strategy_description(),
            },
        }

    def _build_strategy_description(self) -> str:
        parts = []
        for p in self.positions:
            parts.append(
                f"Buy {p.position} on '{p.title[:40]}...' at {p.price_display}"
            )
        cost_str = f"Total cost: ${self.total_cost:.2f}"
        profit_str = f"Guaranteed payout: $1.00, Profit: ${self.profit:.2f}"
        return ". ".join(parts) + f". {cost_str}. {profit_str}"


# =============================================================================
# CLUSTERING: Find candidate event groups
# =============================================================================


def cluster_events_by_similarity(
    embeddings: np.ndarray,
    event_ids: list[str],
) -> list[list[str]]:
    """
    Find groups of semantically similar events as arbitrage candidates.

    Uses agglomerative clustering with distance threshold to find
    groups of 2-5 events that might form exhaustive sets.

    Returns:
        List of event ID groups (each group is a candidate exhaustive set)
    """
    if len(embeddings) < MIN_SET_SIZE:
        return []

    try:
        from sklearn.cluster import AgglomerativeClustering
    except ImportError:
        logger.warning("sklearn not available, skipping arbitrage clustering")
        return []

    # Agglomerative clustering with distance threshold
    # distance = 1 - cosine_similarity, so threshold = 1 - SIMILARITY_THRESHOLD
    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=1 - SIMILARITY_THRESHOLD,
        metric="cosine",
        linkage="average",
    )

    try:
        labels = clustering.fit_predict(embeddings)
    except Exception as e:
        logger.warning(f"Clustering failed: {e}")
        return []

    # Group events by cluster label
    clusters: dict[int, list[str]] = {}
    for idx, label in enumerate(labels):
        if label == -1:  # Noise point
            continue
        if idx < len(event_ids):
            clusters.setdefault(label, []).append(event_ids[idx])

    # Filter clusters by size and cross-event requirement
    candidate_groups = []
    for cluster_events in clusters.values():
        if not (MIN_SET_SIZE <= len(cluster_events) <= MAX_SET_SIZE):
            continue

        # Verify these are from different parent events (cross-event arbitrage)
        # For now, we check that event IDs are different
        # The LLM will do deeper verification of exhaustiveness
        if len(set(cluster_events)) == len(cluster_events):
            candidate_groups.append(cluster_events)

    logger.info(
        f"Found {len(candidate_groups)} candidate groups from {len(clusters)} clusters"
    )
    return candidate_groups


# =============================================================================
# LLM VERIFICATION: Determine exhaustive YES/NO combinations
# =============================================================================

LLM_SYSTEM_PROMPT = """You are an expert in prediction markets analyzing whether a set of markets forms an EXHAUSTIVE probability space.

EXHAUSTIVE means: The chosen positions (YES or NO on each market) together cover ALL possible real-world outcomes, and exactly ONE position will pay out.

For each market, consider:
- Buying YES = betting the event HAPPENS (pays $1 if YES)
- Buying NO = betting the event DOESN'T happen (pays $1 if NO)

Your task:
1. Understand what real-world outcome each YES and NO position represents
2. Find a combination of positions (YES or NO for each market) that:
   - Covers ALL possible outcomes (exhaustive)
   - Has MUTUALLY EXCLUSIVE outcomes (exactly one will happen)
3. If no valid exhaustive combination exists, report is_exhaustive: false

IMPORTANT: Markets must be from DIFFERENT events to qualify (no same-event bracket markets).

Output valid JSON only, no other text."""

LLM_USER_PROMPT_TEMPLATE = """Analyze if these {n} markets can form an exhaustive set:

{markets_description}

Determine if there's a combination of YES/NO positions that covers all possible outcomes.

Output JSON:
{{
  "is_exhaustive": true or false,
  "positions": [
    {{"event_id": "...", "position": "YES" or "NO", "outcome_covered": "what real-world outcome this covers"}}
  ],
  "reasoning": "Explain why these positions form (or don't form) an exhaustive set",
  "confidence": 0.0 to 1.0
}}"""


def _build_market_description(event: dict) -> str:
    """Build a description of a market for LLM analysis."""
    title = event.get("title", "Unknown")
    description = event.get("description", "")[:300]  # Truncate long descriptions
    markets = event.get("markets", [])

    # Get YES/NO prices
    yes_price = 0.5
    no_price = 0.5
    if markets:
        outcome_prices = markets[0].get("outcomePrices", [0.5, 0.5])
        if len(outcome_prices) >= 2:
            yes_price = outcome_prices[0]
            no_price = outcome_prices[1]
        elif len(outcome_prices) == 1:
            yes_price = outcome_prices[0]
            no_price = 1 - yes_price

    return f"""Market ID: {event.get("id")}
Title: {title}
Description: {description}
YES price: {yes_price:.0%} (pays $1 if event happens)
NO price: {no_price:.0%} (pays $1 if event doesn't happen)
"""


async def verify_exhaustive_sets(
    candidate_groups: list[list[str]],
    events_by_id: dict[str, dict],
) -> list[ExhaustiveArbitrageSet]:
    """
    Use LLM to verify which candidate groups form exhaustive sets.

    For each group, the LLM determines:
    1. Whether an exhaustive combination exists
    2. Which specific YES/NO positions to take
    3. What outcome each position covers

    Returns:
        List of verified ExhaustiveArbitrageSet objects
    """
    if not candidate_groups:
        return []

    llm = get_llm_client()
    verified_sets: list[ExhaustiveArbitrageSet] = []

    # Process in batches
    for batch_start in range(0, len(candidate_groups), LLM_BATCH_SIZE):
        batch = candidate_groups[batch_start : batch_start + LLM_BATCH_SIZE]

        for group in batch:
            # Build market descriptions
            markets_desc_parts = []
            for i, event_id in enumerate(group, 1):
                event = events_by_id.get(event_id, {})
                markets_desc_parts.append(f"=== Market {i} ===")
                markets_desc_parts.append(_build_market_description(event))

            markets_description = "\n".join(markets_desc_parts)

            # Build prompt
            user_prompt = LLM_USER_PROMPT_TEMPLATE.format(
                n=len(group),
                markets_description=markets_description,
            )

            messages = [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            try:
                response = await llm.complete(messages, temperature=0.1)
                result = _parse_llm_response(str(response), events_by_id)

                if result and result.is_profitable:
                    verified_sets.append(result)
                    logger.debug(
                        f"Verified exhaustive set: {len(result.positions)} positions, "
                        f"profit={result.profit:.1%}"
                    )

            except Exception as e:
                logger.warning(f"LLM verification failed for group {group}: {e}")
                continue

    logger.info(f"Verified {len(verified_sets)} exhaustive arbitrage sets")
    return verified_sets


def _parse_llm_response(
    response: str,
    events_by_id: dict[str, dict],
) -> ExhaustiveArbitrageSet | None:
    """Parse LLM JSON response into ExhaustiveArbitrageSet."""
    try:
        # Extract JSON from response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start < 0 or end <= start:
            return None

        data = json.loads(response[start:end])

        if not data.get("is_exhaustive"):
            return None

        confidence = data.get("confidence", 0.7)
        if confidence < MIN_CONFIDENCE:
            return None

        reasoning = data.get("reasoning", "")
        positions_data = data.get("positions", [])

        if not positions_data:
            return None

        # Build ArbitragePosition objects
        positions: list[ArbitragePosition] = []
        for pos_data in positions_data:
            event_id = pos_data.get("event_id")
            if event_id not in events_by_id:
                continue

            event = events_by_id[event_id]
            position_type = pos_data.get("position", "YES").upper()
            outcome_covered = pos_data.get("outcome_covered", "")

            # Get the price for this position (YES or NO)
            markets = event.get("markets", [])
            yes_price = 0.5
            no_price = 0.5
            if markets:
                outcome_prices = markets[0].get("outcomePrices", [0.5, 0.5])
                if len(outcome_prices) >= 2:
                    yes_price = outcome_prices[0]
                    no_price = outcome_prices[1]
                elif len(outcome_prices) == 1:
                    yes_price = outcome_prices[0]
                    no_price = 1 - yes_price

            price = yes_price if position_type == "YES" else no_price

            positions.append(
                ArbitragePosition(
                    event_id=event_id,
                    title=event.get("title", ""),
                    slug=event.get("slug"),
                    position=position_type,
                    price=price,
                    outcome_covered=outcome_covered,
                )
            )

        if len(positions) < MIN_SET_SIZE:
            return None

        return ExhaustiveArbitrageSet(
            positions=positions,
            confidence=confidence,
            reasoning=reasoning,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug(f"Failed to parse LLM response: {e}")
        return None


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


async def run_arbitrage_detection(
    events: list[dict],
    embeddings: np.ndarray,
    event_ids: list[str],
) -> tuple[list[dict], dict]:
    """
    Detect cross-market arbitrage opportunities.

    Pipeline:
    1. Cluster events by semantic similarity
    2. LLM verification of exhaustive YES/NO combinations
    3. Filter by profit threshold
    4. Sort by confidence-adjusted profit

    Args:
        events: All events with prices
        embeddings: Event embeddings from pipeline
        event_ids: Event IDs corresponding to embeddings

    Returns:
        Tuple of (opportunities list, summary dict)
    """
    logger.info("Starting cross-market arbitrage detection...")

    # Build event lookup
    events_by_id = {e["id"]: e for e in events}

    # Step 1: Find candidate groups via clustering
    candidate_groups = cluster_events_by_similarity(embeddings, event_ids)

    if not candidate_groups:
        logger.info("No candidate groups found for arbitrage")
        return [], {"total_opportunities": 0, "candidates_analyzed": 0}

    # Step 2: LLM verification of exhaustive sets
    verified_sets = await verify_exhaustive_sets(candidate_groups, events_by_id)

    # Step 3: Filter profitable sets and sort
    profitable_sets = [s for s in verified_sets if s.is_profitable]

    # Sort by confidence-adjusted profit
    profitable_sets.sort(
        key=lambda s: s.profit * s.confidence,
        reverse=True,
    )

    # Step 4: Convert to opportunity dicts
    opportunities = [
        s.to_opportunity_dict(rank=i + 1) for i, s in enumerate(profitable_sets)
    ]

    # Build summary
    summary: dict[str, int | float] = {
        "total_opportunities": len(opportunities),
        "candidates_analyzed": len(candidate_groups),
        "verified_exhaustive": len(verified_sets),
        "profitable_sets": len(profitable_sets),
    }

    if opportunities:
        profits = [o["profit"] for o in opportunities]
        summary["avg_profit"] = round(sum(profits) / len(profits), 4)
        summary["max_profit"] = round(max(profits), 4)

    logger.info(
        f"Arbitrage detection complete: {len(opportunities)} opportunities "
        f"from {len(candidate_groups)} candidates"
    )

    return opportunities, summary
