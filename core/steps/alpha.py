"""
Alpha detection and opportunity generation.

Combines logic from:
- experiments/06_1_compute_conditionals.py
- experiments/06_2_detect_alpha.py
- experiments/06_3_export_opportunities.py

For production pipeline.
"""

from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

# Alpha filtering thresholds
MIN_ALPHA_THRESHOLD = 0.10
MIN_CONFIDENCE = 0.60
MIN_TRIGGER_PRICE = 0.02
MAX_TRIGGER_PRICE = 0.80

# URL generation
POLYMARKET_BASE_URL = "https://polymarket.com/event"

# Relation type priors for P(B|A)
RELATION_TYPE_PRIORS = {
    "DIRECT_CAUSE": {"min": 0.80, "max": 0.95, "default": 0.88},
    "ENABLING_CONDITION": {"min": 0.50, "max": 0.80, "default": 0.65},
    "INHIBITING_CONDITION": {"min": 0.02, "max": 0.25, "default": 0.10},
    "REQUIRES": {"min": 0.00, "max": 0.00, "default": 0.00},
    "CORRELATED": {"min": 0.40, "max": 0.70, "default": 0.55},
}

# Structural relations (skip for alpha - these are non-actionable)
STRUCTURAL_RELATION_TYPES = {
    "TIMEFRAME_VARIANT",  # Same event, different deadline
    "THRESHOLD_VARIANT",  # Same event, different threshold
    "SUBSET_VARIANT",  # Specific implies general (Cuba → Latin America)
    "HIERARCHICAL",  # COUNT market + THRESHOLD market
    "SERIES_MEMBER",  # Part of same series
    "MUTUALLY_EXCLUSIVE",  # Opposite outcomes
    "INDEPENDENT",  # No relationship
}


# =============================================================================
# CONDITIONAL PROBABILITY ESTIMATION
# =============================================================================


def estimate_conditional(edge: dict) -> tuple[float, float, float]:
    """
    Estimate P(B|A) for a causal edge.

    Returns:
        tuple of (P_B_given_A, P_B_given_not_A, confidence)
    """
    rel_type = edge.get("relation_type", "")
    priors = RELATION_TYPE_PRIORS.get(rel_type)

    if priors is None:
        return 0.5, 0.5, 0.5

    conf = edge.get("confidence", 0.7)
    base = priors["default"]

    if rel_type == "INHIBITING_CONDITION":
        p_b_given_a = priors["min"] + (priors["default"] - priors["min"]) * (1 - conf)
        p_b_given_not_a = 0.5
    elif rel_type == "REQUIRES":
        p_b_given_a = priors["default"]
        p_b_given_not_a = 0.0
    else:
        p_b_given_a = base + (priors["max"] - base) * conf
        p_b_given_not_a = base * 0.5

    return p_b_given_a, p_b_given_not_a, conf


def compute_conditionals(
    graph: dict,
    events: list[dict],
) -> tuple[list[dict], dict[str, str]]:
    """
    Compute conditional probabilities for all causal edges.

    Args:
        graph: Knowledge graph with nodes and edges
        events: All events with current prices

    Returns:
        Tuple of (edges with conditional probability estimates, slug lookup)
    """
    # Build price and slug lookups
    prices = {}
    slugs = {}
    for event in events:
        event_id = event.get("id")
        markets = event.get("markets", [])
        if event_id:
            # Price lookup
            if markets:
                outcome_prices = markets[0].get("outcomePrices", [0.5])
                prices[event_id] = outcome_prices[0] if outcome_prices else 0.5
            # Slug lookup (for Polymarket URLs)
            if event.get("slug"):
                slugs[event_id] = event["slug"]

    # Build node lookup from graph
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}

    # Process edges
    processed = []

    for edge in graph.get("edges", []):
        rel_type = edge.get("relation_type", "")

        # Skip structural relations
        if rel_type in STRUCTURAL_RELATION_TYPES:
            continue

        # Skip unknown relation types
        if rel_type not in RELATION_TYPE_PRIORS:
            continue

        source_id = edge.get("source")
        target_id = edge.get("target")

        source_node = nodes_by_id.get(source_id, {})
        target_node = nodes_by_id.get(target_id, {})

        # Get prices (from events or graph nodes)
        source_price = prices.get(source_id, source_node.get("current_price", 0.5))
        target_price = prices.get(target_id, target_node.get("current_price", 0.5))

        # Estimate conditional
        p_b_given_a, p_b_given_not_a, model_conf = estimate_conditional(edge)

        # Market-implied conditional (under independence assumption)
        market_implied = target_price

        # Compute delta
        conditional_delta = p_b_given_a - market_implied

        processed.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "source_title": source_node.get("title", ""),
                "target_title": target_node.get("title", ""),
                "relation_type": rel_type,
                "source_price": source_price,
                "target_price": target_price,
                "model_P_B_given_A": round(p_b_given_a, 4),
                "model_P_B_given_not_A": round(p_b_given_not_a, 4),
                "model_confidence": round(model_conf, 4),
                "market_implied_P_B_given_A": round(market_implied, 4),
                "conditional_delta": round(conditional_delta, 4),
            }
        )

    logger.info(f"Computed conditionals for {len(processed)} causal edges")
    return processed, slugs


# =============================================================================
# ALPHA DETECTION
# =============================================================================


