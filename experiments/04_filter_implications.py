"""
04: Filter Low-Confidence Implications.

WHAT IT DOES
    Removes hedge relationships that are too uncertain for reliable trading.
    Default threshold: keep only covers with ≥85% probability of firing.

WHY WE NEED THIS
    Step 03b created covers with varying confidence levels. A hedge that
    only works 70% of the time is dangerous - you'd lose 30% of the time
    even with perfect market reads. We want near-certain relationships.

    Risk math: If you lose $1 when hedge fails and gain $0.10 when it works,
    70% success rate = 0.70 × $0.10 - 0.30 × $1.00 = -$0.23 per trade (loss!)

HOW IT WORKS
    1. Load implications with probability scores from 03b
    2. Filter out any cover where P(cover fires | target loses) < threshold
    3. Remove groups that have no remaining covers
    4. Typically removes ~95% of relationships (inverse correlations)

WHAT GETS FILTERED
    - KEPT:    "necessary" (98%) and "strong" (85%) - logical relationships
    - REMOVED: "inverse" (70%) - mere correlations without causal link

    Example:
    - KEPT:    "Region captured → City captured" (geographic necessity)
    - REMOVED: "War escalates → Oil up" (correlation, many exceptions)

PIPELINE
    03b_derive_covers → [04_filter_implications] → 05a_expand_to_markets

INPUT
    data/03b_derive_covers/<timestamp>/
        - implications.json: Groups with YES/NO covers and probabilities

OUTPUT
    data/04_filter_implications/<timestamp>/
        - filtered_implications.json : High-confidence covers only
        - summary.json               : Before/after counts

RUNTIME
    <1 second (simple filtering)

CONFIGURATION
    - MIN_PROBABILITY: Minimum P(cover fires) to keep (default: 0.85)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "03b_derive_covers"
OUTPUT_DIR = DATA_DIR / "04_filter_implications"

INPUT_RUN_FOLDER: str | None = None

# Filtering threshold - P(cover_fires | target_outcome)
MIN_PROBABILITY = 0.85

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# FILTERING
# =============================================================================


def get_probability(cov: dict) -> float:
    """Get probability from cover dict (supports both new and old field names)."""
    return cov.get("probability") or cov.get("confidence", 0)


def filter_implications(implications: list[dict], min_prob: float) -> list[dict]:
    """Filter implications to keep only high-probability covering relationships."""
    return [
        {
            "group_id": impl["group_id"],
            "title": impl["title"],
            "yes_covered_by": [
                c
                for c in impl.get("yes_covered_by", [])
                if get_probability(c) >= min_prob
            ],
            "no_covered_by": [
                c
                for c in impl.get("no_covered_by", [])
                if get_probability(c) >= min_prob
            ],
        }
        for impl in implications
    ]


def compute_stats(original: list[dict], filtered: list[dict]) -> dict:
    """Compute before/after statistics."""
    orig_yes = sum(len(i.get("yes_covered_by", [])) for i in original)
    orig_no = sum(len(i.get("no_covered_by", [])) for i in original)
    orig_total = orig_yes + orig_no

    filt_yes = sum(len(i.get("yes_covered_by", [])) for i in filtered)
    filt_no = sum(len(i.get("no_covered_by", [])) for i in filtered)
    filt_total = filt_yes + filt_no

    groups_with_yes_before = sum(1 for i in original if i.get("yes_covered_by"))
    groups_with_no_before = sum(1 for i in original if i.get("no_covered_by"))
    groups_with_yes_after = sum(1 for i in filtered if i.get("yes_covered_by"))
    groups_with_no_after = sum(1 for i in filtered if i.get("no_covered_by"))

    return {
        "before": {
            "total_relationships": orig_total,
            "yes_relationships": orig_yes,
            "no_relationships": orig_no,
            "groups_with_yes": groups_with_yes_before,
            "groups_with_no": groups_with_no_before,
        },
        "after": {
            "total_relationships": filt_total,
            "yes_relationships": filt_yes,
            "no_relationships": filt_no,
            "groups_with_yes": groups_with_yes_after,
            "groups_with_no": groups_with_no_after,
        },
        "removed": {
            "total_relationships": orig_total - filt_total,
            "yes_relationships": orig_yes - filt_yes,
            "no_relationships": orig_no - filt_no,
            "retention_rate": round(filt_total / orig_total, 3) if orig_total else 0,
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
    logger.info("STEP 04: Filter Low-Confidence Relationships")
    logger.info(f"Minimum confidence: {MIN_PROBABILITY:.0%}")
    logger.info("=" * 70)

    # Load input
    input_folder = (
        INPUT_DIR / INPUT_RUN_FOLDER
        if INPUT_RUN_FOLDER
        else get_latest_folder(INPUT_DIR)
    )
    if not input_folder:
        logger.error("No input folder found")
        return

    impl_file = input_folder / "implications.json"
    if not impl_file.exists():
        logger.error(f"File not found: {impl_file}")
        return

    logger.info(f"Loading from: {impl_file}")
    with open(impl_file, encoding="utf-8") as f:
        data = json.load(f)

    original = data.get("implications", [])
    logger.info(f"Loaded {len(original)} groups")

    # Filter
    logger.info(f"\nFiltering with min_probability={MIN_PROBABILITY}...")
    filtered = filter_implications(original, MIN_PROBABILITY)

    # Compute stats
    stats = compute_stats(original, filtered)

    # Prepare output
    run_timestamp = input_folder.name
    output_folder = OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    output_data = {
        "_meta": {
            "source": str(impl_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "min_probability": MIN_PROBABILITY,
            "description": "Filtered implications (low probability covers removed)",
        },
        "implications": filtered,
    }

    output_file = output_folder / "filtered_implications.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    summary = {
        "script": "04_filter_implications",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 2),
        "config": {
            "min_probability": MIN_PROBABILITY,
        },
        "input": str(impl_file),
        "stats": stats,
        "output": str(output_folder),
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print results
    logger.info("\n" + "=" * 70)
    logger.info("FILTERING RESULTS")
    logger.info("=" * 70)
    logger.info("  BEFORE:")
    logger.info(f"    Total relationships: {stats['before']['total_relationships']}")
    logger.info(f"    YES: {stats['before']['yes_relationships']}")
    logger.info(f"    NO:  {stats['before']['no_relationships']}")
    logger.info(f"    Groups with YES: {stats['before']['groups_with_yes']}")
    logger.info(f"    Groups with NO:  {stats['before']['groups_with_no']}")
    logger.info("  AFTER:")
    logger.info(f"    Total relationships: {stats['after']['total_relationships']}")
    logger.info(f"    YES: {stats['after']['yes_relationships']}")
    logger.info(f"    NO:  {stats['after']['no_relationships']}")
    logger.info(f"    Groups with YES: {stats['after']['groups_with_yes']}")
    logger.info(f"    Groups with NO:  {stats['after']['groups_with_no']}")
    logger.info("  REMOVED:")
    logger.info(f"    Relationships: {stats['removed']['total_relationships']}")
    logger.info(f"    Retention rate: {stats['removed']['retention_rate']:.1%}")
    logger.info("=" * 70)
    logger.info(f"Output: {output_file}")


if __name__ == "__main__":
    main()
