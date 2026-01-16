"""Data endpoints for serving pipeline outputs."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from server.price_cache import PriceData

router = APIRouter()

# Data directory - relative to project root
DATA_DIR = Path(__file__).parent.parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"


def recalculate_opportunities_with_live_prices(
    opportunities: list[dict],
    live_prices: dict[str, PriceData],
) -> list[dict]:
    """
    Recalculate alpha opportunities with live prices.

    Updates trigger/consequence prices, recalculates alpha signal,
    and re-sorts by confidence-adjusted alpha.

    Formula:
        model_P_B_given_A = original_alpha + original_consequence_price
        new_alpha = model_P_B_given_A - live_consequence_price

    Args:
        opportunities: Base opportunities from opportunities.json
        live_prices: Current prices from PriceCacheService

    Returns:
        Recalculated and re-sorted opportunities
    """
    recalculated = []

    for opp in opportunities:
        # Make a deep copy to avoid mutating original
        updated = json.loads(json.dumps(opp))

        trigger = updated.get("trigger", {})
        consequence = updated.get("consequence", {})

        trigger_id = trigger.get("event_id")
        consequence_id = consequence.get("event_id")

        if not trigger_id or not consequence_id:
            recalculated.append(updated)
            continue

        # Get original values - handle both flat and nested formats
        # Flat format: alpha_signal, confidence at top level
        # Nested format: alpha.signal, relation.confidence
        if "alpha_signal" in updated:
            # Flat format from alpha detection step
            original_alpha = updated.get("alpha_signal", 0)
            original_consequence_price = consequence.get("current_price", 0.5)
            original_trigger_price = trigger.get("current_price", 0.5)
            confidence = updated.get("confidence", 0.7)
        else:
            # Nested format (legacy)
            alpha = updated.get("alpha", {})
            relation = updated.get("relation", {})
            original_alpha = alpha.get("signal", 0)
            original_consequence_price = consequence.get("price", 0.5)
            original_trigger_price = trigger.get("price", 0.5)
            confidence = relation.get("confidence", 0.7)

        # Get live prices
        trigger_price_data = live_prices.get(trigger_id)
        consequence_price_data = live_prices.get(consequence_id)

        # Use live prices if available
        live_trigger_price = (
            trigger_price_data.price
            if trigger_price_data and trigger_price_data.price is not None
            else original_trigger_price
        )
        live_consequence_price = (
            consequence_price_data.price
            if consequence_price_data and consequence_price_data.price is not None
            else original_consequence_price
        )

        # Back-calculate model prediction from original data
        # model_P_B_given_A = original_alpha + original_consequence_price
        model_conditional = original_alpha + original_consequence_price

        # Recalculate alpha with live price
        new_alpha = model_conditional - live_consequence_price

        # Update trigger prices
        updated["trigger"]["current_price"] = round(live_trigger_price, 4)
        updated["trigger"]["price_display"] = f"{int(live_trigger_price * 100)}%"

        # Update consequence prices
        updated["consequence"]["current_price"] = round(live_consequence_price, 4)
        updated["consequence"]["price_display"] = (
            f"{int(live_consequence_price * 100)}%"
        )

        # Update alpha (use flat format)
        updated["alpha_signal"] = round(new_alpha, 4)
        updated["alpha_signal_display"] = (
            f"+{int(new_alpha * 100)}%"
            if new_alpha >= 0
            else f"{int(new_alpha * 100)}%"
        )
        updated["alpha_direction"] = "BUY" if new_alpha > 0 else "SELL"
        updated["confidence_adjusted_alpha"] = round(new_alpha * confidence, 4)

        # Recalculate expected return
        if new_alpha > 0:
            ev = new_alpha / live_consequence_price if live_consequence_price > 0 else 0
        else:
            risk = 1 - live_consequence_price
            ev = abs(new_alpha) / risk if risk > 0 else 0

        if "strategy" in updated:
            updated["strategy"]["expected_return"] = f"{ev:.1f}x per dollar"

        recalculated.append(updated)

    # Re-sort by confidence-adjusted alpha (absolute value, descending)
    recalculated.sort(
        key=lambda x: abs(x.get("confidence_adjusted_alpha", 0)),
        reverse=True,
    )

    # Re-assign ranks
    for i, opp in enumerate(recalculated, 1):
        opp["rank"] = i
        opp["id"] = f"opp_{i:03d}"

    return recalculated


def recalculate_arbitrage_with_live_prices(
    opportunities: list[dict],
    live_prices: dict[str, PriceData],
) -> list[dict]:
    """
    Recalculate arbitrage opportunities with live prices.

    Updates position prices, recalculates total cost and profit,
    and re-sorts by confidence-adjusted profit.

    Args:
        opportunities: Arbitrage opportunities from opportunities.json
        live_prices: Current prices from PriceCacheService

    Returns:
        Recalculated and re-sorted arbitrage opportunities
    """
    recalculated = []

    for opp in opportunities:
        # Only process arbitrage opportunities
        if opp.get("opportunity_type") != "arbitrage":
            recalculated.append(opp)
            continue

        # Make a deep copy to avoid mutating original
        updated = json.loads(json.dumps(opp))

        positions = updated.get("positions", [])
        if not positions:
            recalculated.append(updated)
            continue

        # Update each position's price
        total_cost = 0.0
        for pos in positions:
            event_id = pos.get("event_id")
            position_type = pos.get("position", "YES")

            price_data = live_prices.get(event_id)
            if price_data and price_data.price is not None:
                # price_data.price is YES price
                if position_type == "YES":
                    new_price = price_data.price
                else:
                    new_price = 1 - price_data.price

                pos["price"] = round(new_price, 4)
                pos["price_display"] = f"{int(new_price * 100)}%"

            total_cost += pos.get("price", 0.5)

        # Recalculate profit
        profit = 1.0 - total_cost
        confidence = updated.get("confidence", 0.7)

        updated["total_cost"] = round(total_cost, 4)
        updated["total_cost_display"] = f"{int(total_cost * 100)}%"
        updated["profit"] = round(profit, 4)
        updated["profit_display"] = f"+{round(profit * 100, 1)}%"
        updated["confidence_adjusted_profit"] = round(profit * confidence, 4)

        # Only keep if still profitable (>= 1%)
        if profit >= 0.01:
            recalculated.append(updated)

    # Re-sort arbitrage by confidence-adjusted profit
    arbitrage_opps = [
        o for o in recalculated if o.get("opportunity_type") == "arbitrage"
    ]
    other_opps = [o for o in recalculated if o.get("opportunity_type") != "arbitrage"]

    arbitrage_opps.sort(
        key=lambda x: x.get("confidence_adjusted_profit", 0),
        reverse=True,
    )

    return other_opps + arbitrage_opps


def transform_to_frontend_format(opportunities: list[dict]) -> list[dict]:
    """Transform opportunities from flat format to nested format expected by frontend.

    Handles both conditional (trigger/consequence) and arbitrage (positions) opportunities.

    Backend format (flat):
        alpha_signal, alpha_direction, confidence, relation_type, trigger.current_price

    Frontend format (nested):
        alpha.signal, alpha.direction, relation.type, relation.confidence, trigger.price
    """
    transformed = []

    for opp in opportunities:
        opportunity_type = opp.get("opportunity_type", "conditional")

        if opportunity_type == "arbitrage":
            # Arbitrage opportunities have a different structure
            result = {
                "id": opp.get("id", opp.get("signal_id", "")),
                "rank": opp.get("rank", 0),
                "opportunity_type": "arbitrage",
                "positions": opp.get("positions", []),
                "total_cost": opp.get("total_cost", 0),
                "total_cost_display": opp.get("total_cost_display", ""),
                "profit": opp.get("profit", 0),
                "profit_display": opp.get("profit_display", ""),
                "num_markets": opp.get("num_markets", len(opp.get("positions", []))),
                "confidence": opp.get("confidence", 0.7),
                "reasoning": opp.get("reasoning", ""),
                "strategy": opp.get("strategy"),
            }
        else:
            # Conditional opportunities (trigger/consequence)
            alpha_signal = opp.get("alpha_signal", 0)
            confidence = opp.get("confidence", 0.7)
            relation_type = opp.get("relation_type", "UNKNOWN")

            result = {
                "id": opp.get("id", opp.get("signal_id", "")),
                "rank": opp.get("rank", 0),
                "opportunity_type": "conditional",
                "trigger": {
                    "event_id": opp.get("trigger", {}).get("event_id", ""),
                    "slug": opp.get("trigger", {}).get("slug"),
                    "title": opp.get("trigger", {}).get("title", ""),
                    "price": opp.get("trigger", {}).get("current_price", 0.5),
                    "price_display": opp.get("trigger", {}).get(
                        "price_display",
                        f"{int(opp.get('trigger', {}).get('current_price', 0.5) * 100)}%",
                    ),
                    "market_url": opp.get("trigger", {}).get("market_url"),
                },
                "consequence": {
                    "event_id": opp.get("consequence", {}).get("event_id", ""),
                    "slug": opp.get("consequence", {}).get("slug"),
                    "title": opp.get("consequence", {}).get("title", ""),
                    "price": opp.get("consequence", {}).get("current_price", 0.5),
                    "price_display": opp.get("consequence", {}).get(
                        "price_display",
                        f"{int(opp.get('consequence', {}).get('current_price', 0.5) * 100)}%",
                    ),
                    "market_url": opp.get("consequence", {}).get("market_url"),
                },
                "relation": {
                    "type": relation_type.replace("_", " ").title(),
                    "type_display": relation_type.replace("_", " ").title(),
                    "confidence": confidence,
                },
                "alpha": {
                    "signal": alpha_signal,
                    "signal_display": (
                        f"+{int(alpha_signal * 100)}%"
                        if alpha_signal >= 0
                        else f"{int(alpha_signal * 100)}%"
                    ),
                    "direction": "BUY" if alpha_signal > 0 else "SELL",
                },
                "strategy": opp.get("strategy"),
            }

        transformed.append(result)

    return transformed


def load_json_file(path: Path) -> Any:
    """Load JSON file, raise 404 if not found."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path.name}")
    return json.loads(path.read_text())


