"""
Incremental processing for single events/groups.

Processes new events as they're detected by polling, without running
the full batch pipeline. Reuses existing step functions with minimal
new code.

Two cases handled:
    Case A: New market in existing group
        - Add markets to group
        - Re-expand pairs (implications already cached)
        - Validate new pairs
        - Rebuild portfolios

    Case B: New event (creates new group)
        - Build group from event
        - Extract implications (bidirectional - already in single LLM call!)
        - Expand pairs (two-way)
        - Validate new pairs
        - Build portfolios

Key insight: The existing LLM prompt already extracts bidirectional
implications (implied_by AND implies), and expand_to_pairs() already
handles two-way expansion. Minimal new code needed.
"""

import os
from dataclasses import asdict
from datetime import datetime, timezone

from loguru import logger

from core.state import PipelineState, export_live_data, load_state
from core.steps.expand import expand_to_pairs
from core.steps.groups import build_group_from_event, extract_markets_from_groups
from core.steps.implications import extract_implications
from core.steps.portfolios import build_portfolios
from core.steps.validate import validate_pairs

# =============================================================================
# CONFIGURATION
# =============================================================================

# LLM models (same as main pipeline) - validated at module load
IMPLICATIONS_LLM_MODEL = os.getenv("IMPLICATIONS_MODEL")
if not IMPLICATIONS_LLM_MODEL:
    raise ValueError("IMPLICATIONS_MODEL environment variable not set")

VALIDATION_LLM_MODEL = os.getenv("VALIDATION_MODEL")
if not VALIDATION_LLM_MODEL:
    raise ValueError("VALIDATION_MODEL environment variable not set")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


async def process_new_event(
    event: dict,
    state: PipelineState | None = None,
) -> dict:
    """
    Process a single new event incrementally.

    This is the main entry point for incremental processing. It handles
    both Case A (market in existing group) and Case B (new event/group).

    Args:
        event: Raw event from Polymarket API (with markets array)
        state: Optional pipeline state (will load if not provided)

    Returns:
        Dict with processing summary:
        - case: "A" or "B"
        - group_id: The group ID processed
        - new_pairs: Number of new pairs generated
        - validated_pairs: Number of pairs that passed validation
        - portfolios: Number of portfolios created/updated
        - elapsed_seconds: Processing time
    """
    start_time = datetime.now(timezone.utc)
    own_state = state is None

    if own_state:
        state = load_state()

    try:
        # Step 1: Build group from event
        group = build_group_from_event(event)
        if not group:
            logger.warning(
                f"Event {event.get('id')} has insufficient markets, skipping"
            )
            return {"skipped": True, "reason": "insufficient_markets"}

        group_dict = asdict(group)
        group_id = group_dict["group_id"]

        # Step 2: Check if this is a new group or existing
        existing_group_ids = state.get_processed_group_ids()
        is_new_group = group_id not in existing_group_ids

        logger.info(
            f"Processing event {group_id}: "
            f"{'NEW GROUP (Case B)' if is_new_group else 'EXISTING GROUP (Case A)'}"
        )

        # Step 3: Add/update group and markets in state
        state.add_groups([group_dict])
        markets = extract_markets_from_groups([group_dict])
        state.add_markets(markets)

        # Step 4: Process based on case
        if is_new_group:
            result = await _handle_new_group(group_dict, state)
        else:
            result = await _handle_existing_group(group_dict, state)

        # Add timing
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        result["elapsed_seconds"] = round(elapsed, 2)
        result["group_id"] = group_id
        result["group_title"] = group_dict.get("title", "")[:60]

        logger.info(
            f"Incremental processing complete: "
            f"{result.get('validated_pairs', 0)} valid pairs, "
            f"{result.get('portfolios', 0)} portfolios "
            f"({elapsed:.1f}s)"
        )

        return result

    finally:
        if own_state:
            state.close()


# =============================================================================
# CASE HANDLERS
# =============================================================================