def generate_market_url(slug: str | None, event_id: str) -> str:
    """Generate Polymarket URL for an event using slug (preferred) or event_id."""
    identifier = slug if slug else event_id
    return f"{POLYMARKET_BASE_URL}/{identifier}"


def detect_alpha(
    edges_with_conditionals: list[dict],
    slugs: dict[str, str] | None = None,
) -> list[dict]:
    """
    Detect alpha opportunities from model vs market divergence.

    Args:
        edges_with_conditionals: Edges with conditional probability estimates
        slugs: Dict mapping event_id to slug for URL generation

    Returns:
        List of alpha opportunities
    """
    if slugs is None:
        slugs = {}

    opportunities = []
    signal_counter = 0

    for edge in edges_with_conditionals:
        model_conditional = edge["model_P_B_given_A"]
        market_implied = edge["market_implied_P_B_given_A"]
        source_price = edge.get("source_price", 0.5)
        model_confidence = edge.get("model_confidence", 0.7)

        # Compute alpha
        alpha_signal = model_conditional - market_implied

        # Apply filters
        if abs(alpha_signal) < MIN_ALPHA_THRESHOLD:
            continue
        if model_confidence < MIN_CONFIDENCE:
            continue
        if source_price < MIN_TRIGGER_PRICE or source_price > MAX_TRIGGER_PRICE:
            continue

        signal_counter += 1

        # Determine direction
        if alpha_signal > 0:
            direction = "BUY_CONSEQUENCE_IF_TRIGGER"
            action = "BUY"
            description = f"If {edge['source_title'][:50]}... resolves YES, buy {edge['target_title'][:50]}..."
        else:
            direction = "SELL_CONSEQUENCE_IF_TRIGGER"
            action = "SELL"
            description = f"If {edge['source_title'][:50]}... resolves YES, sell {edge['target_title'][:50]}..."

        # Compute expected value
        target_price = edge["target_price"]
        if alpha_signal > 0:
            ev_per_dollar = alpha_signal / target_price if target_price > 0 else 0
        else:
            risk = 1 - target_price
            ev_per_dollar = abs(alpha_signal) / risk if risk > 0 else 0

        # Get slugs for URL generation
        source_slug = slugs.get(edge["source_id"])
        target_slug = slugs.get(edge["target_id"])

        opportunities.append(
            {
                "signal_id": f"alpha_{signal_counter:04d}",
                "trigger": {
                    "event_id": edge["source_id"],
                    "slug": source_slug,
                    "title": edge["source_title"],
                    "current_price": edge["source_price"],
                    "market_url": generate_market_url(source_slug, edge["source_id"]),
                },
                "consequence": {
                    "event_id": edge["target_id"],
                    "slug": target_slug,
                    "title": edge["target_title"],
                    "current_price": edge["target_price"],
                    "market_url": generate_market_url(target_slug, edge["target_id"]),
                },
                "relation_type": edge["relation_type"],
                "alpha_signal": round(alpha_signal, 4),
                "alpha_direction": direction,
                "confidence": model_confidence,
                "confidence_adjusted_alpha": round(alpha_signal * model_confidence, 4),
                "strategy": {
                    "description": description,
                    "action": action,
                    "trigger_condition": "TRIGGER_YES",
                    "target_outcome": "YES" if action == "BUY" else "NO",
                },
                "expected_value": {
                    "per_dollar_at_risk": round(ev_per_dollar, 2),
                    "assuming_trigger_resolves_yes": True,
                },
            }
        )

    # Sort by confidence-adjusted alpha
    opportunities.sort(key=lambda x: abs(x["confidence_adjusted_alpha"]), reverse=True)

    logger.info(f"Detected {len(opportunities)} alpha opportunities")
    return opportunities


# =============================================================================
# EXPORT
# =============================================================================


def generate_summary(
    opportunities: list[dict],
    graph: dict,
) -> dict:
    """Generate summary statistics."""
    if not opportunities:
        return {
            "total_opportunities": 0,
            "graph_nodes": graph.get("node_count", 0),
            "graph_edges": graph.get("edge_count", 0),
        }

    alpha_values = [o["alpha_signal"] for o in opportunities]
    by_direction = {}
    for o in opportunities:
        d = o["alpha_direction"]
        by_direction[d] = by_direction.get(d, 0) + 1

    return {
        "total_opportunities": len(opportunities),
        "avg_alpha": round(sum(alpha_values) / len(alpha_values), 4),
        "max_alpha": round(max(alpha_values), 4),
        "min_alpha": round(min(alpha_values), 4),
        "by_direction": by_direction,
        "graph_nodes": graph.get("node_count", 0),
        "graph_edges": graph.get("edge_count", 0),
    }


def run_alpha_detection(
    graph: dict,
    events: list[dict],
) -> tuple[list[dict], dict]:
    """
    Full alpha detection pipeline.

    Args:
        graph: Knowledge graph
        events: All events with prices (including slugs for URL generation)

    Returns:
        Tuple of (opportunities, summary)
    """
    # Compute conditionals (also builds slug lookup for URLs)
    edges_with_conditionals, slugs = compute_conditionals(graph, events)

    # Detect alpha (with slugs for proper Polymarket URLs)
    opportunities = detect_alpha(edges_with_conditionals, slugs)

    # Generate summary
    summary = generate_summary(opportunities, graph)

    return opportunities, summary
