"""
Production pipeline runner.

Single-process incremental pipeline that:
- Loads models once, keeps in memory
- Uses SQLite for O(1) state lookups
- Processes only new events (incremental mode)
- Merges results into accumulated _live/ state

Usage:
    from core.runner import run
    run()           # Incremental (default)
    run(full=True)  # Full reprocessing
"""

import asyncio
import json
from datetime import datetime, timezone

from loguru import logger

from core.models import get_llm_client, preload_models
from core.state import (
    GraphData,
    export_live_data,
    load_state,
)
from core.steps.alpha import run_alpha_detection
from core.steps.arbitrage import run_arbitrage_detection
from core.steps.embeddings import embed_events
from core.steps.entities import extract_and_process_entities
from core.steps.fetch import extract_prices, fetch_events
from core.steps.prepare import prepare_nlp_data
from core.steps.relations import (
    _build_edge_from_relation,
    block_candidate_pairs,
    build_relation_graph,
    classify_causal,
    classify_structural,
    merge_into_graph,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

MAX_LLM_PAIRS = 10000  # Limit LLM calls for causal classification


# =============================================================================
# MAIN RUNNER
# =============================================================================

# Import step tracker for progress monitoring (imported here to avoid circular imports)
from core.step_tracker import StepTracker


async def run_async(
    full: bool = False,
    step_tracker: StepTracker | None = None,
    max_events: int | None = None,
) -> dict:
    """
    Run the pipeline asynchronously.

    Args:
        full: If True, reprocess everything. If False, incremental.
        step_tracker: Optional tracker for progress monitoring.
        max_events: Optional limit on number of events to fetch.
                    Useful for demo/testing with smaller datasets.

    Returns:
        Dict with run statistics
    """
    start_time = datetime.now(timezone.utc)
    logger.info(f"Starting pipeline run (mode: {'full' if full else 'incremental'})")

    # Create tracker if not provided (for CLI/standalone usage)
    tracker = step_tracker or StepTracker()

    # Load state
    state = load_state()

    # Check for and clean up orphaned runs (crashed/interrupted runs)
    orphaned_count = state.cleanup_orphaned_runs()
    if orphaned_count > 0:
        logger.warning(
            f"Cleaned up {orphaned_count} orphaned run(s) from previous crashes"
        )

    if full:
        logger.warning("Full mode: resetting state...")
        state.reset()

    # Start run tracking
    run_id = state.start_run("full" if full else "refresh")

    try:
        # =====================================================================
        # STEP 1: Fetch all events from API
        # =====================================================================
        with tracker.step(1, "Fetch Events"):
            if max_events:
                logger.info(
                    f"Step 1: Fetching events from Polymarket API (max: {max_events})..."
                )
            else:
                logger.info("Step 1: Fetching events from Polymarket API...")
            all_events = await fetch_events(max_events=max_events)
            tracker.update_details(f"Fetched {len(all_events)} events")
            logger.info(f"Fetched {len(all_events)} events")

        # =====================================================================
        # STEP 2: Identify new events
        # =====================================================================
        with tracker.step(2, "Identify New Events"):
            all_ids = [e["id"] for e in all_events]
            new_ids = state.get_new_ids(all_ids)
            new_events = [e for e in all_events if e["id"] in new_ids]
            tracker.update_details(
                f"Found {len(new_events)} new of {len(all_events)} total"
            )
            logger.info(
                f"Total events: {len(all_events)}, New events: {len(new_events)}"
            )

        # =====================================================================
        # STEP 3: Handle no new events case
        # =====================================================================
        if not new_events and not full:
            # Adjust total steps for this shorter path (steps 1-3 only)
            tracker.total_steps = 3
            with tracker.step(3, "Update Prices Only"):
                logger.info("No new events - updating prices only...")

                # Update prices in state
                prices = extract_prices(all_events)
                state.update_event_prices(prices)
                tracker.update_details(f"Updated prices for {len(prices)} events")

                # Load existing graph and run alpha detection with new prices
                graph = state.get_graph()
                if graph.nodes:
                    opportunities, summary = run_alpha_detection(
                        graph.to_dict(), all_events
                    )
                    export_live_data(state, all_events, opportunities)

                    state.complete_run(run_id, len(all_events), 0, "completed")

                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                    logger.info(f"Price update complete in {elapsed:.1f}s")

                    return {
                        "mode": "price_update",
                        "total_events": len(all_events),
                        "new_events": 0,
                        "opportunities": len(opportunities),
                        "elapsed_seconds": elapsed,
                    }

                logger.warning("No existing graph - nothing to update")
                state.complete_run(run_id, 0, 0, "skipped")
                return {"mode": "skipped", "reason": "no_graph"}

        # =====================================================================
        # STEP 3: Preload models (do this before heavy processing)
        # =====================================================================
        # Step 3 "Update Prices Only" was skipped (only runs when no new events)
        # So we continue with consecutive numbering: 3-13 (11 steps after 1-2)
        tracker.total_steps = 14
        with tracker.step(3, "Load ML Models"):
            logger.info("Step 3: Loading ML models...")
            preload_models()
            tracker.update_details("Loaded GLiNER, embedder, LLM client")

        # =====================================================================
        # STEP 4: Prepare NLP data for new events
        # =====================================================================
        with tracker.step(4, "Prepare NLP Data"):
            logger.info("Step 4: Preparing NLP data...")
            nlp_events = prepare_nlp_data(new_events)
            tracker.update_details(f"Prepared {len(nlp_events)} events")
            logger.info(f"Prepared {len(nlp_events)} events for NLP")

        # =====================================================================
        # STEP 5: Extract entities
        # =====================================================================
        with tracker.step(5, "Extract Entities"):
            logger.info("Step 5: Extracting entities...")
            entities = extract_and_process_entities(nlp_events, state)
            tracker.update_details(f"Extracted {len(entities)} entities")
            logger.info(f"Extracted {len(entities)} entities")

        # =====================================================================
        # STEP 6: Extract event semantics
        # =====================================================================
        with tracker.step(6, "Extract Semantics"):
            logger.info("Step 6: Extracting event semantics...")
            from core.steps.semantics import (
                extract_event_semantics,
                get_semantics_for_prioritization,
            )

            semantics_by_id = await extract_event_semantics(
                nlp_events, state, progress_callback=tracker.update_details
            )
            semantics_for_pairs = get_semantics_for_prioritization(semantics_by_id)
            tracker.update_details(
                f"Extracted semantics for {len(semantics_by_id)} events"
            )
            logger.info(f"Extracted semantics for {len(semantics_by_id)} events")

        # =====================================================================
        # STEP 7: Generate embeddings
        # =====================================================================
        with tracker.step(7, "Generate Embeddings"):
            logger.info("Step 7: Generating embeddings...")
            new_embeddings, new_event_ids = embed_events(nlp_events, state)
            tracker.update_details(f"Generated {len(new_event_ids)} embeddings")
            logger.info(f"Generated embeddings for {len(new_event_ids)} events")

        # =====================================================================
        # STEP 8: Enrich quality
        # =====================================================================
        with tracker.step(8, "Enrich Quality"):
            logger.info("Step 8: Enriching quality...")
            from core.steps.quality import enrich_events_quality

            entity_sets = {e["id"]: e.get("entities", []) for e in nlp_events}
            _enriched_events, negation_pairs = enrich_events_quality(
                nlp_events, entity_sets
            )
            tracker.update_details(f"Found {len(negation_pairs)} negation pairs")
            logger.info(f"Quality enriched: {len(negation_pairs)} negation pairs found")

        # =====================================================================
        # STEP 9: Find candidate pairs (new vs all)
        # =====================================================================
        with tracker.step(9, "Find Candidate Pairs"):
            logger.info("Step 9: Finding candidate pairs...")

            # Get all embeddings and event IDs
            all_embeddings, all_event_ids = state.get_embeddings()

            if all_embeddings is None or len(all_embeddings) == 0:
                # First run - use only new embeddings
                all_embeddings = new_embeddings
                all_event_ids = new_event_ids

            # Prepare all events lookup
            existing_events = state.get_all_events()
            all_events_for_pairs = existing_events + nlp_events

            # Build entities lookup for shared entity filtering
            from core.steps.entities import get_entities_by_event

            all_entities = state.get_all_entities()
            entities_by_event = get_entities_by_event(all_entities + entities)

            candidate_pairs = block_candidate_pairs(
                new_events=nlp_events,
                all_events=all_events_for_pairs,
                new_embeddings=new_embeddings,
                all_embeddings=all_embeddings,
                all_event_ids=all_event_ids,
                entities_by_event=entities_by_event,
            )
            tracker.update_details(f"Found {len(candidate_pairs)} pairs")
            logger.info(f"Found {len(candidate_pairs)} candidate pairs")

        # =====================================================================
        # STEP 10: Classify structural relations
        # =====================================================================
        with tracker.step(10, "Classify Structural"):
            logger.info("Step 10: Classifying structural relations...")
            events_by_id = {e["id"]: e for e in all_events_for_pairs}
            structural_relations = classify_structural(
                candidate_pairs, events_by_id, semantics_by_id=semantics_for_pairs
            )

            # Add negation pairs as MUTUALLY_EXCLUSIVE (from quality enrichment)
            for pair in negation_pairs:
                structural_relations.append(
                    {
                        "source_id": pair.event_id_a,
                        "target_id": pair.event_id_b,
                        "relation_type": "MUTUALLY_EXCLUSIVE",
                        "confidence": 0.9,
                        "classification_method": "negation_detection",
                    }
                )

            tracker.update_details(f"Found {len(structural_relations)} relations")
            logger.info(f"Found {len(structural_relations)} structural relations")

        # =====================================================================
        # STEP 11: Classify causal relations (LLM)
        # =====================================================================
        with tracker.step(11, "Classify Causal (LLM)"):
            logger.info("Step 11: Classifying causal relations (LLM)...")

            # Filter pairs not already classified as structural
            structural_pairs = {
                (r["source_id"], r["target_id"]) for r in structural_relations
            }
            pairs_for_causal = [
                p
                for p in candidate_pairs
                if (p["event_a_id"], p["event_b_id"]) not in structural_pairs
                and (p["event_b_id"], p["event_a_id"]) not in structural_pairs
            ][:MAX_LLM_PAIRS]

            tracker.update_details(f"Classifying {len(pairs_for_causal)} pairs...")
            causal_relations = await classify_causal(
                pairs_for_causal,
                events_by_id,
                semantics_by_id=semantics_for_pairs,
                entities_by_event=entities_by_event,
                max_pairs=MAX_LLM_PAIRS,
                progress_callback=tracker.update_details,
            )
            tracker.update_details(f"Found {len(causal_relations)} causal relations")
            logger.info(f"Found {len(causal_relations)} causal relations")

        # =====================================================================
        # STEP 12: Build/merge graph
        # =====================================================================
        with tracker.step(12, "Build Graph"):
            logger.info("Step 12: Building relation graph...")

            # Get existing graph
            existing_graph = state.get_graph()

            if existing_graph.nodes and not full:
                # Incremental: merge into existing graph
                new_graph_nodes = [
                    {
                        "id": e["id"],
                        "title": e.get("title", ""),
                        "current_price": extract_prices([e]).get(e["id"], 0.5),
                    }
                    for e in nlp_events
                ]
                # Build edges with full metadata
                new_graph_edges = [
                    _build_edge_from_relation(r)
                    for r in structural_relations + causal_relations
                ]

                merged = merge_into_graph(
                    existing_graph.to_dict(), new_graph_nodes, new_graph_edges
                )
                graph = GraphData.from_dict(merged)
            else:
                # Full: build new graph
                graph_dict = build_relation_graph(
                    all_events_for_pairs,
                    structural_relations,
                    causal_relations,
                )
                graph = GraphData.from_dict(graph_dict)

            # Save graph (to JSON file and edges to SQLite)
            state.save_graph(graph)
            # Map edge keys: graph uses source/target, DB expects source_id/target_id
            edges_for_db = [
                {
                    "source_id": e["source"],
                    "target_id": e["target"],
                    "relation_type": e["relation_type"],
                    "confidence": e.get("confidence", 0.5),
                }
                for e in graph.edges
            ]
            state.add_graph_edges(edges_for_db)
            tracker.update_details(
                f"{len(graph.nodes)} nodes, {len(graph.edges)} edges"
            )
            logger.info(f"Graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

        # =====================================================================
        # STEP 13: Alpha detection (conditional probability)
        # =====================================================================
        with tracker.step(13, "Detect Alpha"):
            logger.info("Step 13: Detecting conditional alpha opportunities...")
            conditional_opps, _ = run_alpha_detection(graph.to_dict(), all_events)
            # Tag with opportunity type
            for opp in conditional_opps:
                opp["opportunity_type"] = "conditional"
            tracker.update_details(f"Found {len(conditional_opps)} conditional")
            logger.info(
                f"Found {len(conditional_opps)} conditional alpha opportunities"
            )

        # =====================================================================
        # STEP 14: Cross-market arbitrage detection
        # =====================================================================
        with tracker.step(14, "Detect Arbitrage"):
            logger.info("Step 14: Detecting cross-market arbitrage...")
            all_embeddings, all_event_ids = state.get_embeddings()

            arbitrage_opps: list[dict] = []
            if all_embeddings is not None and len(all_embeddings) > 0:
                arbitrage_opps, arb_summary = await run_arbitrage_detection(
                    all_events, all_embeddings, all_event_ids
                )
                tracker.update_details(
                    f"Found {len(arbitrage_opps)} arbitrage from "
                    f"{arb_summary.get('candidates_analyzed', 0)} candidates"
                )
                logger.info(f"Found {len(arbitrage_opps)} arbitrage opportunities")
            else:
                tracker.update_details("No embeddings available")
                logger.warning("No embeddings available for arbitrage detection")

            # Combine all opportunities
            all_opportunities = conditional_opps + arbitrage_opps

            # Save state and export
            state.add_events(nlp_events)
            export_live_data(state, all_events, all_opportunities)

        # Complete run
        state.complete_run(run_id, len(all_events), len(new_events), "completed")

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(f"Pipeline complete in {elapsed:.1f}s")

        return {
            "mode": "full" if full else "incremental",
            "total_events": len(all_events),
            "new_events": len(new_events),
            "entities": len(entities),
            "structural_relations": len(structural_relations),
            "causal_relations": len(causal_relations),
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "opportunities": len(all_opportunities),
            "conditional_opportunities": len(conditional_opps),
            "arbitrage_opportunities": len(arbitrage_opps),
            "elapsed_seconds": elapsed,
        }

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        state.complete_run(run_id, 0, 0, "failed")
        raise

    finally:
        # Cleanup LLM client
        llm = get_llm_client()
        await llm.close()
        state.close()


def run(
    full: bool = False,
    step_tracker: StepTracker | None = None,
    max_events: int | None = None,
) -> dict:
    """
    Run the pipeline synchronously.

    Args:
        full: If True, reprocess everything. If False, incremental.
        step_tracker: Optional tracker for progress monitoring.
        max_events: Optional limit on number of events to fetch.
                    Useful for demo/testing with smaller datasets.

    Returns:
        Dict with run statistics
    """
    return asyncio.run(
        run_async(full, step_tracker=step_tracker, max_events=max_events)
    )


# =============================================================================
# CLI ENTRY POINT
# =============================================================================


def main():
    """CLI entry point."""
    import sys

    full = "--full" in sys.argv or "-f" in sys.argv

    try:
        result = run(full=full)
        print(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
