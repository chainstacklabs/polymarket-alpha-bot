"""
Production pipeline runner for covering portfolios.

9-step pipeline that finds hedging opportunities:
1. Fetch events from Polymarket
2. Build market groups from events
3. Identify new groups (incremental)
4. Extract implications via LLM (CACHED)
5. Expand to market-level pairs (two-way)
6. Validate pairs via LLM (CACHED)
7. Build portfolios with metrics
8. Export data to _live/
9. (Background) Update prices

Usage:
    from core.runner import run
    run()           # Incremental (default)
    run(full=True)  # Full reprocessing
"""

import asyncio
import json
import os
from datetime import datetime, timezone

from loguru import logger

from core.models import close_all_llm_clients
from core.state import export_live_data, load_state
from core.step_tracker import StepTracker
from core.steps.expand import expand_all_to_pairs, expand_to_pairs
from core.steps.fetch import fetch_events
from core.steps.groups import build_groups, extract_markets_from_groups
from core.steps.implications import extract_implications_batch
from core.steps.portfolios import build_and_save_portfolios
from core.steps.validate import validate_pairs

# =============================================================================
# CONFIGURATION
# =============================================================================

# LLM model for implications (cheaper, good enough for relationship extraction)
IMPLICATIONS_LLM_MODEL = os.getenv("IMPLICATIONS_MODEL")
if not IMPLICATIONS_LLM_MODEL:
    raise ValueError("IMPLICATIONS_MODEL environment variable not set")

# LLM model for validation (more expensive, needs reasoning for temporal logic)
VALIDATION_LLM_MODEL = os.getenv("VALIDATION_MODEL")
if not VALIDATION_LLM_MODEL:
    raise ValueError("VALIDATION_MODEL environment variable not set")


# =============================================================================
# MAIN RUNNER
# =============================================================================