def find_latest_run(output_name: str) -> Path | None:
    """Find the latest run directory for an output."""
    output_dir = DATA_DIR / output_name
    if not output_dir.exists():
        return None

    runs = sorted(
        [d for d in output_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
        key=lambda p: p.name,
        reverse=True,
    )
    return runs[0] if runs else None


def get_run_path(output_name: str, run_id: str | None = None) -> Path:
    """Get the run path, using latest if run_id not specified."""
    if run_id:
        path = DATA_DIR / output_name / run_id
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return path

    latest = find_latest_run(output_name)
    if not latest:
        raise HTTPException(status_code=404, detail=f"No runs found for {output_name}")
    return latest


# =============================================================================
# LIVE DATA ENDPOINTS (Production - reads from _live/)
# =============================================================================


@router.get("/opportunities")
async def get_opportunities(
    limit: int = Query(100, description="Max number of opportunities to return"),
    offset: int = Query(
        0, description="Number of opportunities to skip (for pagination)"
    ),
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(None, description="Specific historical run ID"),
    type: str | None = Query(
        None, description="Filter by type: 'arbitrage', 'conditional', or all (default)"
    ),
) -> dict[str, Any]:
    """Get alpha opportunities with live price recalculation.

    By default, returns live accumulated data from the production pipeline,
    with alpha signals recalculated using current market prices.

    Use type='arbitrage' to get cross-market arbitrage opportunities.
    Use type='conditional' to get conditional probability opportunities (dependencies).
    Use live=false or specify run_id to access historical script-based runs.
    """
    from server.price_cache import price_cache

    # Try live data first
    live_path = LIVE_DIR / "opportunities.json"
    if live and run_id is None and live_path.exists():
        data = load_json_file(live_path)

        # Handle nested format: {"_meta": {...}, "opportunities": [...]}
        if isinstance(data, dict) and "opportunities" in data:
            opportunities = data["opportunities"]
        elif isinstance(data, list):
            opportunities = data
        else:
            opportunities = []

        # Get live prices
        live_prices = price_cache.get_prices()
        metadata = price_cache.get_metadata()

        if live_prices:
            # Recalculate conditional opportunities
            opportunities = recalculate_opportunities_with_live_prices(
                opportunities, live_prices
            )
            # Recalculate arbitrage opportunities
            opportunities = recalculate_arbitrage_with_live_prices(
                opportunities, live_prices
            )

        # Filter by opportunity type if specified
        if type in ("arbitrage", "conditional"):
            opportunities = [
                o for o in opportunities if o.get("opportunity_type") == type
            ]

        # Get total before pagination
        total_count = len(opportunities)

        # Apply offset and limit for pagination
        opportunities = opportunities[offset : offset + limit]

        # Transform to frontend format (nested structure)
        opportunities = transform_to_frontend_format(opportunities)

        # Count by type for response metadata
        arbitrage_count = sum(
            1 for o in opportunities if o.get("opportunity_type") == "arbitrage"
        )
        conditional_count = sum(
            1 for o in opportunities if o.get("opportunity_type") == "conditional"
        )

        return {
            "source": "live",
            "count": len(opportunities),
            "total_count": total_count,
            "arbitrage_count": arbitrage_count,
            "conditional_count": conditional_count,
            "data": {"opportunities": opportunities},
            "prices": {
                "last_fetch": (
                    metadata.last_fetch.isoformat() if metadata.last_fetch else None
                ),
                "is_stale": metadata.is_stale,
                "event_count": metadata.event_count,
            },
        }

    # Fall back to historical runs (no live recalculation)
    run_path = get_run_path("06_3_export_opportunities", run_id)
    data = load_json_file(run_path / "opportunities.json")

    # Handle nested format: {"_meta": {...}, "opportunities": [...]}
    if isinstance(data, dict) and "opportunities" in data:
        opportunities = data["opportunities"]
    elif isinstance(data, list):
        opportunities = data
    else:
        opportunities = []

    # Infer opportunity_type from structure if not set
    for opp in opportunities:
        if "opportunity_type" not in opp:
            # If it has positions, it's arbitrage; if trigger/consequence, it's conditional
            if "positions" in opp:
                opp["opportunity_type"] = "arbitrage"
            elif "trigger" in opp or "consequence" in opp:
                opp["opportunity_type"] = "conditional"

    # Filter by opportunity type if specified
    if type in ("arbitrage", "conditional"):
        opportunities = [o for o in opportunities if o.get("opportunity_type") == type]

    # Get total before pagination
    total_count = len(opportunities)

    # Apply offset and limit for pagination
    opportunities = opportunities[offset : offset + limit]

    return {
        "source": "historical",
        "run_id": run_path.name,
        "count": len(opportunities),
        "total_count": total_count,
        "data": {"opportunities": opportunities},
    }


@router.get("/graph")
async def get_graph(
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(None, description="Specific historical run ID"),
) -> dict[str, Any]:
    """Get knowledge graph.

    By default, returns live accumulated graph from the production pipeline.
    """
    # Try live data first
    live_path = LIVE_DIR / "graph.json"
    if live and run_id is None and live_path.exists():
        graph = load_json_file(live_path)
        return {
            "source": "live",
            "data": graph,
        }

    # Fall back to historical runs
    run_path = get_run_path("05_4_build_relation_graph", run_id)
    graph = load_json_file(run_path / "relation_graph.json")

    return {
        "source": "historical",
        "run_id": run_path.name,
        "data": graph,
    }


@router.get("/events")
async def get_events(
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(None, description="Specific historical run ID"),
) -> dict[str, Any]:
    """Get events data.

    By default, returns live accumulated events from the production pipeline.
    """
    # Try live data first
    live_path = LIVE_DIR / "events.json"
    if live and run_id is None and live_path.exists():
        data = load_json_file(live_path)

        # Handle nested format: {"_meta": {...}, "events": [...]}
        if isinstance(data, dict) and "events" in data:
            events = data["events"]
            meta = data.get("_meta", {})
        elif isinstance(data, list):
            events = data
            meta = {}
        else:
            events = []
            meta = {}

        return {
            "source": "live",
            "count": len(events),
            "data": {"events": events},
            "meta": meta,
        }

    # Fall back to historical runs
    run_path = get_run_path("01_fetch_events", run_id)
    events = load_json_file(run_path / "events.json")

    return {
        "source": "historical",
        "run_id": run_path.name,
        "count": len(events) if isinstance(events, list) else 1,
        "data": events,
    }


@router.get("/entities")
async def get_entities(
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(
        None, description="Specific run ID, or latest if not specified"
    ),
) -> dict[str, Any]:
    """Get entities.

    By default, returns live accumulated entities from the production pipeline.
    Use live=false or specify run_id to access historical script-based runs.
    """
    # Try live data first (from SQLite state)
    if live and run_id is None:
        try:
            from core.state import load_state

            state = load_state()
            entities = state.get_all_entities()
            state.close()

            if entities:
                return {
                    "source": "live",
                    "count": len(entities),
                    "data": entities,
                }
        except Exception:
            pass  # Fall through to historical

    # Fall back to historical runs
    run_path = get_run_path("03_3_normalize_entities", run_id)
    entities = load_json_file(run_path / "entities_normalized.json")

    return {
        "source": "historical",
        "run_id": run_path.name,
        "count": len(entities) if isinstance(entities, list) else 1,
        "data": entities,
    }


@router.get("/relations")
async def get_relations(
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(
        None, description="Specific run ID, or latest if not specified"
    ),
) -> dict[str, Any]:
    """Get relation graph data.

    By default, returns live accumulated graph from the production pipeline.
    Use live=false or specify run_id to access historical script-based runs.
    """
    # Try live data first
    live_path = LIVE_DIR / "graph.json"
    if live and run_id is None and live_path.exists():
        graph = load_json_file(live_path)
        return {
            "source": "live",
            "data": graph,
        }

    # Fall back to historical runs
    run_path = get_run_path("05_4_build_relation_graph", run_id)
    graph = load_json_file(run_path / "relation_graph.json")

    return {
        "source": "historical",
        "run_id": run_path.name,
        "data": graph,
    }


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    """List all pipeline runs organized by output type."""
    runs: dict[str, list[dict]] = {}

    output_dirs = [
        "01_fetch_events",
        "02_prepare_nlp_data",
        "03_3_normalize_entities",
        "05_4_build_relation_graph",
        "06_3_export_opportunities",
    ]

    for output_name in output_dirs:
        output_dir = DATA_DIR / output_name
        if not output_dir.exists():
            continue

        output_runs = []
        for run_dir in sorted(output_dir.iterdir(), reverse=True)[:10]:
            if not run_dir.is_dir() or not run_dir.name[0].isdigit():
                continue

            summary_path = run_dir / "summary.json"
            summary = None
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text())
                except json.JSONDecodeError:
                    pass

            output_runs.append(
                {
                    "run_id": run_dir.name,
                    "path": str(run_dir),
                    "summary": summary,
                }
            )

        if output_runs:
            runs[output_name] = output_runs

    return {"runs": runs}


