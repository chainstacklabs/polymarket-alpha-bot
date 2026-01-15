"""
03b: Derive Cover Positions from Raw Implications.

WHAT IT DOES
    Converts abstract "A implies B" relationships into concrete hedge
    positions. For each implication, creates BOTH direct and contrapositive
    covers to maximize hedging opportunities.

WHY WE NEED THIS
    The LLM found relationships like "election held → election called".
    But to actually trade, we need to know: "If I bet X, what covers me?"
    This step translates logical relationships into trading positions.

HOW IT WORKS
    For implication "A → B" (A implies B), derive TWO covers:

    1. DIRECT COVER (hedge A_NO position):
       - Target: A = NO (betting A won't happen)
       - Cover:  B = YES (if A happens, B definitely happens)
       - Logic: If A_NO loses, B_YES pays

    2. CONTRAPOSITIVE COVER (hedge B_YES position):
       - Target: B = YES (betting B happens)
       - Cover:  A = NO (if B doesn't happen, A can't happen)
       - Logic: If A→B, then NOT-B→NOT-A (contrapositive)
       - If B_YES loses, A_NO pays

    Contrapositive = logical flip. "Rain → wet ground" means "dry ground → no rain"

CONFIDENCE MAPPING
    LLM confidence → probability that cover fires when needed:
    - "necessary": 0.98 (logical/geographic certainty)
    - "strong":    0.85 (very likely but not guaranteed)
    - "inverse":   0.70 (correlation, not causation)

PIPELINE
    03a_extract_implications → [03b_derive_covers] → 04_filter_implications

INPUT
    data/03a_extract_implications/<timestamp>/
        - raw_implications.json: LLM relationships per group
    data/02_build_groups/<timestamp>/
        - groups.json: For title → group_id matching

OUTPUT
    data/03b_derive_covers/<timestamp>/
        - implications.json : Groups with YES_covers and NO_covers arrays
        - summary.json      : Run stats (cover counts)

RUNTIME
    <5 seconds (pure data transformation)

CONFIGURATION
    - PROBABILITY_MAP: Confidence level → probability mapping
    - IMPLIES_MULTIPLIER: Downgrade "implies" direction (0.90)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_IMPL_DIR = DATA_DIR / "03a_extract_implications"
GROUPS_DIR = DATA_DIR / "02_build_groups"
SCRIPT_OUTPUT_DIR = DATA_DIR / "03b_derive_covers"

INPUT_RUN_FOLDER: str | None = None

# Probability mapping by confidence level
PROBABILITY_MAP = {
    "necessary": 0.98,
    "strong": 0.85,
    "inverse": 0.70,
}

# Downgrade "implies" direction (LLMs often confuse correlation with implication)
IMPLIES_MULTIPLIER = 0.90

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# TITLE MATCHING
# =============================================================================


def match_title_to_group(
    title: str,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
) -> dict | None:
    """Match LLM output title to actual group."""
    # Exact match
    if title in groups_by_title:
        return groups_by_title[title]

    # Case-insensitive match
    title_lower = title.lower().strip()
    if title_lower in groups_by_title_lower:
        return groups_by_title_lower[title_lower]

    # Fuzzy match - substring
    for group_title, group in groups_by_title.items():
        if title_lower in group_title.lower() or group_title.lower() in title_lower:
            return group

    return None


# =============================================================================
# VALIDATION
# =============================================================================


def find_bidirectional_pairs(results: list[dict]) -> set[tuple[str, str]]:
    """
    Find groups with bidirectional implied_by (A←B AND B←A).

    This is usually wrong - at most one direction is logically necessary.
    """
    # Build map: group_title -> set of titles it claims implied_by
    implied_by_map: dict[str, set[str]] = {}
    for r in results:
        titles = {item.get("group_title", "") for item in r.get("implied_by", [])}
        implied_by_map[r["title"]] = titles

    # Find pairs where both directions claim implied_by
    pairs = set()
    for a_title, a_implied_by in implied_by_map.items():
        for b_title in a_implied_by:
            if a_title in implied_by_map.get(b_title, set()):
                pairs.add(tuple(sorted([a_title, b_title])))

    return pairs


# =============================================================================
# COVER DERIVATION
# =============================================================================


def derive_covers(
    raw_impl: dict,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
    bidirectional_pairs: set[tuple[str, str]],
) -> dict:
    """
    Derive covers from raw implications using contrapositive logic.

    For target event T:
    - "implies" (T → other): other_YES covers T_NO
    - "implied_by" (other → T): other_NO covers T_YES (contrapositive)
    """
    target_id = raw_impl["group_id"]
    target_title = raw_impl["title"]

    yes_covered_by = []  # Covers for target_YES (fire when target=NO)
    no_covered_by = []  # Covers for target_NO (fire when target=YES)

    # Check if target is in any bidirectional pair
    target_bidirectional = {
        pair for pair in bidirectional_pairs if target_title in pair
    }

    # Process "implied_by": other → target (contrapositive gives YES cover)
    for item in raw_impl.get("implied_by", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        # Skip if this is part of a bidirectional pair (keep only first alphabetically)
        pair = tuple(sorted([target_title, other_title]))
        if pair in target_bidirectional and target_title > other_title:
            logger.info(
                f"  SKIP bidirectional: {target_title[:30]} ← {other_title[:30]}"
            )
            continue

        confidence = item.get("confidence", "strong")
        prob = PROBABILITY_MAP.get(confidence, 0.85)

        yes_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "NO",
                "relationship": f"other→target (contrapositive): {item.get('explanation', '')}",
                "relationship_type": confidence,
                "probability": prob,
                "counterexample_check": item.get("counterexample_check", ""),
            }
        )

    # Process "implies": target → other (direct gives NO cover)
    for item in raw_impl.get("implies", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        confidence = item.get("confidence", "strong")
        base_prob = PROBABILITY_MAP.get(confidence, 0.85)
        prob = round(base_prob * IMPLIES_MULTIPLIER, 4)  # Downgrade "implies"

        no_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "YES",
                "relationship": f"target→other: {item.get('explanation', '')}",
                "relationship_type": confidence,
                "probability": prob,
                "counterexample_check": item.get("counterexample_check", ""),
            }
        )

    # Process "inverse": negatively correlated
    inverse_prob = PROBABILITY_MAP["inverse"]
    for item in raw_impl.get("inverse", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        yes_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "YES",
                "relationship": f"inverse: {item.get('explanation', '')}",
                "relationship_type": "inverse",
                "probability": inverse_prob,
            }
        )
        no_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "NO",
                "relationship": f"inverse: {item.get('explanation', '')}",
                "relationship_type": "inverse",
                "probability": inverse_prob,
            }
        )

    return {
        "group_id": target_id,
        "title": target_title,
        "yes_covered_by": yes_covered_by,
        "no_covered_by": no_covered_by,
        "raw_counts": {
            "implies": len(raw_impl.get("implies", [])),
            "implied_by": len(raw_impl.get("implied_by", [])),
            "inverse": len(raw_impl.get("inverse", [])),
        },
    }


# =============================================================================
# MAIN
# =============================================================================


def get_latest_folder(base_dir: Path) -> Path | None:
    """Get latest run folder."""
    if not base_dir.exists():
        return None
    folders = [f for f in base_dir.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.stat().st_mtime) if folders else None


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("STEP 03b: Derive Covers (contrapositive logic)")
    logger.info("=" * 70)

    # Load raw implications
    raw_folder = (
        RAW_IMPL_DIR / INPUT_RUN_FOLDER
        if INPUT_RUN_FOLDER
        else get_latest_folder(RAW_IMPL_DIR)
    )
    if not raw_folder:
        logger.error("No raw implications folder found")
        return

    raw_file = raw_folder / "raw_implications.json"
    if not raw_file.exists():
        logger.error(f"File not found: {raw_file}")
        return

    logger.info(f"Loading raw implications: {raw_file}")
    with open(raw_file, encoding="utf-8") as f:
        raw_data = json.load(f)

    raw_implications = raw_data.get("raw_implications", [])
    logger.info(f"Loaded {len(raw_implications)} raw implications")

    # Load groups for matching
    run_timestamp = raw_folder.name
    groups_file = GROUPS_DIR / run_timestamp / "groups.json"

    if not groups_file.exists():
        logger.error(f"Groups file not found: {groups_file}")
        return

    logger.info(f"Loading groups: {groups_file}")
    with open(groups_file, encoding="utf-8") as f:
        groups_data = json.load(f)

    groups = groups_data.get("groups", [])
    groups_by_title = {g["title"]: g for g in groups}
    groups_by_title_lower = {g["title"].lower().strip(): g for g in groups}
    logger.info(f"Loaded {len(groups)} groups for matching")

    # Find bidirectional pairs for validation
    bidirectional_pairs = find_bidirectional_pairs(raw_implications)
    if bidirectional_pairs:
        logger.warning(
            f"Found {len(bidirectional_pairs)} bidirectional pairs (will fix)"
        )
        for pair in bidirectional_pairs:
            logger.warning(f"  {pair[0][:40]} <-> {pair[1][:40]}")

    # Derive covers
    logger.info("\nDeriving covers...")
    results = []
    for raw_impl in raw_implications:
        derived = derive_covers(
            raw_impl, groups_by_title, groups_by_title_lower, bidirectional_pairs
        )
        results.append(derived)

        # Log progress
        yes_count = len(derived["yes_covered_by"])
        no_count = len(derived["no_covered_by"])
        if yes_count or no_count:
            logger.info(
                f"  {derived['title'][:40]}: {yes_count} YES, {no_count} NO covers"
            )

    # Save results
    output_folder = SCRIPT_OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    output_data = {
        "_meta": {
            "source_raw": str(raw_file),
            "source_groups": str(groups_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "description": "Covers derived via contrapositive logic",
            "derivation_rules": {
                "implies (T→O)": "T_NO loses → O_YES wins (cover for T_NO)",
                "implied_by (O→T)": "T_YES loses → O_NO wins (contrapositive cover for T_YES)",
            },
            "probability_map": PROBABILITY_MAP,
            "implies_multiplier": IMPLIES_MULTIPLIER,
            "bidirectional_pairs_fixed": len(bidirectional_pairs),
        },
        "implications": results,
    }

    output_file = output_folder / "implications.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary stats
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    total_yes = sum(len(r["yes_covered_by"]) for r in results)
    total_no = sum(len(r["no_covered_by"]) for r in results)
    groups_with_covers = sum(
        1 for r in results if r["yes_covered_by"] or r["no_covered_by"]
    )

    summary = {
        "script": "03b_derive_covers",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 2),
        "input": {
            "raw_implications": str(raw_file),
            "groups_file": str(groups_file),
        },
        "validation": {
            "bidirectional_pairs_fixed": len(bidirectional_pairs),
        },
        "results": {
            "groups_processed": len(results),
            "groups_with_covers": groups_with_covers,
            "total_yes_covers": total_yes,
            "total_no_covers": total_no,
        },
        "output": str(output_folder),
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("\n" + "=" * 70)
    logger.info(f"Groups processed: {len(results)}")
    logger.info(f"Groups with covers: {groups_with_covers}")
    logger.info(f"Total YES covers: {total_yes}")
    logger.info(f"Total NO covers: {total_no}")
    logger.info(f"Bidirectional pairs fixed: {len(bidirectional_pairs)}")
    logger.info(f"Duration: {duration:.2f}s")
    logger.info(f"Output: {output_file}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