async def _handle_new_group(
    group: dict,
    state: PipelineState,
) -> dict:
    """
    Case B: Process a completely new event/group.

    Full processing path:
    1. Extract implications (already bidirectional in single LLM call)
    2. Expand to pairs (two-way expansion)
    3. Validate pairs
    4. Build portfolios
    5. Export data
    """
    group_id = group["group_id"]
    new_group_ids = {group_id}

    # Get all groups for context
    all_groups = state.get_all_groups()

    # Step 4a: Extract implications for this new group
    # NOTE: The LLM prompt already extracts BOTH directions:
    # - implied_by: what guarantees this group (other -> this)
    # - implies: what this group guarantees (this -> other)
    logger.info(f"Extracting implications for new group: {group['title'][:50]}...")

    implications = await extract_implications(
        new_groups=[group],
        all_groups=all_groups,
        state=state,
        llm_model=IMPLICATIONS_LLM_MODEL,
    )

    if not implications:
        logger.warning("No implications found for new group")
        return {"case": "B", "new_pairs": 0, "validated_pairs": 0, "portfolios": 0}

    # Step 4b: Expand to market-level pairs
    # NOTE: expand_to_pairs() already handles two-way expansion:
    # - Strategy 1: new_target × all_covers
    # - Strategy 2: all_targets × new_covers
    candidate_pairs, expand_summary = expand_to_pairs(
        implications=implications,
        groups=all_groups,
        state=state,
        new_group_ids=new_group_ids,
    )

    if not candidate_pairs:
        logger.info("No new candidate pairs to validate")
        return {"case": "B", "new_pairs": 0, "validated_pairs": 0, "portfolios": 0}

    logger.info(f"Generated {len(candidate_pairs)} candidate pairs for validation")

    # Step 4c: Validate pairs
    validated_pairs, validate_summary = await validate_pairs(
        candidate_pairs=candidate_pairs,
        state=state,
        llm_model=VALIDATION_LLM_MODEL,
    )

    # Step 4d: Build portfolios
    if validated_pairs:
        # Get ALL validated pairs (cached + new) to rebuild complete portfolio set
        all_validated = state.get_all_validated_pairs()

        # Build complete portfolio list
        portfolios, portfolio_summary = build_portfolios(all_validated)
        state.save_portfolios(portfolios)
    else:
        portfolios = state.get_portfolios()
        portfolio_summary = {"total_portfolios": len(portfolios)}

    # Step 4e: Export to _live/
    export_live_data(state, all_groups, portfolios)

    return {
        "case": "B",
        "new_pairs": len(candidate_pairs),
        "validated_pairs": len(validated_pairs),
        "portfolios": portfolio_summary.get("total_portfolios", len(portfolios)),
    }


async def _handle_existing_group(
    group: dict,
    state: PipelineState,
) -> dict:
    """
    Case A: Process new market(s) added to existing group.

    Lighter processing path (implications already cached):
    1. Re-expand pairs involving this group
    2. Validate new pairs only
    3. Rebuild portfolios
    4. Export data

    NOTE: We don't re-extract implications because they're cached forever.
    The existing implications for this group will be reused.
    """
    group_id = group["group_id"]
    new_group_ids = {group_id}

    # Get all groups and implications
    all_groups = state.get_all_groups()
    implications = state.get_all_implications()

    if not implications:
        logger.warning("No cached implications found")
        return {"case": "A", "new_pairs": 0, "validated_pairs": 0, "portfolios": 0}

    # Step: Re-expand pairs involving this group
    # The two-way expansion will pick up new markets in this group
    candidate_pairs, expand_summary = expand_to_pairs(
        implications=implications,
        groups=all_groups,
        state=state,
        new_group_ids=new_group_ids,
    )

    if not candidate_pairs:
        logger.info("No new candidate pairs (all already validated)")
        # Still re-export with updated prices
        portfolios = state.get_portfolios()
        export_live_data(state, all_groups, portfolios)
        return {
            "case": "A",
            "new_pairs": 0,
            "validated_pairs": 0,
            "portfolios": len(portfolios),
        }

    logger.info(f"Generated {len(candidate_pairs)} candidate pairs for validation")

    # Validate new pairs
    validated_pairs, validate_summary = await validate_pairs(
        candidate_pairs=candidate_pairs,
        state=state,
        llm_model=VALIDATION_LLM_MODEL,
    )

    # Rebuild portfolios
    if validated_pairs:
        all_validated = state.get_all_validated_pairs()
        portfolios, portfolio_summary = build_portfolios(all_validated)
        state.save_portfolios(portfolios)
    else:
        portfolios = state.get_portfolios()
        portfolio_summary = {"total_portfolios": len(portfolios)}

    # Export
    export_live_data(state, all_groups, portfolios)

    return {
        "case": "A",
        "new_pairs": len(candidate_pairs),
        "validated_pairs": len(validated_pairs),
        "portfolios": portfolio_summary.get("total_portfolios", len(portfolios)),
    }


# =============================================================================
# BATCH PROCESSING (for multiple new events)
# =============================================================================


async def process_new_events_batch(
    events: list[dict],
    state: PipelineState | None = None,
) -> dict:
    """
    Process multiple new events as a batch.

    More efficient than processing one at a time because:
    - Single state connection
    - Batched exports
    - Shared LLM context

    Args:
        events: List of raw events from Polymarket API
        state: Optional pipeline state

    Returns:
        Summary dict with aggregated stats
    """
    if not events:
        return {"processed": 0, "skipped": 0}

    start_time = datetime.now(timezone.utc)
    own_state = state is None

    if own_state:
        state = load_state()

    try:
        results = []
        skipped = 0

        for event in events:
            try:
                result = await process_new_event(event, state)
                if result.get("skipped"):
                    skipped += 1
                else:
                    results.append(result)
            except Exception as e:
                logger.error(f"Error processing event {event.get('id')}: {e}")
                skipped += 1

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        return {
            "processed": len(results),
            "skipped": skipped,
            "total_new_pairs": sum(r.get("new_pairs", 0) for r in results),
            "total_validated_pairs": sum(r.get("validated_pairs", 0) for r in results),
            "elapsed_seconds": round(elapsed, 2),
        }

    finally:
        if own_state:
            state.close()