@router.get("/summary/{output_name}")
async def get_summary(
    output_name: str,
    run_id: str | None = Query(
        None, description="Specific run ID, or latest if not specified"
    ),
) -> dict[str, Any]:
    """Get summary.json for a specific output."""
    run_path = get_run_path(output_name, run_id)
    summary = load_json_file(run_path / "summary.json")

    return {
        "run_id": run_path.name,
        "output": output_name,
        "data": summary,
    }


# =============================================================================
# COVERING PORTFOLIOS ENDPOINTS (New System)
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
        (0.00, 4, "LOW_COVERAGE"),
    ]

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

        # Reclassify tier
        tier = 4
        tier_label = "LOW_COVERAGE"
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
    max_tier: int = Query(4, description="Maximum tier to include (1-4, 1=best)"),
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
    - Tier 4: <85% coverage (speculative)

    Use max_tier to filter quality (e.g., max_tier=2 for only Tier 1 and 2).
    Use profitable_only=true to get only portfolios with positive expected profit.
    """
    from server.price_cache import price_cache

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
        live_prices = price_cache.get_prices()
        price_metadata = price_cache.get_metadata()

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


@router.get("/groups")
async def get_groups(
    limit: int = Query(100, description="Max number of groups to return"),
    offset: int = Query(0, description="Number of groups to skip"),
    partition_type: str | None = Query(
        None,
        description="Filter by partition type: 'candidate', 'threshold', 'timeframe'",
    ),
) -> dict[str, Any]:
    """
    Get market groups.

    Market groups organize related markets from a single event.
    Each group contains multiple markets that differ by timeframe,
    threshold, or candidate (e.g., "Election by March" vs "Election by June").

    Partition types:
    - candidate: Markets differ by entity (e.g., different people)
    - threshold: Markets differ by numeric threshold
    - timeframe: Markets differ by date
    """
    live_path = LIVE_DIR / "groups.json"

    # Return empty data if file doesn't exist (pipeline running after reset)
    if not live_path.exists():
        return {
            "source": "live",
            "count": 0,
            "total_count": 0,
            "by_partition": {},
            "data": {"groups": []},
            "meta": {},
        }

    data = load_json_file(live_path)

    # Handle nested format
    if isinstance(data, dict) and "groups" in data:
        groups = data["groups"]
        meta = data.get("_meta", {})
    elif isinstance(data, list):
        groups = data
        meta = {}
    else:
        groups = []
        meta = {}

    # Apply partition type filter
    if partition_type:
        groups = [g for g in groups if g.get("partition_type") == partition_type]

    # Get total before pagination
    total_count = len(groups)

    # Apply pagination
    groups = groups[offset : offset + limit]

    # Count by partition type
    partition_counts = {}
    for g in groups:
        ptype = g.get("partition_type", "unknown")
        partition_counts[ptype] = partition_counts.get(ptype, 0) + 1

    return {
        "source": "live",
        "count": len(groups),
        "total_count": total_count,
        "by_partition": partition_counts,
        "data": {"groups": groups},
        "meta": meta,
    }


@router.get("/implications")
async def get_implications(
    limit: int = Query(100, description="Max number of implications to return"),
    offset: int = Query(0, description="Number of implications to skip"),
) -> dict[str, Any]:
    """
    Get group-level implications (LLM-extracted logical relationships).

    Implications define which groups cover which other groups:
    - yes_covered_by: What covers this group's YES position
    - no_covered_by: What covers this group's NO position

    These are cached forever once extracted.
    """
    try:
        from core.state import load_state

        state = load_state()
        implications = state.get_all_implications()
        state.close()

        # Get total before pagination
        total_count = len(implications)

        # Apply pagination
        implications = implications[offset : offset + limit]

        # Count covers
        total_yes_covers = sum(len(i.get("yes_covered_by", [])) for i in implications)
        total_no_covers = sum(len(i.get("no_covered_by", [])) for i in implications)

        return {
            "source": "live",
            "count": len(implications),
            "total_count": total_count,
            "total_yes_covers": total_yes_covers,
            "total_no_covers": total_no_covers,
            "data": {"implications": implications},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load implications: {e}")
