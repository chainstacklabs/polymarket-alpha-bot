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


def transform_to_frontend_format(opportunities: list[dict]) -> list[dict]:
    """Transform opportunities from flat format to nested format expected by frontend.

    Backend format (flat):
        alpha_signal, alpha_direction, confidence, relation_type, trigger.current_price

    Frontend format (nested):
        alpha.signal, alpha.direction, relation.type, relation.confidence, trigger.price
    """
    transformed = []

    for opp in opportunities:
        # Extract values from flat format
        alpha_signal = opp.get("alpha_signal", 0)
        alpha_direction = opp.get("alpha_direction", "BUY")
        confidence = opp.get("confidence", 0.7)
        relation_type = opp.get("relation_type", "UNKNOWN")

        # Build nested structure
        result = {
            "id": opp.get("id", opp.get("signal_id", "")),
            "rank": opp.get("rank", 0),
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
    live: bool = Query(True, description="Use live data (default) or historical"),
    run_id: str | None = Query(None, description="Specific historical run ID"),
) -> dict[str, Any]:
    """Get alpha opportunities with live price recalculation.

    By default, returns live accumulated data from the production pipeline,
    with alpha signals recalculated using current market prices.
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

        # Get live prices and recalculate alpha
        live_prices = price_cache.get_prices()
        metadata = price_cache.get_metadata()

        if live_prices:
            opportunities = recalculate_opportunities_with_live_prices(
                opportunities, live_prices
            )

        # Apply limit after re-sorting
        opportunities = opportunities[:limit]

        # Transform to frontend format (nested structure)
        opportunities = transform_to_frontend_format(opportunities)

        return {
            "source": "live",
            "count": len(opportunities),
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
    opportunities = load_json_file(run_path / "opportunities.json")

    if isinstance(opportunities, list):
        opportunities = opportunities[:limit]

    return {
        "source": "historical",
        "run_id": run_path.name,
        "count": len(opportunities) if isinstance(opportunities, list) else 1,
        "data": opportunities,
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