async def run_async(
    full: bool = False,
    step_tracker: StepTracker | None = None,
    max_events: int | None = None,
    implications_model: str | None = None,
    validation_model: str | None = None,
    quiet: bool = False,
) -> dict:
    """
    Run the covering portfolios pipeline.

    Args:
        full: If True, reprocess everything. If False, incremental.
        step_tracker: Optional tracker for progress monitoring.
        max_events: Optional limit on events to fetch (for demo/testing).
        implications_model: Override LLM model for implications.
        validation_model: Override LLM model for validation.
        quiet: If True, suppress console output (for API/background runs).

    Returns:
        Dict with run statistics
    """
    start_time = datetime.now(timezone.utc)

    # Create tracker if not provided (with quiet mode support)
    tracker = step_tracker or StepTracker(quiet=quiet)

    # Print pipeline start banner
    mode = "full" if full else "incremental"
    tracker.print_pipeline_start(mode)

    # Load state
    state = load_state()

    # Clean up any orphaned runs
    orphaned = state.cleanup_orphaned_runs()
    if orphaned > 0:
        logger.warning(f"Cleaned up {orphaned} orphaned run(s)")

    if full:
        state.reset()

    # Start run tracking
    run_id = state.start_run("full" if full else "refresh")

    # Resolve LLM models
    impl_model = implications_model or IMPLICATIONS_LLM_MODEL
    val_model = validation_model or VALIDATION_LLM_MODEL

    try:
        # =====================================================================
        # STEP 1: Fetch events from Polymarket
        # =====================================================================
        with tracker.step(1, "Fetch Markets"):
            all_events = await fetch_events(max_events=max_events)
            tracker.update_details(f"Fetched {len(all_events)} events")

        # =====================================================================
        # STEP 2: Build market groups
        # =====================================================================
        with tracker.step(2, "Build Groups"):
            groups, groups_summary = build_groups(all_events)
            tracker.update_details(
                f"{groups_summary['groups_count']} groups, "
                f"{groups_summary['total_markets']} markets"
            )

        # =====================================================================
        # STEP 3: Identify new groups
        # =====================================================================
        with tracker.step(3, "Detect New"):
            all_group_ids = [g["group_id"] for g in groups]
            new_group_ids = state.get_new_group_ids(all_group_ids)
            new_groups = [g for g in groups if g["group_id"] in new_group_ids]

            tracker.update_details(f"{len(new_groups)} new of {len(groups)} total")

            # Save all groups to state
            state.add_groups(groups)
            markets = extract_markets_from_groups(groups)
            state.add_markets(markets)

        # Handle no new groups case
        if not new_groups and not full:
            # Get existing portfolios and recalculate with new prices
            existing_portfolios = state.get_portfolios()

            if existing_portfolios:
                # Update prices in existing portfolios
                from core.steps.portfolios import update_portfolio_prices

                # Build price updates from groups
                price_updates = {}
                for group in groups:
                    for market in group.get("markets", []):
                        price_updates[market["id"]] = {
                            "price_yes": market.get("price_yes", 0.5),
                            "price_no": market.get("price_no", 0.5),
                        }

                portfolios, price_summary = update_portfolio_prices(
                    state, price_updates
                )
                export_live_data(state, groups, portfolios)

                state.complete_run(run_id, len(all_events), 0, "completed")

                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                result = {
                    "mode": "price_update",
                    "total_events": len(all_events),
                    "total_groups": len(groups),
                    "new_groups": 0,
                    "portfolios": len(portfolios),
                    "prices_updated": price_summary.get("prices_updated", 0),
                    "elapsed_seconds": elapsed,
                }
                tracker.print_price_update(result)
                return result

            state.complete_run(run_id, 0, 0, "skipped")
            tracker.print_skip("No new groups and no existing portfolios")
            return {"mode": "skipped", "reason": "no_portfolios"}

        # =====================================================================
        # STEP 4: Extract implications (LLM, CACHED)
        # =====================================================================
        with tracker.step(4, "Find Implications"):
            implications = await extract_implications_batch(
                groups=new_groups,
                all_groups=groups,
                state=state,
                llm_model=impl_model,
                batch_size=10,
                max_concurrent=8,
                progress_callback=tracker.update_details,
            )

            total_yes = sum(len(i.get("yes_covered_by", [])) for i in implications)
            total_no = sum(len(i.get("no_covered_by", [])) for i in implications)

            tracker.update_details(
                f"{len(implications)} implications ({total_yes} YES, {total_no} NO)"
            )

        # =====================================================================
        # STEP 5: Expand to market-level pairs (two-way)
        # =====================================================================
        with tracker.step(5, "Expand Pairs"):
            if full:
                # Full expansion without cache check
                candidate_pairs, expand_summary = expand_all_to_pairs(
                    implications, groups
                )
            else:
                # Incremental two-way expansion
                candidate_pairs, expand_summary = expand_to_pairs(
                    implications=implications,
                    groups=groups,
                    state=state,
                    new_group_ids=new_group_ids,
                )

            new_pairs = expand_summary.get("new_pairs", expand_summary["total_pairs"])
            tracker.update_details(
                f"{expand_summary['total_pairs']} pairs ({new_pairs} new)"
            )

        # =====================================================================
        # STEP 6: Validate pairs (LLM, CACHED)
        # =====================================================================
        with tracker.step(6, "Validate Logic"):
            if not candidate_pairs:
                validated_pairs = []
                validate_summary = {"validated_count": 0, "retention_rate": 0}
            else:
                validated_pairs, validate_summary = await validate_pairs(
                    candidate_pairs=candidate_pairs,
                    state=state,
                    llm_model=val_model,
                    progress_callback=tracker.update_details,
                )

            retention = validate_summary.get("retention_rate", 0)
            tracker.update_details(
                f"{validate_summary['validated_count']} valid ({retention:.0%} kept)"
            )

        # =====================================================================
        # STEP 7: Build portfolios
        # =====================================================================
        with tracker.step(7, "Build Portfolios"):
            if not validated_pairs:
                portfolios = []
                portfolio_summary = {"total_portfolios": 0, "by_tier": {}}
            else:
                portfolios, portfolio_summary = build_and_save_portfolios(
                    validated_pairs=validated_pairs,
                    state=state,
                )

            tier_counts = portfolio_summary.get("by_tier", {})
            tier_str = ", ".join(
                f"T{k.replace('tier_', '')}: {v['count']}"
                for k, v in tier_counts.items()
                if v.get("count", 0) > 0
            )

            count = portfolio_summary["total_portfolios"]
            if tier_str:
                tracker.update_details(f"{count} portfolios ({tier_str})")
            else:
                tracker.update_details(f"{count} portfolios")

        # =====================================================================
        # STEP 8: Export data
        # =====================================================================
        with tracker.step(8, "Export Data"):
            export_live_data(state, groups, portfolios)
            tracker.update_details("Saved to data/_live/")

        # Complete run
        state.complete_run(run_id, len(all_events), len(new_groups), "completed")

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        result = {
            "mode": "full" if full else "incremental",
            "total_events": len(all_events),
            "total_groups": len(groups),
            "new_groups": len(new_groups),
            "implications": len(implications),
            "candidate_pairs": len(candidate_pairs),
            "validated_pairs": len(validated_pairs),
            "portfolios": len(portfolios),
            "portfolio_summary": portfolio_summary,
            "elapsed_seconds": elapsed,
        }

        tracker.print_pipeline_complete(result)
        return result

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        state.complete_run(run_id, 0, 0, "failed")
        raise

    finally:
        # Cleanup LLM clients
        await close_all_llm_clients()
        state.close()


def run(
    full: bool = False,
    step_tracker: StepTracker | None = None,
    max_events: int | None = None,
    implications_model: str | None = None,
    validation_model: str | None = None,
    quiet: bool = False,
) -> dict:
    """
    Run the pipeline synchronously.

    Args:
        full: If True, reprocess everything. If False, incremental.
        step_tracker: Optional tracker for progress monitoring.
        max_events: Optional limit on events to fetch.
        implications_model: Override LLM model for implications.
        validation_model: Override LLM model for validation.
        quiet: If True, suppress console output (for API/background runs).

    Returns:
        Dict with run statistics
    """
    return asyncio.run(
        run_async(
            full=full,
            step_tracker=step_tracker,
            max_events=max_events,
            implications_model=implications_model,
            validation_model=validation_model,
            quiet=quiet,
        )
    )


# =============================================================================
# CLI ENTRY POINT
# =============================================================================


def main():
    """CLI entry point."""
    import sys

    full = "--full" in sys.argv or "-f" in sys.argv

    # Check for model overrides
    impl_model = None
    val_model = None

    for i, arg in enumerate(sys.argv):
        if arg == "--impl-model" and i + 1 < len(sys.argv):
            impl_model = sys.argv[i + 1]
        elif arg == "--val-model" and i + 1 < len(sys.argv):
            val_model = sys.argv[i + 1]

    try:
        result = run(
            full=full,
            implications_model=impl_model,
            validation_model=val_model,
        )
        print(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
