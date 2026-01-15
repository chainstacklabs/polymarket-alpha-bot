"""
05a: Expand Group Implications to Market-Level Pairs.

WHAT IT DOES
    Explodes group-level relationships into ALL possible market combinations.
    Each group has multiple markets (different deadlines), so one group
    relationship becomes N × M market pairs.

    Example: Groups "Election called by...?" and "Election held by...?"
    Each has 4 markets (March, June, Sept, Dec). One implication becomes
    4 × 4 = 16 candidate pairs to evaluate.

WHY WE NEED THIS
    Group-level implications don't account for TIMING. The relationship
    "election held → election called" is true, but specific deadlines matter:

    - "Election held by Dec" + "Called by June" = VALID
      (if held by Dec, was definitely called by June)

    - "Election held by March" + "Called by Dec" = INVALID
      (cover resolves in Dec, but we need protection in March!)

    We generate ALL combinations here, then filter invalid ones in 05b.

HOW IT WORKS
    1. For each group with covers, get all target markets
    2. For each cover relationship, get all cover group markets
    3. Create cartesian product: every target × every cover market
    4. Attach prices, deadlines, and original confidence scores
    5. No filtering - pure expansion for next step to validate

PIPELINE
    04_filter_implications → [05a_expand_to_markets] → 05b_validate_candidates

INPUT
    data/04_filter_implications/<timestamp>/
        - filtered_implications.json: Group-level covers with confidence
    data/02_build_groups/<timestamp>/
        - groups.json: Market details (prices, deadlines)

OUTPUT
    data/05a_expand_to_markets/<timestamp>/
        - market_candidates.json : All target × cover market pairs
        - summary.json           : Expansion stats

RUNTIME
    <5 seconds (cartesian product expansion)

CONFIGURATION
    - INPUT_RUN_FOLDER: Specific timestamp (None = latest)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
IMPLICATIONS_DIR = DATA_DIR / "04_filter_implications"
GROUPS_DIR = DATA_DIR / "02_build_groups"
OUTPUT_DIR = DATA_DIR / "05a_expand_to_markets"

INPUT_RUN_FOLDER: str | None = None

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


def get_all_cover_markets(covering_group: dict) -> list[dict]:
    """
    Get all markets from covering group. No filtering - pure propagation.
    """
    return [
        {
            "market_id": m["id"],
            "question": m.get("question", ""),
            "resolution_date": m.get("resolution_date"),
            "price_yes": m.get("price_yes"),
            "price_no": m.get("price_no"),
            "bracket_label": m.get("bracket_label"),
        }
        for m in covering_group.get("markets", [])
    ]


# =============================================================================
# PROPAGATION
# =============================================================================


def collect_covering_candidates(
    covering_groups: list[dict],
    groups_by_id: dict[str, dict],
    target_group_id: str,
) -> list[dict]:
    """
    Collect ALL covering candidates - pure propagation, no filtering.
    """
    candidates = []

    for cov_info in covering_groups:
        if cov_info["group_id"] == target_group_id:
            continue

        cov_group = groups_by_id.get(cov_info["group_id"])
        if not cov_group:
            continue

        cov_partition = cov_group.get("partition_type", "unknown")
        prob = cov_info.get("probability") or cov_info.get("confidence", 0)

        for cm in get_all_cover_markets(cov_group):
            candidates.append(
                {
                    **cm,
                    "source_group_id": cov_info["group_id"],
                    "source_group_title": cov_info["title"],
                    "cover_position": cov_info.get("cover_position", "YES"),
                    "relationship": cov_info.get("relationship", ""),
                    "relationship_type": cov_info.get("relationship_type", "causal"),
                    "probability": prob,
                    "cover_partition_type": cov_partition,
                }
            )

    return candidates


def propagate_implications(
    implications: list[dict],
    groups_by_id: dict[str, dict],
) -> list[dict]:
    """
    Propagate group-level implications to market-level candidates.
    Pure propagation - no filtering, no decisions.
    """
    results = []

    for impl in implications:
        target_group_id = impl["group_id"]
        target_group = groups_by_id.get(target_group_id)

        if not target_group:
            logger.warning(f"Target group {target_group_id} not found in groups")
            continue

        yes_covering_groups = impl.get("yes_covered_by", [])
        no_covering_groups = impl.get("no_covered_by", [])

        market_entries = []
        for target_market in target_group.get("markets", []):
            market_entries.append(
                {
                    "market_id": target_market["id"],
                    "question": target_market.get("question", ""),
                    "resolution_date": target_market.get("resolution_date"),
                    "price_yes": target_market.get("price_yes"),
                    "price_no": target_market.get("price_no"),
                    "bracket_label": target_market.get("bracket_label"),
                    "yes_covering_candidates": collect_covering_candidates(
                        yes_covering_groups, groups_by_id, target_group_id
                    ),
                    "no_covering_candidates": collect_covering_candidates(
                        no_covering_groups, groups_by_id, target_group_id
                    ),
                }
            )

        results.append(
            {
                "target_group_id": target_group_id,
                "target_group_title": impl["title"],
                "partition_type": target_group.get("partition_type", "unknown"),
                "markets": market_entries,
            }
        )

    return results


def compute_stats(propagated: list[dict]) -> dict:
    """Compute propagation statistics."""
    total_target_markets = 0
    total_yes_candidates = 0
    total_no_candidates = 0
    markets_with_yes = 0
    markets_with_no = 0
    markets_with_both = 0

    for group in propagated:
        for market in group.get("markets", []):
            total_target_markets += 1
            yes_count = len(market.get("yes_covering_candidates", []))
            no_count = len(market.get("no_covering_candidates", []))
            total_yes_candidates += yes_count
            total_no_candidates += no_count

            if yes_count > 0:
                markets_with_yes += 1
            if no_count > 0:
                markets_with_no += 1
            if yes_count > 0 and no_count > 0:
                markets_with_both += 1

    return {
        "total_target_groups": len(propagated),
        "total_target_markets": total_target_markets,
        "total_yes_candidates": total_yes_candidates,
        "total_no_candidates": total_no_candidates,
        "markets_with_yes_covers": markets_with_yes,
        "markets_with_no_covers": markets_with_no,
        "markets_with_both_covers": markets_with_both,
        "avg_yes_per_market": round(total_yes_candidates / total_target_markets, 2)
        if total_target_markets
        else 0,
        "avg_no_per_market": round(total_no_candidates / total_target_markets, 2)
        if total_target_markets
        else 0,
    }


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("STEP 05: Expand to Market Combinations")
    logger.info("=" * 70)

    # Load filtered implications
    impl_folder = (
        IMPLICATIONS_DIR / INPUT_RUN_FOLDER
        if INPUT_RUN_FOLDER
        else get_latest_folder(IMPLICATIONS_DIR)
    )
    if not impl_folder:
        logger.error("No implications folder found")
        return

    impl_file = impl_folder / "filtered_implications.json"
    if not impl_file.exists():
        logger.error(f"File not found: {impl_file}")
        return

    logger.info(f"Loading implications from: {impl_file}")
    with open(impl_file, encoding="utf-8") as f:
        impl_data = json.load(f)

    implications = impl_data.get("implications", [])
    logger.info(f"Loaded {len(implications)} group implications")

    # Load groups (use same run timestamp)
    run_timestamp = impl_folder.name
    groups_file = GROUPS_DIR / run_timestamp / "groups.json"

    if not groups_file.exists():
        logger.error(f"Groups file not found: {groups_file}")
        return

    logger.info(f"Loading groups from: {groups_file}")
    with open(groups_file, encoding="utf-8") as f:
        groups_data = json.load(f)

    groups = groups_data.get("groups", [])
    groups_by_id = {g["group_id"]: g for g in groups}
    logger.info(f"Loaded {len(groups)} groups")

    # Propagate - pure expansion, no filtering
    logger.info("\nPropagating to market level (all combinations)...")
    propagated = propagate_implications(implications, groups_by_id)

    # Compute stats
    stats = compute_stats(propagated)

    # Prepare output
    output_folder = OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    output_data = {
        "_meta": {
            "source_implications": str(impl_file),
            "source_groups": str(groups_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "description": "Market-level covering candidates (all combinations)",
        },
        "market_candidates": propagated,
    }

    output_file = output_folder / "market_candidates.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    summary = {
        "script": "05a_expand_to_markets",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 2),
        "input": {
            "implications_file": str(impl_file),
            "groups_file": str(groups_file),
        },
        "stats": stats,
        "output": str(output_folder),
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print results
    logger.info("\n" + "=" * 70)
    logger.info("PROPAGATION RESULTS")
    logger.info("=" * 70)
    logger.info(f"  Target groups: {stats['total_target_groups']}")
    logger.info(f"  Target markets: {stats['total_target_markets']}")
    logger.info(f"  YES covering candidates: {stats['total_yes_candidates']}")
    logger.info(f"  NO covering candidates: {stats['total_no_candidates']}")
    logger.info(f"  Markets with YES covers: {stats['markets_with_yes_covers']}")
    logger.info(f"  Markets with NO covers: {stats['markets_with_no_covers']}")
    logger.info(f"  Markets with BOTH covers: {stats['markets_with_both_covers']}")
    logger.info("=" * 70)
    logger.info(f"Output: {output_file}")


if __name__ == "__main__":
    main()
